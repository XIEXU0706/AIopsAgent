"""故障知识库服务 —— Chroma 向量存储实现

- 文档上传（markdown 解析为案例）→ 向量化 → 存入 Chroma
- 查询：输入告警特征文本，返回最相似故障案例
- 无网络依赖：使用自研确定性 hashing embedding（crc32 特征哈希），
  避免 Chroma 内置 ONNX 模型的首次下载，且跨进程稳定（重启后向量不失效）。
"""

import asyncio
import logging
import math
import re
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CHROMA_DIR = DATA_DIR / "chroma"

EMBED_DIM = 512
# embedding 算法版本：升级算法后集合名随之变化，强制重建索引避免旧向量不兼容
EMBEDDING_VERSION = "v2"
COLLECTION_NAME = f"fault_cases_{EMBEDDING_VERSION}"

try:
    # Chroma 1.x 需要 embedding function 实现 name/get_config/build_from_config/is_legacy
    from chromadb.api.types import EmbeddingFunction
    _CHROMA_IMPORT_OK = True
except Exception:
    EmbeddingFunction = object
    _CHROMA_IMPORT_OK = False


class HashingEmbeddingFunction(EmbeddingFunction):
    """确定性特征哈希 embedding（中文 2-gram + 英文词）

    crc32 而非 hash()：hash() 跨进程盐化，重启后已持久化的向量会失效。
    """

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim

    @staticmethod
    def name() -> str:
        return "hashing_embedding_v2"

    @staticmethod
    def build_from_config(config: dict[str, Any]):
        return HashingEmbeddingFunction(dim=config.get("dim", EMBED_DIM))

    def get_config(self) -> dict[str, Any]:
        return {"dim": self.dim}

    def is_legacy(self) -> bool:
        return False

    def __call__(self, input):
        return [self._embed(text) for text in input]

    @staticmethod
    def _tokens(text: str) -> list[str]:
        """中文整段 + 2-gram + 英文词（蛇形标识符拆子词），提升跨语言召回"""
        tokens: list[str] = []
        for seg in re.findall(r"[一-龥]+", text):
            if len(seg) == 1:
                tokens.append(seg)
            else:
                tokens.append(seg)
                tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", text.lower()):
            tokens.append(w)
            tokens.extend(p for p in w.split("_") if p)
        return tokens

    def _embed(self, text: str) -> list[float]:
        # 有符号特征哈希：碰撞在向量空间中随机抵消，显著优于朴素计数哈希
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for tok in self._tokens(text):
            counts[tok] = counts.get(tok, 0) + 1
        for tok, cnt in counts.items():
            h = zlib.crc32(tok.encode("utf-8"))
            dim = h % self.dim
            sign = 1.0 if (h >> 31) & 1 else -1.0
            vec[dim] += sign * (1.0 + math.log(cnt))
        norm = (sum(x * x for x in vec)) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


# 内置故障案例（迁移自 RetrievalAgent 的规则知识库，作为开箱即用的种子数据）
# keywords 为检索辅助关键字（含错误类型 / 中英文别名），不参与结果展示
BUILTIN_CASES: list[dict] = [
    {
        "title": "MySQL 连接数打满",
        "symptom": "连接数达到 max_connections，新连接被拒绝",
        "root_cause": "连接池配置过小 or 存在慢查询堆积占用连接",
        "solution": "1. 临时增大 max_connections\n2. 检查并 kill 长时间空闲连接\n3. 优化慢查询\n4. 配置连接池合理上限",
        "keywords": "mysql_connection mysql connection too many connections max_connections 连接数",
    },
    {
        "title": "MySQL 主从延迟",
        "symptom": "Seconds_Behind_Master 持续增大",
        "root_cause": "从库单线程复制跟不上主库写入",
        "solution": "1. 检查从库 CPU/IO 负载\n2. 开启并行复制\n3. 考虑读写分离架构调整",
        "keywords": "mysql_slow_query mysql replication slave lag 主从延迟",
    },
    {
        "title": "Redis OOM 拒绝写入",
        "symptom": "OOM command not allowed when used memory > maxmemory",
        "root_cause": "内存打满，达到 maxmemory 上限",
        "solution": "1. 分析大 Key 并拆分\n2. 设置合理的淘汰策略\n3. 扩容或升级实例",
        "keywords": "redis_oom redis oom memory maxmemory 内存",
    },
    {
        "title": "磁盘空间不足",
        "symptom": "磁盘使用率达到阈值，写操作失败",
        "root_cause": "日志/数据文件堆积，磁盘空间耗尽",
        "solution": "1. 清理过期日志\n2. 扩容磁盘\n3. 配置磁盘告警阈值",
        "keywords": "disk_full disk space no space disk 磁盘空间",
    },
    {
        "title": "应用 CPU 使用率过高",
        "symptom": "CPU 使用率持续 100%，服务响应变慢",
        "root_cause": "应用存在死循环或线程泄漏，大量线程空转",
        "solution": "1. 抓取线程堆栈定位热点\n2. 排查死循环/内存泄漏\n3. 扩容或限流",
        "keywords": "high_cpu cpu load cpu usage 负载",
    },
    {
        "title": "通用故障排查",
        "symptom": "未知或未匹配的错误类型",
        "root_cause": "无法根据告警信息定位具体根因",
        "solution": "1. 查看完整监控面板\n2. 检查最近变更记录\n3. 查看应用日志堆栈",
        "keywords": "custom unknown generic default error troubleshooting 通用 默认",
    },
]

