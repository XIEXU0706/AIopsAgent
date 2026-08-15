"""CoordinatorAgent —— 告警事件协调者

职责：
  1. 接收告警事件
  2. 拆解为多个子任务发布到黑板（日志分析、知识检索）
  3. 监听黑板事件，等待子任务完成
  4. 汇总分析结论
  5. 调用 SafetyAgent 做安全审查
  6. 输出最终处置报告
"""


import asyncio
import json
import logging
import time
from typing import Any

from app.agents.base_agent import BaseMindAgent
from app.blackboard.models import Artifact, BoardEvent, Task
from app.models.severity import normalize_severity
from app.llm import DeepSeekClient, LLMConfig
from app.runtime.agent_runtime import AgentResult
from app.runtime.context import ExecutionContext

logger = logging.getLogger(__name__)

# 子任务收集超时（秒）：子 Agent 异常时防止告警永久挂起
SUBTASK_TIMEOUT = 30

# Coordinator 归纳阶段的系统提示词：约束模型做结构化、可审计的运维结论
SYNTHESIS_SYSTEM_PROMPT = (
    "你是 AIOps 告警处置系统的协调者。你需要综合日志分析 Agent 与知识检索 Agent 的产出，"
    "给出面向运维人员的根因归纳与处置清单。要求：1) 结论必须基于输入事实，不得臆造；"
    "2) 处置清单按优先级排序、可执行、避免高危操作；3) 严格输出 JSON 格式 "
    "{\"summary\": str, \"disposition_plan\": str}。"
)


