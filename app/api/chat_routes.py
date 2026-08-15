"""会话管理 & 辅助对话 API 路由 —— 集成分层记忆系统

记忆架构：
  HierarchicalMemory
    ├── short_term: RedisStore (TTL 1h，热窗口)
    └── long_term:  MySQLStore (生产落库) / 连接失败时自动降级 SqliteStore

压缩策略：
  1. 保留最近 N 条完整消息
  2. 更早的历史 → 摘要 → MemoryBrief
  3. 总长度受 max_tokens 限制

归档策略：
  chat_archive_days > 0 时，每次对话结束后把 updated_at 超过该天数的记录
  从热表 chat_kv 移入归档表 chat_kv_archive，主表保持轻量。
"""


import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.llm import DeepSeekClient, LLMConfig
from app.memory import (
    HierarchicalMemory,
    SqliteStore,
    RedisStore,
    MySQLStore,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])

# 短期记忆用 Redis（低延迟热窗口）
_short_term = RedisStore()

# 长期记忆接 MySQL（生产落库），连接/驱动不可用时自动降级回 SQLite，保证服务可用
if settings.long_term_backend.lower() == "mysql":
    try:
        _long_term: SqliteStore = MySQLStore(dsn=settings.mysql_dsn)
        logger.info("长期记忆后端：MySQL (%s)", settings.mysql_dsn)
    except Exception as e:  # 解析/导入阶段失败 → 降级
        logger.warning("MySQL 长期记忆不可用，降级到 SQLite：%s", e)
        _long_term = SqliteStore()
else:
    _long_term = SqliteStore()
    logger.info("长期记忆后端：SQLite")

memory = HierarchicalMemory(short_term=_short_term, long_term=_long_term)

# DeepSeek 客户端（智能对话 LLM，懒初始化）
_chat_llm: Optional[DeepSeekClient] = None


def _get_chat_llm() -> Optional[DeepSeekClient]:
    global _chat_llm
    if _chat_llm is None:
        if not settings.deepseek_api_key:
            logger.warning("DEEPSEEK_API_KEY 未配置，智能对话不可用")
            return None
        _chat_llm = DeepSeekClient(
            LLMConfig(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_chat_model,
                max_tokens=4096,  # 推理模型会先消耗 token，调高避免回答被截断
            )
        )
    return _chat_llm


SYSTEM_PROMPT = (
    "你是一位专业的运维（AIOps）助手，擅长故障排查、日志分析和告警处置。"
    "请用简洁、专业的中文回答。回答可以分点列出，便于阅读。"
)

class AskRequest(BaseModel):
    session_id: str
    query: str
    alert_context: Optional[dict] = None


class SessionCreateResponse(BaseModel):
    id: str
    title: str


class SessionListItem(BaseModel):
    id: str
    title: str
    create_time: datetime


class MessageItem(BaseModel):
    role: str
    content: str
    timestamp: str = ""


async def _maybe_archive() -> None:
    """按配置归档过期会话（days<=0 自动跳过）；失败仅告警不影响主流程"""
    if settings.chat_archive_days > 0:
        try:
            await _long_term.archive_old_sessions(settings.chat_archive_days)
        except Exception as e:
            logger.warning("归档历史会话失败（不影响本次请求）：%s", e)


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session():
    """创建新会话"""
    import uuid
    sid = str(uuid.uuid4())
    await _long_term.set(
        f"session:{sid}",
        {"id": sid, "title": "新会话", "create_time": datetime.now().isoformat()},
    )
    # 创建会话时顺手清理一次过期会话（覆盖“只建不对话”的场景）
    await _maybe_archive()
    return SessionCreateResponse(id=sid, title="新会话")


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions():
    """获取会话列表"""
    sessions = await _long_term.list_sessions()
    return [
        SessionListItem(
            id=s["id"],
            title=s["title"],
            create_time=s["create_time"],
        )
        for s in sorted(
            sessions,
            key=lambda x: x["create_time"],
            reverse=True,
        )
    ]


