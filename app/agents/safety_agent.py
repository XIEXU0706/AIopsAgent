"""SafetyAgent —— 高危操作安全拦截 Agent

职责：
  - 规则匹配：正则检测高危命令（DROP TABLE、rm -rf 等），命中即硬拦截
  - LLM 评估：规则未命中时，由大模型对处置计划做语义级风险评级，
    捕获规则库覆盖不到的隐式高危意图（如“回滚到上周版本”“踢掉所有会话”）
  - 拦截结果包含风险等级、匹配规则、处置建议
"""


import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from app.agents.base_agent import BaseMindAgent
from app.llm import DeepSeekClient, LLMConfig
from app.runtime.agent_runtime import AgentResult

logger = logging.getLogger(__name__)

# Safety Agent 语义评级系统提示词：约束模型在规则库之外识别隐式高危意图
SAFETY_SYSTEM_PROMPT = (
    "你是 AIOps 安全审查官。规则库已拦截显式高危命令，请你基于运维语义判断处置计划是否存在"
    "隐式风险（如回滚到旧版本、踢掉全部用户会话、跨可用区切换、批量重启等可能引发二次故障的操作）。"
    "严格输出 JSON：{\"risk_level\": \"low|medium|high|critical\", "
    "\"rationale\": 一句话理由, \"requires_approval\": bool}。"
    "只有确认无风险时才返回 low。"
)

# 内置兜底规则（配置文件缺失或损坏时使用，保证安全能力不降级）
_BUILTIN_RISK_RULES: list[dict] = [
    # 数据库
    {"pattern": r"\bDROP\s+(TABLE|DATABASE|INDEX|VIEW)\b", "level": "critical", "label": "数据库删除操作"},
    {"pattern": r"\bDELETE\s+FROM\b", "level": "high", "label": "数据库删除数据"},
    {"pattern": r"\bTRUNCATE\b", "level": "critical", "label": "数据库清空操作"},
    {"pattern": r"\bALTER\s+TABLE\b", "level": "medium", "label": "数据库结构变更"},
    {"pattern": r"\bUPDATE\b.*\bSET\b", "level": "high", "label": "数据库数据修改"},
    # 系统
    {"pattern": r"\brm\s+-[rf]+\b", "level": "critical", "label": "强制删除文件"},
    {"pattern": r"\bshutdown\b", "level": "critical", "label": "关机操作"},
    {"pattern": r"\breboot\b", "level": "critical", "label": "重启操作"},
    {"pattern": r"\bkill\s+-9\b", "level": "high", "label": "强制杀进程"},
    {"pattern": r"\bchmod\s+777\b", "level": "medium", "label": "修改文件权限为777"},
    {"pattern": r"\bwget\b", "level": "medium", "label": "下载外部文件"},
    {"pattern": r"\bcurl\b.*\|.*\bbash\b", "level": "critical", "label": "管道执行远程脚本"},
    # 中间件
    {"pattern": r"\bflushall\b", "level": "critical", "label": "Redis 清空全部数据"},
    {"pattern": r"\bflushdb\b", "level": "high", "label": "Redis 清空当前数据库"},
    {"pattern": r"\bcluster\s+failover\b", "level": "high", "label": "Redis 手动故障转移"},
    {"pattern": r"\breset\s+master\b", "level": "critical", "label": "MySQL 重置主库"},
    {"pattern": r"\bstop\s+slave\b", "level": "high", "label": "MySQL 停止复制"},
]


def load_risk_rules() -> list[dict]:
    """从外置 JSON 配置加载高危规则库；文件缺失/损坏时回退内置规则，保证安全能力不降级。"""
    config_path = Path(__file__).parent / "safety_rules.json"
    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rules = data.get("rules", [])
            if rules:
                logger.info("Loaded %d risk rules from %s", len(rules), config_path)
                return rules
            logger.warning("Risk rule config is empty, falling back to builtin rules")
        else:
            logger.warning("Risk rule config %s not found, using builtin rules", config_path)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load risk rules (%s), falling back to builtin rules", e)
    return list(_BUILTIN_RISK_RULES)


# 高危操作规则库（优先外置配置，兜底内置）
RISK_RULES: list[dict] = load_risk_rules()


class SafetyAgent(BaseMindAgent):
    name = "safety"
    description = "安全审查 Agent：检测高危操作，强制人工复核"
    claim_types = ["safety_check"]

    async def run(self, input_data: dict[str, Any]) -> AgentResult:
        disposition_plan = input_data.get("disposition_plan", "")
        alert_message = input_data.get("alert_message", "")

        # 1. 规则匹配（硬拦截层）
        matched_rules = self._rule_check(disposition_plan + " " + alert_message)

        # 2. 风险评级：规则命中即定级，否则交给 LLM 做语义级评级
        if matched_rules:
            levels = [r["level"] for r in matched_rules]
            risk_level = "critical" if "critical" in levels else "high"
            llm_risk = None
        else:
            risk_level, llm_risk = await self._llm_assess(disposition_plan, alert_message)

        return AgentResult(
            output={
                "approved": risk_level == "low",
                "risk_level": risk_level,
                "matched_rules": matched_rules,
                "llm_risk": llm_risk,
                "reason": self._build_reason(risk_level, matched_rules, llm_risk),
                "suggested_mitigation": (
                    "请人工复核处置方案，确认无误后在工单系统中审批执行。"
                    if risk_level in ("critical", "high")
                    else ""
                ),
            },
            intercepted=risk_level in ("critical", "high"),
        )

    async def _llm_assess(self, disposition_plan: str, alert_message: str) -> tuple[str, dict | None]:
        """规则未命中时的语义级风险评级。

        返回 (risk_level, llm_risk)。LLM 不可用时返回 ("low", None) 走放行，
        但保留规则层作为最终硬拦截，避免 LLM 误判放大风险。
        """
        try:
            from app.config import settings
            if not settings.deepseek_api_key:
                return "low", None
            llm = DeepSeekClient(LLMConfig(api_key=settings.deepseek_api_key))
            resp = await llm.chat(
                messages=[
                    {"role": "system", "content": SAFETY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"告警信息：{alert_message}\n处置计划：{disposition_plan}"
                        ),
                    },
                ],
                temperature=0.0,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(resp.strip())
            level = parsed.get("risk_level", "low")
            level = level if level in ("low", "medium", "high", "critical") else "low"
            return level, {
                "risk_level": level,
                "rationale": parsed.get("rationale", ""),
                "requires_approval": parsed.get("requires_approval", level != "low"),
            }
        except Exception as exc:
            logger.warning("Safety LLM 评级失败，放行(规则层仍生效): %s", exc)
            return "low", None

    def _rule_check(self, text: str) -> list[dict]:
        """匹配所有命中的高危规则"""
        matched = []
        for rule in RISK_RULES:
            if re.search(rule["pattern"], text, re.IGNORECASE):
                matched.append(rule)
        return matched

    def _build_reason(self, risk_level: str, matched_rules: list[dict], llm_risk: dict | None) -> str:
        if matched_rules:
            labels = [r["label"] for r in matched_rules]
            return (
                f"检测到 {risk_level.upper()} 风险操作：{', '.join(labels)}。"
                f"共 {len(matched_rules)} 条规则命中，需人工复核。"
            )
        if llm_risk:
            rationale = llm_risk.get("rationale", "")
            return (
                f"规则层未命中，但语义评级为 {risk_level.upper()} 风险。"
                f"{rationale}"
            )
        return "未检测到高危操作（规则与语义评级均通过）"
