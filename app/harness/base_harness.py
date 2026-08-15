"""
AIOpsAgentHarness —— Agent 工程治理层

职责（横切关注点统一处理）：
  1. 输入脱敏 —— 去除 IP、主机名等敏感信息
  2. Agent 编排 —— Coordinator → sub-agents → Safety
  3. Skill 加载 —— 根据告警类型匹配并执行 Skill
  4. MCP 工具后处理 —— 工具调用异步队列（幂等/限流/重试/DLQ）
  5. 处置报告生成 —— 落库
  6. Trace 追踪 —— 全链路 span 记录

设计意图：Agent 只关注业务逻辑，Harness 只关注治理。
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.blackboard.models import Artifact

from app.agents.coordinator import CoordinatorAgent
from app.agents.log_analyzer import LogAnalyzeAgent
from app.agents.retrieval_agent import RetrievalAgent
from app.agents.safety_agent import SafetyAgent
from app.mcp.tools import excel_export, send_notification, append_note
from app.blackboard.blackboard import CollaborationBlackboard
from app.models.report import DispositionReport
from app.mcp import AsyncToolQueue, ToolTask
from app.mcp.tools import export_report
from app.runtime.agent_runtime import AgentResult, AgentRuntime
from app.runtime.context import ExecutionContext
from app.runtime.trace import trace_manager
from app.skills import AIOpsSkillRegistry

logger = logging.getLogger(__name__)


@dataclass
class HarnessResult:
    """Harness 处理结果"""
    trace_id: str
    report: DispositionReport
    events: list[dict] = field(default_factory=list) #  处理过程中产生的所有事件
    skill_results: list[dict] = field(default_factory=list)  # Skill 插件执行结果列表
    tool_tasks: list[str] = field(default_factory=list) # 异步 MCP 工具任务的 ID 列表
    agent_title: str = ""
    agent_severity: str = ""
    agent_error_type: str = ""
    handover_summary: str = ""
    adoption_rounds: int = 0
    related_cases: list[dict] = field(default_factory=list)


class AIOpsAgentHarness:
    """Agent 治理 Harness"""
    def __init__(
        self,
        skill_registry: Optional[AIOpsSkillRegistry] = None,
        tool_queue: Optional[AsyncToolQueue] = None,
    ):
        self.blackboard = CollaborationBlackboard()  # agent间共享的协作黑板
        self.runtime = AgentRuntime(self.blackboard)
        self.register_agents()

        # Skill 系统
        if skill_registry is None:
            scan_dir = str(Path(__file__).resolve().parent.parent / "skills" / "definitions")
            skill_registry = AIOpsSkillRegistry(scan_dir=scan_dir)
        self.skill_registry = skill_registry

        # MCP 工具队列
        if tool_queue is None:
            tool_queue = AsyncToolQueue()
            self.register_default_tools(tool_queue)
        self.tool_queue = tool_queue
        self._tool_queue_task: Optional[asyncio.Task] = None

        self._event_store: dict[str, list[dict]] = {}
        self._event_queues: dict[str, list] = {}

    def register_default_tools(self, queue: AsyncToolQueue) -> None:
        """注册默认 MCP 工具处理器"""
        queue.register_handler("export_report", export_report)
        queue.register_handler("excel_export", excel_export)
        queue.register_handler("send_notification", send_notification)
        queue.register_handler("append_note", append_note)

    def start_tool_queue(self) -> None:
        """启动后台工具队列处理循环"""
        if self._tool_queue_task is None:
            self._tool_queue_task = asyncio.create_task(self.tool_queue.process_loop())
            logger.info("Tool queue background loop started")


    # ── Agent 注册 ──────────────────────────────────────────
    def register_agents(self) -> None:
        """注册所有 Agent"""
        self.runtime.register_agent(CoordinatorAgent)
        self.runtime.register_agent(LogAnalyzeAgent)
        self.runtime.register_agent(RetrievalAgent)
        self.runtime.register_agent(SafetyAgent)


    # ── 核心流程 ────────────────────────────────────────────
    async def process_alert(
        self,
        alert_dict: dict,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> HarnessResult:
        """处理一条告警事件（完整链路）

        流程:
          Coordinator → [LogAnalyze, Retrieval] 并行 → Safety
          → Skill 匹配与执行 → MCP 工具后处理
        """
        if trace_id is None:
            trace_id = self.create_trace(alert_dict.get("id"), session_id)
        start = time.time()

        # 1. 输入脱敏
        sanitized = self.sanitize(alert_dict)
        self.push_event(trace_id, "alert_received", {"alert": sanitized})

        # 2. 运行 Coordinator (统筹的agent)
        coord_ctx = ExecutionContext(
            trace_id=trace_id,
            agent_name="coordinator",
            alert=alert_dict,
        )
        sub_agents = ["log_analyzer", "retrieval"]
        claim_tasks = [
            asyncio.create_task(self.auto_claim_loop(agent_name, trace_id))
            for agent_name in sub_agents
        ]

        self.push_event(trace_id, "agent_started", {"agent": "coordinator"})
        coord_result = await self.runtime.run("coordinator", coord_ctx, {
            "alert": sanitized,
        })
        await asyncio.gather(*claim_tasks)

        self.push_event(
            trace_id, 
            "coordinator_completed", 
            { 
                "summary": coord_result.output.get("summary", ""),
            }
        )

        # 3. Skill 匹配与执行（error_type 从 Coordinator 的分析结果提取）
        error_type = coord_result.output.get("error_type", "")
        skill_results = await self._execute_skills(trace_id, error_type, sanitized)

        # 4. Safety 审查 + 重新采纳循环（最多 2 轮）
        safety_ctx = ExecutionContext(
            trace_id=trace_id,
            parent_span_id=trace_id,
            agent_name="safety",
            alert=alert_dict,
        )
        max_adoption_rounds = 2
        adoption_round = 0
        current_disposition = coord_result.output.get("disposition_plan", "")
        final_safety_result = None
        raw_alert = alert_dict.get("raw_data") or {}
        alert_message = alert_dict.get("message") or raw_alert.get("message") or ""

        while adoption_round < max_adoption_rounds:
            self.push_event(trace_id, "agent_started", {
                "agent": "safety", "round": adoption_round + 1,
            })
            safety_result = await self.runtime.run("safety", safety_ctx, {
                "disposition_plan": current_disposition,
                "alert_message": alert_message,
            })
            self.push_event(trace_id, "safety_completed", {
                "approved": safety_result.output.get("approved", False),
                "risk_level": safety_result.output.get("risk_level", "low"),
                "reason": safety_result.output.get("reason", ""),
                "round": adoption_round + 1,
            })
            final_safety_result = safety_result

            if not safety_result.intercepted:
                break

            adoption_round += 1
            adjusted = CoordinatorAgent.adjust_plan(
                current_disposition, safety_result.output,
            )
            current_disposition = adjusted.get("revised_plan", current_disposition)
            self.push_event(trace_id, "plan_adjusted", {
                "round": adoption_round,
                "adjusted": adjusted.get("adjusted", False),
            })

        duration_ms = int((time.time() - start) * 1000)

        # 5. 生成报告（使用可能调整后的处置计划）
        has_intercept = final_safety_result.intercepted if final_safety_result else False
        report = DispositionReport(
            trace_id=trace_id,
            alert_id=alert_dict.get("id", ""),
            conclusion=coord_result.output.get("summary", ""),
            disposition_plan=current_disposition,
            has_safety_intercept=has_intercept,
            safety_reason=final_safety_result.output.get("reason") if final_safety_result else "",
            duration_ms=duration_ms,
            status="intercepted" if has_intercept else "success",
        )

        # 6. MCP 工具后处理（异步入队）
        tool_task_ids = await self._enqueue_post_tasks(trace_id, report)

        # 7. Trace 完成
        trace_manager.finish_trace(trace_id, report.status)

        self.push_event(trace_id, "completed", {
            "trace_id": trace_id,
            "status": report.status,
            "conclusion": report.conclusion,
            "skills": [s.get("skill", "") for s in skill_results],
            "tools": tool_task_ids,
            "adoption_rounds": adoption_round,
        })

        return HarnessResult(
            trace_id=trace_id,
            report=report,
            skill_results=skill_results,
            tool_tasks=tool_task_ids,
            agent_title=coord_result.output.get("title", ""),
            agent_severity=coord_result.output.get("severity", ""),
            agent_error_type=coord_result.output.get("error_type", ""),
            handover_summary=coord_result.output.get("handover_summary", ""),
            adoption_rounds=adoption_round,
            related_cases=coord_result.output.get("related_cases", []),
        )



    # ── Skill 执行 ──────────────────────────────────────────
    async def _execute_skills(
        self,
        trace_id: str,
        error_type: str,
        alert: dict,
    ) -> list[dict]:
        """根据告警 error_type 匹配并执行 Skill"""
        skills = self.skill_registry.get_skills_for_alert(error_type)
        if not skills:
            logger.info("No skills matched for error_type=%s", error_type)
            return []

        results = []
        for skill in skills:
            self.push_event(trace_id, "skill_started", {
                "skill": skill.name,
                "risk_level": skill.risk_level,
            })
            try:
                output = await skill.execute({"error_type": error_type, "alert": alert})
                results.append({
                    "skill": skill.name,
                    "risk_level": skill.risk_level,
                    "status": "executed",
                    "output": output,
                })
                self.push_event(trace_id, "skill_completed", {
                    "skill": skill.name,
                    "status": "executed",
                })
            except Exception as e:
                logger.exception("Skill %s failed: %s", skill.name, e)
                results.append({
                    "skill": skill.name,
                    "risk_level": skill.risk_level,
                    "status": "error",
                    "error": str(e),
                })

        # 高风险 Skill 强制叠加安全处理计划
        safety_plan = self.skill_registry.get_safety_plan(error_type)
        if safety_plan:
            self.push_event(trace_id, "safety_plan_overlay", {
                "skills": [s.name for s in safety_plan],
                "message": "高风险场景，强制叠加安全处理计划",
            })
            for skill in safety_plan:
                try:
                    output = await skill.execute({"error_type": error_type, "alert": alert, "reason": "safety_overlay"})
                    results.append({
                        "skill": skill.name,
                        "risk_level": skill.risk_level,
                        "status": "safety_overlay",
                        "output": output,
                    })
                except Exception as e:
                    logger.exception("Safety skill %s failed: %s", skill.name, e)

        return results



    # ── MCP 工具后处理 ──────────────────────────────────────
    async def _enqueue_post_tasks(
        self,
        trace_id: str,
        report: DispositionReport,
    ) -> list[str]:
        """Agent 处理完成后入队异步 MCP 任务"""
        task_ids = []

        # 自动启动工具队列（如未启动）
        self.start_tool_queue()

        report_data = {
            "trace_id": report.trace_id,
            "alert_id": report.alert_id,
            "summary": report.conclusion,
            "disposition_plan": report.disposition_plan,
            "status": report.status,
            "has_safety_intercept": report.has_safety_intercept,
        }

        # 1. 导出处置报告（JSON）
        export_task = ToolTask(
            name="export_report",
            params={"format": "json", "data": report_data},
            max_retries=2,
        )
        await self.tool_queue.enqueue(export_task)
        task_ids.append(export_task.id)

        # 2. 导出 Excel 报告
        excel_task = ToolTask(
            name="excel_export",
            params={"data": report_data},
            max_retries=2,
        )
        await self.tool_queue.enqueue(excel_task)
        task_ids.append(excel_task.id)

        # 3. 发送通知
        notify_task = ToolTask(
            name="send_notification",
            params={
                "channel": "default",
                "trace_id": report.trace_id,
                "alert_id": report.alert_id,
                "title": f"告警处置完成: {report.alert_id}",
                "message": f"状态: {report.status}, 耗时: {report.duration_ms}ms",
            },
            max_retries=3,
        )
        await self.tool_queue.enqueue(notify_task)
        task_ids.append(notify_task.id)

        # 4. 追加处理备注
        note_task = ToolTask(
            name="append_note",
            params={
                "alert_id": report.alert_id,
                "note": f"Trace {report.trace_id}: {report.status} — {report.conclusion[:200]}",
            },
            max_retries=1,
        )
        await self.tool_queue.enqueue(note_task)
        task_ids.append(note_task.id)

        return task_ids



    # ── 自动 Claim 循环 ────────────────────────────────────
    async def auto_claim_loop(
        self, agent_name: str, trace_id: str
    ) -> None:
        """Agent 自动认领黑板任务的循环"""
        agent = self.build_agent(agent_name, trace_id)
        if agent is None or not agent.claim_types:
            return

        for claim_type in agent.claim_types:
            pending = await self.blackboard.get_pending_tasks(claim_type)
            for task in pending:
                await self.execute_task(agent, task, agent_name, trace_id)

        queue = self.blackboard.subscribe()
        try:
            from app.blackboard.models import BoardEvent

            while True:
                event: BoardEvent = await queue.get()
                if event.event_type == "task_published":
                    task_type = event.task.type
                    if task_type in agent.claim_types:
                        await self.execute_task(agent, event.task, agent_name, trace_id)
                if event.event_type in ("task_completed", "task_failed"):
                    t = await self.blackboard.get_task(event.task.id)
                    if t and t.type == "root":
                        break
        finally:
            self.blackboard.unsubscribe(queue)

    def build_agent(self, agent_name: str, trace_id: str):
        """根据名称构造 Agent 实例"""
        for name, cls in self.runtime.registry.items():
            if name == agent_name:
                ctx = ExecutionContext(
                    trace_id=trace_id,
                    agent_name=agent_name,
                )
                return cls(context=ctx, blackboard=self.blackboard)
        return None

    async def execute_task(
        self,
        agent,
        task,
        agent_name: str,
        trace_id: str,
    ) -> None:
        """认领并执行一个黑板任务"""
        claimed = await self.blackboard.claim_task(task.id, agent_name)
        if not claimed:
            return

        self.push_event(trace_id, "task_claimed", {"agent": agent_name, "task_id": task.id})
        result = await agent.run(task.input)

        artifact = Artifact(
            task_id=task.id,
            agent_name=agent_name,
            type=task.type,
            content=result.output,
        )
        await self.blackboard.complete_task(task.id, artifact)

        self.push_event(trace_id, "artifact_produced", {
            "agent": agent_name,
            "task_id": task.id,
            "summary": list(result.output.keys()),
        })



    # ── Trace ──────────────────────────────────────────────
    def create_trace(
        self,
        alert_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        trace = trace_manager.create_trace(alert_id=alert_id, session_id=session_id)
        return trace.trace_id

    # ── 脱敏 ──────────────────────────────────────────────
    def sanitize(self, data: dict) -> dict:
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                v = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "x.x.x.x", v)
                v = re.sub(r"Host:\s*\S+", "Host: <sanitized>", v)
            result[k] = v
        return result

    # ── 事件存储（SSE 用） ─────────────────────────────────
    def push_event(self, trace_id: str, event_type: str, data: dict) -> None:
        self._event_store.setdefault(trace_id, []).append({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        })
        if trace_id in self._event_queues:
            for q in self._event_queues[trace_id]:
                q.put_nowait(event_type)

    def subscribe_events(self, trace_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        for event in self._event_store.get(trace_id, []):
            q.put_nowait(event["type"])
        self._event_queues.setdefault(trace_id, []).append(q)
        return q

    def get_events(self, trace_id: str) -> list[dict]:
        return self._event_store.get(trace_id, [])