@router.delete("/sessions/{session_id}", response_model=dict)
async def delete_session(session_id: str):
    """删除会话：同时清理会话元数据与对应记忆（Redis + 长期存储）"""
    # 1. 删除对话记忆（分层记忆会清 Redis 热窗口与 long_term 回填）
    await memory.clear(session_id)
    # 2. 删除会话元数据
    await _long_term.delete(f"session:{session_id}")
    return {"deleted": session_id}


@router.get("/sessions/{session_id}/messages", response_model=list[MessageItem])
async def get_messages(session_id: str):
    """获取会话消息（从分层记忆中读取）"""
    brief = await memory.get_memory_brief(session_id)
    messages = brief.recent_messages
    if brief.summary:
        # 在消息列表前插入摘要标记
        messages = [
            {"role": "system", "content": f"[历史摘要]: {brief.summary}", "timestamp": ""}
        ] + messages
    return [MessageItem(**m) for m in messages]


def _build_messages(brief, alert_context: Optional[dict]) -> list[dict]:
    """构造发给 Kimi 的 messages（含历史摘要 / 最近消息 / 告警上下文）"""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if brief.summary:
        messages.append({"role": "system", "content": f"[历史对话摘要]: {brief.summary}"})
    if alert_context:
        messages.append({
            "role": "system",
            "content": "当前正在查看/处理以下告警，回答请结合该告警信息：\n"
            + json.dumps(alert_context, ensure_ascii=False, indent=2),
        })
    # recent_messages 已包含刚加入的当前问题（最后一条）；记忆内 AI 角色为 "ai"，需映射为 OpenAI 协议的 "assistant"
    for m in brief.recent_messages:
        role = "assistant" if m["role"] == "ai" else m["role"]
        messages.append({"role": role, "content": m["content"]})
    return messages


async def _stream_chat(messages: list[dict]):
    """流式调用对话 LLM，逐块产出文本；失败时输出提示并抛出异常（便于调用方不入库）"""
    llm = _get_chat_llm()
    if llm is None:
        yield (
            "\n\n[提示] 尚未配置 DEEPSEEK_API_KEY，请在 .env 中填写后重启服务。\n"
            "当前仍可正常使用告警分析等其他功能。"
        )
        return
    try:
        async for delta in llm.chat_stream(messages):
            yield delta
    except Exception as e:
        logger.error("Chat LLM stream failed: %s", e)
        yield f"\n\n[错误] 调用 LLM API 失败：{e}"
        raise


@router.post("/chat/ask")
async def ask(request: AskRequest):
    """辅助对话接口（SSE 流式），接入分层记忆"""
    session_id = request.session_id

    session_key = f"session:{session_id}"

    # 自动创建会话
    if not await _long_term.exists(session_key):
        await _long_term.set(session_key, {
            "id": session_id,
            "title": "新会话",
            "create_time": datetime.now().isoformat(),
        })

    # 1. 保存用户消息到记忆
    await memory.add_message(session_id, "user", request.query)

    # 2. 获取记忆摘要（含压缩后的历史上下文）
    brief = await memory.get_memory_brief(session_id)

    # 3. 自动更新标题
    session = await _long_term.get(session_key)
    if session and session["title"] == "新会话":
        session["title"] = request.query[:15] + (
            "..." if len(request.query) > 15 else ""
        )
        await _long_term.set(session_key, session)

    # 4. 构造发给 LLM 的 messages
    messages = _build_messages(brief, request.alert_context)

    async def generate():
        collected: list[str] = []
        try:
            async for chunk in _stream_chat(messages):
                collected.append(chunk)
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
        except Exception:
            collected = []  # 出错时错误提示已发给前端，但不入库污染上下文
        # 5. 保存 AI 回复到记忆
        response_text = "".join(collected).strip()
        if response_text:
            await memory.add_message(session_id, "ai", response_text)
            # 6. 可选归档：把超过保留期的历史会话移入归档表
            await _maybe_archive()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁止反向代理（nginx 等）缓冲
            "Connection": "keep-alive",
        },
    )