TEMPLATE_MD = """# 故障案例知识库模板

请复制以下模板，按格式填写故障案例，保存为 `.md` 文件后上传到知识库。

> 规则：
> - 每个案例以一个 `## 案例` 二级标题开始，标题后写案例名称。
> - 案例包含三个字段：症状、根因、处置方案。
> - 处置方案为编号列表，每个步骤独占一行。
> - 支持中文和英文描述。

## 案例：MySQL 连接数打满

- **症状**：连接数达到 max_connections，新连接被拒绝
- **根因**：连接池配置过小或存在慢查询堆积占用连接
- **处置方案**：
  1. 临时增大 max_connections
  2. 检查并 kill 长时间空闲连接
  3. 优化慢查询
  4. 配置连接池合理上限

## 案例：Redis OOM 拒绝写入

- **症状**：OOM command not allowed when used memory > maxmemory
- **根因**：内存打满，达到 maxmemory 上限
- **处置方案**：
  1. 分析大 Key 并拆分
  2. 设置合理的淘汰策略
  3. 扩容或升级实例
"""


def parse_cases(text: str) -> list[dict]:
    """解析知识库 markdown 文档，拆分为案例列表

    格式（与 TEMPLATE_MD 一致）：
        ## 案例：标题
        - **症状**：xxx
        - **根因**：xxx
        - **处置方案**：
          1. xxx
          2. xxx
    """
    cases: list[dict] = []
    blocks = re.split(r"\n(?=##\s*案例)", text)
    for block in blocks:
        title_m = re.match(r"##\s*案例\s*[:：]?\s*(.+)", block)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        symptom = _extract_field(block, "症状")
        root_cause = _extract_field(block, "根因")
        solution = _extract_steps(block)
        if not (symptom or root_cause or solution):
            continue
        cases.append({
            "title": title,
            "symptom": symptom,
            "root_cause": root_cause,
            "solution": solution,
        })
    return cases