class CoordinatorAgent(BaseMindAgent):
    name = "coordinator"
    description = "告警事件协调者：拆解任务、汇总结论、触发安全审查"
    claim_types = []

    def __init__(self, context: ExecutionContext, blackboard=None):
        super().__init__(context, blackboard)
        self._collected_artifacts: dict[str, Artifact] = {}
        self._llm = None  # 惰性初始化，避免无 LLM 配置时强制报错

    def _get_llm(self):
        """惰性获取 LLM 客户端；未配置时返回 None 走规则降级路径。"""
        if self._llm is None:
            try:
                from app.config import settings
                if settings.deepseek_api_key:
                    self._llm = DeepSeekClient(LLMConfig(api_key=settings.deepseek_api_key))
                else:
                    self._llm = False
            except Exception as exc:  # 未配置 API key 等：静默降级
                logger.warning("Coordinator LLM 不可用，使用规则归纳: %s", exc)
                self._llm = False
        return self._llm or None

    async def run(self, input_data: dict[str, Any]) -> AgentResult:
        alert = input_data.get("alert", {})
        logger.info("Coordinator handling alert: %s", alert.get("id"))

        # 外部告警只有 raw_data，从 raw_data 兜底提取结构化字段
        raw_data = alert.get("raw_data", {}) or {}
        title = str(alert.get("title") or raw_data.get("alertname")
                    or raw_data.get("alert_name") or raw_data.get("title") or "")
        message = str(alert.get("message") or raw_data.get("message") or "")
        error_type = str(alert.get("error_type") or raw_data.get("error_type") or "")
        severity = normalize_severity(
            alert.get("severity") or raw_data.get("severity") or raw_data.get("level")
        )

        # 1. 在黑板上创建根任务
        root_task = Task(
            type="root",
            input={
                "alert_id": alert.get("id"),
                "title": title,
                "message": message,
                "error_type": error_type,
                "severity": severity,
                "source": alert.get("source"),
                "raw_data": raw_data,
            },
        )
        await self.blackboard.publish_task(root_task)

        # 2. 发布子任务
        subtask_types = ["log_analysis", "knowledge_retrieval"]
        subtask_ids = []

        for stype in subtask_types:
            task = Task(
                type=stype,
                input={
                    "alert_id": alert.get("id"),
                    "title": title,
                    "message": message,
                    "error_type": error_type,
                    "raw_data": raw_data,
                },
            )
            await self.blackboard.publish_task(task)
            subtask_ids.append(task.id)

        # 3. 订阅黑板事件，等待子任务完成
        queue = self.blackboard.subscribe()
        # 超时保护：子 Agent 若崩溃，其任务永不会完成，此处兜底防止永久挂起
        deadline = time.monotonic() + SUBTASK_TIMEOUT
        try:
            while len(self._collected_artifacts) < len(subtask_ids):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "Subtask collection timed out, continuing with %d/%d artifacts",
                        len(self._collected_artifacts), len(subtask_ids),
                    )
                    break
                try:
                    event: BoardEvent = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Subtask collection timed out, continuing with %d/%d artifacts",
                        len(self._collected_artifacts), len(subtask_ids),
                    )
                    break
                if event.event_type == "task_completed" and event.task.id in subtask_ids:
                    if event.task.artifact_ids:
                        aid = event.task.artifact_ids[-1]
                        artifact = await self.blackboard.get_artifact(aid)
                        if artifact:
                            self._collected_artifacts[event.task.type] = artifact
                            logger.info(
                                "Collected artifact from %s (task=%s)",
                                event.task.type,
                                event.task.id,
                            )
        finally:
            self.blackboard.unsubscribe(queue)

        # 4. 标记根任务完成（通知 Sub-Agent 循环退出）
        await self.blackboard.complete_task(
            root_task.id,
            Artifact(
                task_id=root_task.id,
                agent_name=self.name,
                type="root_summary",
                content={"status": "completed"},
            ),
        )

        # 5. 从 LogAnalyzer 结果提取结构化字段（用于 UI 展示和 Skill 匹配）
        log_artifact = self._collected_artifacts.get("log_analysis")
        extracted_title = log_artifact.content.get("title", "") if log_artifact else ""
        extracted_severity = normalize_severity(
            log_artifact.content.get("severity_level", "") if log_artifact else ""
        )
        extracted_error_type = log_artifact.content.get("error_type", "") if log_artifact else ""

        # 6. 汇总结论
        conclusion = self._synthesize(self._collected_artifacts)

        # 7. 相关故障案例（结构化字段，供前端在"元信息"下方单独展示）
        related_cases = self._extract_related_cases(self._collected_artifacts)

        # 8. Safety 审查（由 Harness 在外部调用，此处仅生成处置计划）
        disposition_plan = conclusion.get("disposition_plan", "")
        summary = conclusion.get("summary", "")

        # 9. 生成交接摘要
        handover_summary = self._generate_handover_summary(
            alert, conclusion, extracted_title, extracted_error_type,
        )

        return AgentResult(
            output={
                "alert_id": alert.get("id"),
                "title": extracted_title,
                "severity": extracted_severity,
                "summary": summary,
                "disposition_plan": disposition_plan,
                "handover_summary": handover_summary,
                "related_cases": related_cases,
                "artifacts": {
                    k: v.content for k, v in self._collected_artifacts.items()
                },
                "error_type": extracted_error_type,
                "trace_id": self.context.trace_id,
            }
        )

    @staticmethod
    def _extract_related_cases(artifacts: dict[str, Artifact]) -> list[dict]:
        """提取有实质内容的相关案例（过滤掉"未知异常 / 待分析"这类通用兜底案例）"""
        retrieval_result = artifacts.get("knowledge_retrieval")
        if not retrieval_result:
            return []
        return [
            c for c in retrieval_result.content.get("relevant_cases", [])[:3]
            if c.get("root_cause") not in ("", "待分析")
            and c.get("symptom") not in ("", "未知异常")
        ]

    @staticmethod
    def adjust_plan(original_plan: str, safety_feedback: dict) -> dict:
        """根据 Safety 审查结果调整处置计划（重新采纳）"""
        matched_rules = safety_feedback.get("matched_rules", [])
        if not matched_rules:
            return {"revised_plan": original_plan, "adjusted": False}

        risk_level = safety_feedback.get("risk_level", "low")
        labels = [r["label"] for r in matched_rules]

        lines = [
            f"> **经过安全审查后修订的处置计划**（原计划检测到 {risk_level.upper()} 风险：{', '.join(labels)}）",
            "",
            "**以下操作已移除或替换为安全替代方案：**",
        ]
        for rule in matched_rules:
            lines.append(f"- ~~{rule['label']}~~ → 已移除，需人工审批")

        lines.append("")
        lines.append("**修订后的处置措施：**")
        lines.append("1. 优先采用非侵入式诊断手段（查看日志、监控指标）")
        lines.append("2. 如需执行高危操作，需提交工单审批")
        lines.append("3. 建议先在灰度环境验证")
        lines.append("4. 操作前备份关键配置和数据")

        return {
            "revised_plan": "\n".join(lines),
            "adjusted": True,
            "original_risk": risk_level,
            "removed_operations": labels,
        }

    @staticmethod
    def _generate_handover_summary(alert: dict, conclusion: dict, extracted_title: str, extracted_error_type: str) -> str:
        """生成交班摘要"""
        lines = [
            f"## 交接摘要",
            f"",
            f"**告警**: {extracted_title or alert.get('title', '未知')}",
            f"**错误类型**: {extracted_error_type or alert.get('error_type', '')}",
            f"**严重级别**: {alert.get('severity', '')}",
            f"",
            f"**分析概要**: {conclusion.get('summary', '')[:300]}",
            f"",
            f"**处置计划**: {conclusion.get('disposition_plan', '')[:300]}",
            f"",
            f"**待办事项**:",
            f"1. 确认告警是否已恢复",
            f"2. 如处置计划已执行，验证效果",
            f"3. 关注同类告警是否重复出现",
            f"",
            f"> 由 AIOps 系统自动生成，仅供参考。",
        ]
        return "\n".join(lines)

    def _synthesize(
        self,
        artifacts: dict[str, Artifact],
    ) -> dict:
        """汇总各 Agent 产出，生成分析结论。

        策略：优先调用 LLM 做跨 Agent 的结构化归纳（根因关联、处置优先级排序）；
        当 LLM 不可用（未配置 / 超时 / 解析失败）时降级为基于子 Agent 原始产出的规则拼接，
        保证链路在任何环境下都可产出结果。
        """
        log_result = artifacts.get("log_analysis")
        retrieval_result = artifacts.get("knowledge_retrieval")

        # 规则降级路径：直接复用子 Agent 原始产出（无 LLM 时的最低可用结果）
        rule_summary, rule_disposition = self._rule_fallback(log_result)

        llm = self._get_llm()
        if llm is None:
            return {"summary": rule_summary, "disposition_plan": rule_disposition}

        # 构造跨 Agent 归纳所需的上下文
        llm_payload = {
            "log_analysis": log_result.content if log_result else None,
            "knowledge_retrieval": retrieval_result.content if retrieval_result else None,
        }
        prompt = self._build_synthesis_prompt(llm_payload)

        try:
            resp = llm.chat(
                messages=[
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=900,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(resp.strip())
            return {
                "summary": parsed.get("summary") or rule_summary,
                "disposition_plan": parsed.get("disposition_plan") or rule_disposition,
                "llm_synthesized": True,
            }
        except Exception as exc:
            logger.warning("LLM 归纳失败，降级规则归纳: %s", exc)
            return {"summary": rule_summary, "disposition_plan": rule_disposition}

    @staticmethod
    def _rule_fallback(log_result: Artifact | None) -> tuple[str, str]:
        """无 LLM 时的规则归纳（保持与历史行为一致）。"""
        lines = []
        if log_result:
            analysis = log_result.content.get("analysis", "")
            if analysis:
                lines.append("### 日志分析")
                lines.append(analysis)
                lines.append("")
        suggested = log_result.content.get("suggested_actions", []) if log_result else []
        disposition_lines = ["根据分析结果，建议执行以下处置措施："]
        for j, action in enumerate(suggested[:5], 1):
            disposition_lines.append(f"  {j}. {action}")
        if not suggested:
            disposition_lines.append("  1. 查看完整监控面板")
            disposition_lines.append("  2. 检查最近变更记录")
            disposition_lines.append("  3. 查看应用日志堆栈")
        return "\n".join(lines), "\n".join(disposition_lines)

    @staticmethod
    def _build_synthesis_prompt(payload: dict) -> str:
        """将多个子 Agent 的结构化产出压缩为 LLM 归纳提示词。"""
        parts = ["请将以下多个运维 Agent 的分析结果归纳成一份处置结论。"]
        la = payload.get("log_analysis")
        if la:
            parts.append(f"【日志分析 Agent】\n分析: {la.get('analysis', '')}")
            if la.get("suggested_actions"):
                parts.append("建议操作: " + "; ".join(la["suggested_actions"]))
            if la.get("root_cause"):
                parts.append(f"推测根因: {la['root_cause']}")
        kr = payload.get("knowledge_retrieval")
        if kr and kr.get("relevant_cases"):
            cases = kr["relevant_cases"][:3]
            parts.append("【知识检索 Agent 相关历史案例】")
            for i, c in enumerate(cases, 1):
                parts.append(
                    f"  {i}. 症状={c.get('symptom')} 根因={c.get('root_cause')} "
                    f"处置={c.get('disposition')}"
                )
        parts.append(
            "请输出 JSON："
            "{\"summary\": 面向运维人员的根因归纳(中文), "
            "\"disposition_plan\": 按优先级排序的处置清单(中文, 含编号)}"
        )
        return "\n".join(parts)
