"""分层记忆管理器

架构：
  HierarchicalMemory
    ├── short_term: RedisStore（TTL 短期，低延迟优先）
    └── long_term:  MySQLStore / SqliteStore（Redis 缺失时回填）

读写策略：
  - 读：优先 short_term（Redis）；未命中则从 long_term 回填并写回 Redis
  - 写：双写 short_term（带 TTL）与 long_term（持久化）
  - 任一后端异常自动降级，不影响主流程

压缩策略：
  1. 保留最近 N 条完整消息 (memory_recent_count)
  2. 更早的历史 → 语义压缩 → MemoryBrief
     - 开启 memory_use_llm_compress 时，调用 LLM（DeepSeek/Kimi）将旧历史
       滚动压缩为要点摘要（保留诉求/已排查步骤/待办/关键实体）
     - LLM 不可用或关闭时，降级为规则拼接（窗口裁剪 + 截断）
  3. 总长度受 max_tokens 限制
"""


import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.config import settings
from app.memory.stores import (
    InMemoryStore,
    MemoryStore,
    MySQLStore,
    RedisStore,
    SqliteStore,
)

logger = logging.getLogger(__name__)


@dataclass
class MemoryBrief:
    """压缩后的记忆摘要"""
    summary: str = ""
    recent_messages: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    message_count: int = 0


class HierarchicalMemory:
    """分层记忆管理器：Redis 短期优先 + 后端长期回填"""

    def __init__(
        self,
        short_term: Optional[MemoryStore] = None,
        long_term: Optional[MemoryStore] = None,
        store: Optional[MemoryStore] = None,
    ):
        # 兼容旧调用：HierarchicalMemory(store=...) 等价于双端同后端
        if store is not None:
            short_term = short_term or store
            long_term = long_term or store
        # short_term 缺省为 Redis；long_term 缺省为 SQLite（开发）/ MySQL（生产）
        self.short_term = short_term or RedisStore()
        self.long_term = long_term or SqliteStore()
        self.max_tokens = settings.memory_max_tokens
        self.recent_count = settings.memory_recent_count
        self.redis_ttl = settings.redis_ttl_seconds

    async def _read(self, key: str) -> Optional[dict]:
        """优先 Redis，未命中从 long_term 回填并写回 Redis"""
        data = await self.short_term.get(key)
        if data is not None:
            return data
        # Redis 缺失 → 回填
        data = await self.long_term.get(key)
        if data is not None:
            await self.short_term.set(key, data, ttl=self.redis_ttl)
            logger.debug("Memory 回填: %s (Redis 未命中，从 long_term 读取)", key)
        return data

    async def _write(self, key: str, value: dict) -> None:
        """双写 short_term + long_term"""
        await self.short_term.set(key, value, ttl=self.redis_ttl)
        await self.long_term.set(key, value)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """向会话中添加一条消息"""
        key = f"memory:{session_id}"
        brief = await self._read(key) or MemoryBrief().__dict__

        recent = brief.get("recent_messages", [])
        recent.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        brief["recent_messages"] = recent
        brief["message_count"] = brief.get("message_count", 0) + 1

        # 超过 recent_count 则触发压缩
        if len(recent) > self.recent_count * 2:
            brief = await self._compress(brief)

        await self._write(key, brief)

    async def get_memory_brief(
        self,
        session_id: str,
    ) -> MemoryBrief:
        """获取压缩后的记忆摘要"""
        key = f"memory:{session_id}"
        data = await self._read(key)
        if data:
            return MemoryBrief(**data)
        return MemoryBrief()

    COMPRESS_PROMPT = (
        "你是智能运维对话压缩器。请把以下历史对话压缩为一份简洁的要点摘要，"
        "必须保留：1) 用户的核心诉求；2) 已排查/已执行的步骤与结论；"
        "3) 尚未解决的问题与待办项；4) 关键实体（IP、服务名、告警ID、错误码）。"
        "用中文分点输出，不要遗漏任何待办项，不要编造未提及的信息。\n\n"
        "历史对话：\n{history}"
    )

    async def _compress(self, brief: dict) -> dict:
        """窗口裁剪 + 语义压缩。

        最近 N 条完整保留；更早的历史：
        - 开启 memory_use_llm_compress 时调用 LLM 滚动压缩（见 _llm_compress）
        - 否则 / LLM 不可用时降级为规则拼接（见 _rule_compress）
        """
        recent = brief.get("recent_messages", [])
        keep = recent[-self.recent_count:]          # 最近 N 条完整保留
        old = recent[:-self.recent_count]           # 待压缩的旧历史

        prev_summary = brief.get("summary", "")
        if not old:
            return {
                "summary": prev_summary,
                "recent_messages": keep,
                "message_count": brief.get("message_count", 0),
            }

        if settings.memory_use_llm_compress:
            summary = await self._llm_compress(old, prev_summary)
        else:
            summary = self._rule_compress(old, prev_summary)

        return {
            "summary": summary,
            "recent_messages": keep,
            "message_count": brief.get("message_count", 0),
        }

    def _rule_compress(self, old: list, prev_summary: str) -> str:
        """规则拼接降级：每条旧消息取前 100 字，整体截断 2000 字符。"""
        parts = [f"[{m['role']}]: {m['content'][:100]}" for m in old]
        summary = "\n".join(parts) if parts else prev_summary
        if prev_summary:
            summary = prev_summary + "\n" + summary
        return summary[-2000:]  # 截断防止过长

    async def _llm_compress(self, old: list, prev_summary: str) -> str:
        """LLM 滚动语义压缩：把旧历史 + 上一轮摘要压成新的要点摘要。

        滚动摘要（rolling summary）保证跨多轮压缩时早期语义不流失；
        LLM 调用失败则降级为规则拼接，保证主流程可用。
        """
        history = "\n".join(f"[{m['role']}]: {m['content']}" for m in old)
        # 喂给 LLM 的历史也做长度上限保护，避免撑爆压缩请求本身
        budget = self.max_tokens * 2
        if len(history) > budget:
            history = history[-budget:]

        try:
            provider = settings.memory_compress_provider
            if provider == "kimi":
                from app.llm.kimi import KimiClient
                client = KimiClient()
            else:
                from app.llm.deepseek import DeepSeekClient
                client = DeepSeekClient()
            raw = await client.chat([
                {"role": "system", "content": self.COMPRESS_PROMPT.format(history=history)},
            ])
            # 滚动：新摘要 = 旧摘要 + 本轮压缩结果，再截断到预算内
            new_summary = (prev_summary + "\n" + raw).strip() if prev_summary else raw
            return new_summary[-self.max_tokens * 4:]
        except Exception as e:  # noqa: BLE001
            logger.warning("memory LLM compress failed, fallback to rule: %s", e)
            return self._rule_compress(old, prev_summary)
        finally:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def clear(self, session_id: str) -> None:
        key = f"memory:{session_id}"
        await self.short_term.delete(key)
        await self.long_term.delete(key)