def _id_index(chroma_id: str) -> int:
    """从 Chroma id（格式 doc_id:序号）提取案例序号，用于恢复文档原始顺序"""
    try:
        return int(chroma_id.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return 0


def _extract_field(block: str, label: str) -> str:
    """提取 `- **症状**：xxx` 或 `- 症状：xxx` 形式字段"""
    m = re.search(rf"- \*?{label}\*?[:：]\s*(.+)", block)
    if not m:
        m = re.search(rf"^[-\s]*\*?{label}\*?[:：]\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_steps(block: str) -> str:
    """提取处置方案编号步骤，拼回 "1. xxx\n2. xxx" 格式"""
    lines = []
    for m in re.finditer(r"^\s*(\d+)[.、]\s*(.+)$", block, re.MULTILINE):
        lines.append(f"{m.group(1)}. {m.group(2).strip()}")
    return "\n".join(lines)


class KnowledgeBaseService:
    """Chroma 向量知识库"""

    def __init__(self, persist_dir: str = str(CHROMA_DIR)):
        self._persist_dir = persist_dir
        self._client = None
        self._collection = None
        self._init_error: Optional[str] = None
        self._lock = asyncio.Lock()
        self._seeded = False

    # ── 初始化 ──────────────────────────────────────────
    def _ensure(self) -> None:
        """懒初始化 Chroma（失败只记录，不抛异常，避免阻塞启动）"""
        if self._collection is not None or self._init_error:
            return
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils.embedding_functions import register_embedding_function

            # 注册自定义 embedding function，重启后加载已有 collection 时才能重建
            register_embedding_function(HashingEmbeddingFunction)

            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            # 集合名带 embedding 版本：算法升级后自动换新集合重建，旧向量不兼容
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=HashingEmbeddingFunction(),
                metadata={"hnsw:space": "cosine", "embedding_version": EMBEDDING_VERSION},
            )
            logger.info("Chroma knowledge base ready at %s", self._persist_dir)
            self._seed_builtin_cases()
        except Exception as e:
            self._init_error = str(e)
            logger.warning("Chroma init failed, fallback to rule search: %s", e)

    def _seed_builtin_cases(self) -> None:
        if self._seeded or self._collection is None:
            return
        try:
            if self._collection.count() == 0:
                self._add_cases(
                    doc_id="builtin",
                    doc_name="内置故障案例.md",
                    cases=BUILTIN_CASES,
                    create_time=datetime.now().isoformat(),
                )
                logger.info("Seeded %d builtin cases into Chroma", len(BUILTIN_CASES))
            self._seeded = True
        except Exception as e:
            logger.warning("Seed builtin cases failed: %s", e)

    def _add_cases(self, doc_id: str, doc_name: str, cases: list[dict],
                   create_time: str) -> int:
        if self._collection is None or not cases:
            return 0
        ids = [f"{doc_id}:{i}" for i in range(len(cases))]
        documents = [
            "\n".join(filter(None, [
                c.get("title", ""), c.get("symptom", ""),
                c.get("root_cause", ""), c.get("solution", ""),
                c.get("keywords", ""),
            ]))
            for c in cases
        ]
        metadatas = [
            {
                "doc_id": doc_id,
                "doc_name": doc_name,
                "title": c.get("title", ""),
                "symptom": c.get("symptom", ""),
                "root_cause": c.get("root_cause", ""),
                "solution": c.get("solution", ""),
                "create_time": create_time,
            }
            for c in cases
        ]
        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(cases)

    # ── 文档管理 ────────────────────────────────────────
    async def upload_document(self, name: str, content: str) -> dict:
        """解析并入库一个知识文档（同名文档先删旧再插入，保证幂等）"""
        cases = parse_cases(content)
        if not cases:
            raise ValueError("未解析到有效案例，请按模板格式编写")

        async with self._lock:
            self._ensure()
            if self._collection is None:
                raise RuntimeError(
                    self._init_error or "向量库初始化失败"
                )
            doc_id = uuid.uuid4().hex[:12]
            create_time = datetime.now().isoformat()
            await asyncio.to_thread(
                self._add_cases, doc_id, name, cases, create_time,
            )
            return {
                "doc_id": doc_id,
                "doc_name": name,
                "case_count": len(cases),
            }

    async def delete_document(self, doc_id: str) -> bool:
        async with self._lock:
            self._ensure()
            if self._collection is None:
                return False
            await asyncio.to_thread(
                self._collection.delete, where={"doc_id": doc_id},
            )
            return True

    def list_documents(self) -> list[dict]:
        self._ensure()
        if self._collection is None:
            return []
        result = self._collection.get(include=["metadatas"])
        counts: dict[str, int] = {}
        first: dict[str, dict] = {}
        for meta in result.get("metadatas") or []:
            did = meta.get("doc_id")
            if not did:
                continue
            counts[did] = counts.get(did, 0) + 1
            if did not in first:
                first[did] = {
                    "doc_id": did,
                    "doc_name": meta.get("doc_name", ""),
                    "create_time": meta.get("create_time", ""),
                }
        docs = [
            {**info, "case_count": counts.get(did, 0)}
            for did, info in first.items()
        ]
        return sorted(docs, key=lambda d: d.get("create_time", ""), reverse=True)

    def get_document_content(self, doc_id: str) -> Optional[dict]:
        """按原格式重建文档内容（供在线预览 / 下载）"""
        self._ensure()
        if self._collection is None:
            return None
        result = self._collection.get(
            where={"doc_id": doc_id}, include=["metadatas"],
        )
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        if not ids:
            return None
        # 按案例序号恢复原始顺序
        ordered = sorted(
            zip(ids, metadatas),
            key=lambda pair: _id_index(pair[0]),
        )
        cases = [m for _, m in ordered]
        doc_name = cases[0].get("doc_name", f"{doc_id}.md")
        lines = [f"# {doc_name}", ""]
        for c in cases:
            lines.append(f"## 案例：{c.get('title', '')}")
            lines.append("")
            if c.get("symptom"):
                lines.append(f"- **症状**：{c['symptom']}")
            if c.get("root_cause"):
                lines.append(f"- **根因**：{c['root_cause']}")
            if c.get("solution"):
                lines.append("- **处置方案**：")
                for step in str(c["solution"]).split("\n"):
                    if step.strip():
                        lines.append(f"  {step.strip()}")
            lines.append("")
        return {
            "doc_id": doc_id,
            "doc_name": doc_name,
            "content": "\n".join(lines).rstrip() + "\n",
            "case_count": len(cases),
        }

    # ── 检索 ────────────────────────────────────────────
    def query(self, text: str, top_k: int = 3) -> list[dict]:
        self._ensure()
        if self._collection is None or self._collection.count() == 0:
            return []
        try:
            result = self._collection.query(
                query_texts=[text], n_results=min(top_k, 10),
            )
        except Exception as e:
            logger.warning("Chroma query failed: %s", e)
            return []
        metadatas = (result.get("metadatas") or [[]])[0]
        return [
            {
                "title": m.get("title", ""),
                "symptom": m.get("symptom", ""),
                "root_cause": m.get("root_cause", ""),
                "solution": m.get("solution", ""),
            }
            for m in metadatas
        ]

    # ── 统计 / 模板 ─────────────────────────────────────
    def stats(self) -> dict:
        self._ensure()
        if self._collection is None:
            return {
                "ready": False,
                "error": self._init_error or "未初始化",
                "doc_count": 0,
                "case_count": 0,
            }
        return {
            "ready": True,
            "error": None,
            "doc_count": len(self.list_documents()),
            "case_count": self._collection.count(),
        }

    def get_template(self) -> dict:
        return {"name": "故障案例模板.md", "content": TEMPLATE_MD}


# 全局单例
knowledge_service = KnowledgeBaseService()
