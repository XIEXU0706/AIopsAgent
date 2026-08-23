"""故障知识库服务 —— Chroma 向量存储实现

- 文档上传（markdown 解析为案例）→ 向量化 → 存入 Chroma
- 查询：输入告警特征文本，返回最相似故障案例
- embedding 后端可切换（EMBEDDING_BACKEND）：
  - bge:      BAAI/bge-small-zh-v1.5 语义向量（512 维，CPU 可跑），
              检索侧加 bge 官方指令前缀提升召回
  - hashing:  自研确定性特征哈希（crc32），零依赖、跨进程稳定
  - auto（默认）: 安装了 sentence-transformers 则用 bge，否则降级 hashing；
              bge 模型加载失败时同样自动降级，保证知识库能力不中断
"""

import asyncio
import importlib.util
import logging
import math
import re
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CHROMA_DIR = DATA_DIR / "chroma"

EMBED_DIM = 512
# embedding 算法版本：升级算法后集合名随之变化，强制重建索引避免旧向量不兼容
EMBEDDING_VERSION = "v2"
COLLECTION_NAME = f"fault_cases_{EMBEDDING_VERSION}"
# BGE 语义向量使用独立集合（向量空间与哈希不兼容，且文档需重新编码）
BGE_COLLECTION_NAME = "fault_cases_bge_v1"

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

    # ── 统一编码接口（与 BGEEmbeddingFunction 对齐） ──
    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def encode_query(self, text: str) -> list[float]:
        return self._embed(text)

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


# bge 官方推荐的中文检索指令前缀：仅加在 query 侧，文档侧不加
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def bge_available() -> bool:
    """检测 sentence-transformers 是否已安装（不触发模型下载）"""
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except Exception:
        return False


def resolve_embedding_backend(requested: str) -> str:
    """把 auto 解析成实际可用的 backend（bge 不可用时降级 hashing）"""
    if requested == "auto":
        return "bge" if bge_available() else "hashing"
    return requested


class BGEEmbeddingFunction(EmbeddingFunction):
    """BAAI/bge-small-zh-v1.5 语义 embedding（512 维，CPU 秒级推理）

    - 构造零开销：模型懒加载，保证 Chroma 从 collection 元数据重建
      EF 时不会触发模型下载
    - 文档编码不加前缀；query 编码加 bge 官方检索指令前缀提升召回
    - 输出 L2 归一化，配合 cosine 空间
    """

    def __init__(self, dim: int = EMBED_DIM, model_name: str = ""):
        self.dim = dim
        self.model_name = model_name or settings.bge_model_name
        self._model = None  # 懒加载

    @staticmethod
    def name() -> str:
        return "bge_small_zh_v1"

    @staticmethod
    def build_from_config(config: dict[str, Any]):
        return BGEEmbeddingFunction(
            dim=config.get("dim", EMBED_DIM),
            model_name=config.get("model_name", ""),
        )

    def get_config(self) -> dict[str, Any]:
        return {"dim": self.dim, "model_name": self.model_name}

    def is_legacy(self) -> bool:
        return False

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading BGE model: %s (首次运行需下载)", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def __call__(self, input):
        return self.encode_documents(list(input))

    # ── 统一编码接口 ──
    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def encode_query(self, text: str) -> list[float]:
        model = self._load()
        vec = model.encode(
            [BGE_QUERY_PREFIX + text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        return vec.tolist()


# BGE embedder 进程级单例：模型加载耗时（秒级），必须全局复用
_BGE_INSTANCE: Optional["BGEEmbeddingFunction"] = None


def get_bge_embedder() -> "BGEEmbeddingFunction":
    global _BGE_INSTANCE
    if _BGE_INSTANCE is None:
        _BGE_INSTANCE = BGEEmbeddingFunction()
    return _BGE_INSTANCE


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
    """Chroma 向量知识库（embedding 后端可切换：bge 语义 / hashing 词法）"""

    def __init__(self, persist_dir: str = str(CHROMA_DIR),
                 embedding_backend: Optional[str] = None):
        self._persist_dir = persist_dir
        # None → 跟随全局配置 settings.embedding_backend
        self._requested_backend = embedding_backend
        self._backend: Optional[str] = None  # 实际生效的 backend（_ensure 后确定）
        self._client = None
        self._collection = None
        self._init_error: Optional[str] = None
        self._lock = asyncio.Lock()
        self._seeded = False

    @property
    def backend_name(self) -> str:
        """实际生效的 embedding 后端（bge / hashing；未初始化时返回解析后的请求值）"""
        if self._backend:
            return self._backend
        return resolve_embedding_backend(
            self._requested_backend or settings.embedding_backend
        )

    # ── 初始化 ──────────────────────────────────────────
    def _ensure(self) -> None:
        """懒初始化 Chroma（失败只记录，不抛异常，避免阻塞启动）"""
        if self._collection is not None or self._init_error:
            return
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils.embedding_functions import register_embedding_function

            requested = self._requested_backend or settings.embedding_backend
            backend = resolve_embedding_backend(requested)

            # bge 自检：模型加载/下载失败则降级 hashing，保证知识库能力不中断
            embedder = None
            if backend == "bge":
                candidate = get_bge_embedder()
                try:
                    candidate.encode_query("embedding 自检")
                    embedder = candidate
                except Exception as e:
                    logger.warning(
                        "BGE 模型不可用(%s)，知识库降级为 hashing embedding", e)
                    backend = "hashing"
            if embedder is None:
                embedder = HashingEmbeddingFunction()

            # 注册自定义 embedding function，重启后加载已有 collection 时才能重建
            register_embedding_function(type(embedder))

            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            # 集合名带 embedding 版本：算法升级后自动换新集合重建，旧向量不兼容
            collection_name = (
                BGE_COLLECTION_NAME if backend == "bge" else COLLECTION_NAME
            )
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                embedding_function=embedder,
                metadata={"hnsw:space": "cosine", "embedding_version": backend},
            )
            self._backend = backend
            logger.info(
                "Chroma knowledge base ready at %s (embedding=%s)",
                self._persist_dir, backend,
            )
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
            if self._backend == "bge":
                # 检索侧加 bge 指令前缀（文档入库时未加），显式传向量绕过自动编码
                query_vector = get_bge_embedder().encode_query(text)
                result = self._collection.query(
                    query_embeddings=[query_vector],
                    n_results=min(top_k, 10),
                )
            else:
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
                "backend": self.backend_name,
                "doc_count": 0,
                "case_count": 0,
            }
        return {
            "ready": True,
            "error": None,
            "backend": self._backend,
            "doc_count": len(self.list_documents()),
            "case_count": self._collection.count(),
        }

    def get_template(self) -> dict:
        return {"name": "故障案例模板.md", "content": TEMPLATE_MD}


# 全局单例
knowledge_service = KnowledgeBaseService()
