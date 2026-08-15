"""LogAnalyzeAgent —— 日志分析 Agent

支持双模式：
  1. LLM 模式：调用 DeepSeek 分析（优先）
  2. 规则模式：正则匹配错误模式（降级/无 LLM 时）
"""


import re
from typing import Any, Optional

from app.agents.base_agent import BaseMindAgent
from app.llm import DeepSeekClient, LLMConfig
from app.runtime.agent_runtime import AgentResult

# 错误模式（规则匹配 fallback）
ERROR_PATTERNS: dict[str, list[str]] = {
    "mysql_connection": [
        r"too many connections", r"max_connections",
        r"connection.*refused", r"can't connect",
        r"connections?\s*[:=]\s*\d+", r"connection count",
    ],
    "mysql_slow_query": [r"slow query", r"lock wait timeout"],
    "redis_oom": [r"oom", r"out of memory", r"maxmemory"],
    "high_cpu": [r"cpu.*(high|100%|saturation)", r"load average"],
    "disk_full": [r"disk full", r"no space left"],
}

LOG_ANALYSIS_PROMPT = """你是一个运维日志分析专家。分析以下告警消息，返回 JSON 格式的分析结果（不要带 markdown 代码块标记，只返回纯 JSON）：

{{
  "title": "简洁的告警标题（10字以内）",
  "error_type": "错误类型（简洁中文描述，如：数据库连接数超限）",
  "severity_level": "必须三选一：critical(严重) / warning(警告) / info(提示)",
  "root_cause": "根因分析（一句话概括）",
  "analysis": "详细分析说明（100字以内）",
  "suggested_actions": ["建议1", "建议2"]
}}

告警标题：{title}
告警消息：{message}
告警来源：{source}

原始告警数据（完整上下文）：
{raw_payload}"""


class LogAnalyzeAgent(BaseMindAgent):
    name = "log_analyzer"
    description = "日志分析 Agent：基于 LLM + 规则双引擎分析告警日志"
    claim_types = ["log_analysis"]

    def __init__(self, context=None, blackboard=None):
        super().__init__(context, blackboard)
        self._llm: Optional[DeepSeekClient] = None
        self._config: LLMConfig = LLMConfig(temperature=0.1)

    async def run(self, input_data: dict[str, Any]) -> AgentResult:
        message = input_data.get("message", "")
        title = input_data.get("title", "")
        source = input_data.get("source", "")
        error_type = input_data.get("error_type", "")
        raw_data = input_data.get("raw_data", {})

        # 优先 LLM 分析
        analysis = await self._llm_analyze(title, message, source, raw_data)

        # LLM 失败则规则降级
        if analysis is None:
            analysis = self._rule_analyze(message, error_type)

        return AgentResult(output=analysis)

    async def _llm_analyze(
        self, title: str, message: str, source: str, raw_data: dict | None = None
    ) -> Optional[dict]:
        """调用 DeepSeek 分析日志"""
        client = self._get_llm()
        if client is None:
            return None

        # 构造原始数据预览（限制大小）
        raw_preview = ""
        if raw_data:
            try:
                import json
                preview = json.dumps(raw_data, ensure_ascii=False, indent=2)
                raw_preview = preview[:2000]  # 截断防止超 token
            except Exception:
                raw_preview = str(raw_data)[:2000]

        prompt = LOG_ANALYSIS_PROMPT.format(
            title=title or "未知",
            message=message[:1500],
            source=source or "unknown",
            raw_payload=raw_preview or "无",
        )

        try:
            text = await client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
            )
            # 清理可能的 markdown 包装
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

            import json
            return json.loads(text)
        except Exception as e:
            self._log(f"LLM analysis failed, fallback to rules: {e}")
            return None

    def _rule_analyze(self, message: str, error_type: str) -> dict:
        """规则匹配降级"""
        matched = self._match_patterns(message, error_type)
        info = self._extract_key_info(message)
        analysis = self._build_analysis_text(error_type, matched, info)
        canonical = self._canonical_error_type(error_type, matched)

        return {
            "error_type": canonical,
            "root_cause": analysis.split("\n")[0] if analysis else "未知",
            "analysis": analysis,
            "severity_level": "critical" if matched else "warning",
            "suggested_actions": self._suggest_actions(error_type, matched),
        }

    def _canonical_error_type(self, error_type: str, matched: list[str]) -> str:
        """把降级分析出的错误类型归一到已知枚举，避免出现 unknown/中文等非枚举值"""
        if error_type and error_type in ERROR_PATTERNS:
            return error_type
        if matched:
            for etype, pat_list in ERROR_PATTERNS.items():
                if matched[0] in pat_list:
                    return etype
        return "custom"

    def _match_patterns(self, message: str, error_type: str) -> list[str]:
        matched = []
        patterns = ERROR_PATTERNS.get(error_type, [])
        for p in patterns:
            if re.search(p, message, re.IGNORECASE):
                matched.append(p)
        if not matched:
            for pat_list in ERROR_PATTERNS.values():
                for p in pat_list:
                    if re.search(p, message, re.IGNORECASE) and p not in matched:
                        matched.append(p)
        return matched

    def _extract_key_info(self, message: str) -> dict:
        numbers = re.findall(r"(\d+[,.]?\d*)", message)
        hosts = re.findall(r"Host:\s*(\S+)", message, re.IGNORECASE)
        return {"numbers": numbers[:10], "hosts": hosts[:5]}

    def _build_analysis_text(self, error_type: str, matched: list[str], info: dict) -> str:
        lines = [f"错误类型: {error_type}"]
        if matched:
            lines.append(f"匹配模式: {', '.join(matched)}")
        if info.get("hosts"):
            lines.append(f"相关主机: {', '.join(info['hosts'])}")
        lines.append("")
        if error_type == "mysql_connection" or "too many connections" in str(matched):
            lines.append("数据库连接数已达上限，建议检查连接池配置和慢查询")
        elif "oom" in error_type:
            lines.append("内存资源耗尽，建议检查大 Key 或内存泄漏")
        else:
            lines.append("建议查看完整日志和监控指标以确定根因")
        return "\n".join(lines)

    def _suggest_actions(self, error_type: str, matched: list[str]) -> list[str]:
        if "too many connections" in str(matched):
            return ["检查连接池配置", "排查连接泄漏", "临时增加 max_connections"]
        elif "oom" in error_type:
            return ["分析大 Key", "设置合理淘汰策略", "考虑扩容"]
        return ["查看完整监控面板", "检查最近变更记录"]

    def _get_llm(self) -> Optional[DeepSeekClient]:
        if self._llm is None:
            try:
                from app.config import settings
                if settings.deepseek_api_key:
                    self._llm = DeepSeekClient(
                        LLMConfig(api_key=settings.deepseek_api_key)
                    )
            except Exception:
                pass
        return self._llm
