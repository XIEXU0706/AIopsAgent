"""RetrievalAgent —— RAG 知识检索 Agent

职责：
  - 根据告警错误特征从故障知识库检索相似案例（优先 Chroma 向量库）
  - Chroma 不可用 / 库为空时降级为关键词规则匹配（内置案例）
  - 返回结构与知识库一致：{title, symptom, root_cause, solution}
"""


import asyncio
import logging
import re
from typing import Any, Optional

from app.agents.base_agent import BaseMindAgent
from app.runtime.agent_runtime import AgentResult
from app.services.knowledge_base import (
    BUILTIN_CASES,
    knowledge_service as _default_knowledge_service,
)

logger = logging.getLogger(__name__)


class RetrievalAgent(BaseMindAgent):
    name = "retrieval"
    description = "故障知识库检索 Agent：检索相似历史故障案例与处置方案"
    claim_types = ["knowledge_retrieval"]

    def __init__(self, context=None, blackboard=None, knowledge_service=None):
        super().__init__(context, blackboard)
        # 可注入独立知识库实例（测试用隔离库），默认使用全局单例
        self._knowledge_service = knowledge_service or _default_knowledge_service

    async def run(self, input_data: dict[str, Any]) -> AgentResult:
        error_type = input_data.get("error_type", "default")
        message = input_data.get("message", "")
        raw_data = input_data.get("raw_data", {})

        # 聚合所有可搜索文本：错误类型 + 告警消息 + 原始数据关键词
        extra_keywords = self._extract_keywords(raw_data)
        query_text = " ".join(
            filter(None, [error_type, message, *extra_keywords])
        )

        # 1. 优先 Chroma 向量检索
        cases, method = await self._vector_search(query_text)
        # 2. 空结果则降级为规则匹配
        if not cases:
            cases, method = self._rule_search(query_text), "rule"

        return AgentResult(
            output={
                "relevant_cases": cases,
                "total": len(cases),
                "method": method,
            }
        )

    async def _vector_search(
        self, query_text: str, top_k: int = 3
    ) -> tuple[list[dict], str]:
        """Chroma 向量检索（同步查询放线程池避免阻塞事件循环）"""
        if not query_text.strip():
            return [], "vector"
        try:
            cases = await asyncio.to_thread(self._knowledge_service.query, query_text, top_k)
            return cases, "vector"
        except Exception as e:
            logger.warning("Vector search failed, fallback to rules: %s", e)
            return [], "vector"

    def _rule_search(self, search_text: str) -> list[dict]:
        """基于关键词对内置案例打分匹配（Chroma 不可用时的降级路径）"""
        tokens = self._tokenize(search_text)
        scored: list[tuple[int, dict]] = []
        for case in BUILTIN_CASES:
            case_text = " ".join(
                str(case.get(k, ""))
                for k in ("title", "symptom", "root_cause", "solution", "keywords")
            )
            score = sum(1 for t in tokens if t and t in case_text)
            if score > 0:
                scored.append((score, case))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:3]]

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """把中英文文本拆成匹配词：ASCII 词 + 连续中文片段 + 中文 2-gram"""
        tokens: set[str] = set()
        tokens.update(re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", text))
        for seg in re.findall(r"[一-龥]{2,}", text):
            tokens.add(seg)
            for i in range(len(seg) - 1):
                tokens.add(seg[i:i + 2])
        return tokens

    @staticmethod
    def _extract_keywords(raw_data: dict) -> list[str]:
        """从原始告警数据提取可搜索的关键词"""
        keywords = []
        if not raw_data:
            return keywords
        # 常见指标/实例字段（支持大小写变体）
        for key in ["metric", "metricName", "alertname", "alertName",
                    "instance", "instanceId", "host", "pod", "node",
                    "region", "threshold", "namespace"]:
            val = raw_data.get(key)
            if isinstance(val, str) and val:
                keywords.append(val)
        # Prometheus 风格 labels
        labels = raw_data.get("labels", {})
        if isinstance(labels, dict):
            for val in labels.values():
                if isinstance(val, str) and val and len(val) < 50:
                    keywords.append(val)
        return keywords
