"""告警事件 API 路由"""


import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.harness import AIOpsAgentHarness
from app.models.severity import normalize_severity
from app.services.alert_store import AlertRecord, store as alert_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

# 全局 Harness 实例（应用启动时初始化）
harness: AIOpsAgentHarness | None = None


def init_harness(h: AIOpsAgentHarness) -> None:
    global harness
    harness = h


class AlertRequest(BaseModel):
    id: str = ""
    source: str = "prometheus"
    raw_data: dict = {}


class AlertResponse(BaseModel):
    alert_id: str
    status: str
    events_url: str = ""


class AlertListItem(BaseModel):
    id: str
    title: str
    source: str
    severity: str
    error_type: str
    message: str
    status: str  # processing | completed | error
    trace_id: str = ""
    create_time: datetime

    # 处理结果（仅 completed 时有值）
    conclusion: str = ""
    disposition_plan: str = ""
    has_safety_intercept: bool = False
    safety_reason: str = ""
    duration_ms: int = 0
    safety_overlays: list[dict] = []
    raw_data: dict = {}
    handover_summary: str = ""
    adoption_rounds: int = 0
    related_cases: list[dict] = []
    notes: list[dict] = []


def record_to_item(r: AlertRecord) -> AlertListItem:
    return AlertListItem(
        id=r.id,
        title=r.title,
        source=r.source,
        severity=normalize_severity(r.severity),
        error_type=r.error_type,
        message=r.message,
        status=r.status,
        trace_id=r.trace_id,
        create_time=r.create_time,
        conclusion=r.conclusion,
        disposition_plan=r.disposition_plan,
        has_safety_intercept=r.has_safety_intercept,
        safety_reason=r.safety_reason,
        duration_ms=r.duration_ms,
        safety_overlays=[s for s in r.skill_results if s.get("status") == "safety_overlay"],
        raw_data=r.raw_data,
        handover_summary=r.handover_summary,
        adoption_rounds=r.adoption_rounds,
        related_cases=r.related_cases,
        notes=r.notes,
    )


@router.post("", response_model=AlertResponse, status_code=202)
async def ingest_alert(request: AlertRequest, background: BackgroundTasks):
    """接收告警事件，保存并启动异步处置流程"""
    return submit_alert(request.model_dump(), background)


def submit_alert(payload: dict, background: BackgroundTasks) -> AlertResponse:
    """告警接入核心逻辑：落库 + 建 trace + 后台异步处置。

    供 POST /api/v1/alerts 与 Alertmanager webhook 复用。
    """
    if harness is None:
        raise HTTPException(status_code=503, detail="harness not initialized")

    # 1. 立即保存告警（状态: processing）；主键冲突（重复推送）时换新 id 重试一次
    for attempt in range(2):
        try:
            alert = alert_store.create(payload)
            break
        except Exception:
            if attempt == 1:
                raise
            payload = {**payload, "id": ""}  # 清空 id 让 store 自动生成

    # 2. 提前创建 trace_id，写入 alert 便于前端 SSE 连接
    trace_id = harness.create_trace(alert.id)
    alert_store.set_trace(alert.id, trace_id)

    # 3. 后台执行完整处置链路
    async def process():
        try:
            result = await harness.process_alert(payload, trace_id=trace_id)
            # 4. 处理完成，更新存储
            alert_store.complete(alert.id, result.trace_id, result.report,
                                 skill_results=result.skill_results,
                                 agent_title=result.agent_title,
                                 agent_severity=result.agent_severity,
                                 agent_error_type=result.agent_error_type,
                                 handover_summary=result.handover_summary,
                                 adoption_rounds=result.adoption_rounds,
                                 related_cases=result.related_cases)
            logger.info(
                "Alert %s processed: trace=%s status=%s",
                alert.id,
                result.trace_id,
                result.report.status,
            )
        except Exception as e:
            alert_store.fail(alert.id, str(e))
            logger.exception("Alert %s processing failed", alert.id)

    background.add_task(process)

    return AlertResponse(
        alert_id=alert.id,
        status="accepted",
        events_url=f"/api/v1/alerts/{trace_id}/events",
    )


@router.get("", response_model=list[AlertListItem])
async def list_alerts():
    """获取告警列表（含处理状态和结果）"""
    records = alert_store.list()
    return [record_to_item(r) for r in records]


@router.get("/{alert_id}", response_model=AlertListItem)
async def get_alert(alert_id: str):
    """获取单条告警详情（含处理结果）"""
    record = alert_store.get(alert_id)
    if not record:
        raise HTTPException(status_code=404, detail="alert not found")
    return record_to_item(record)


@router.get("/{trace_id}/events")
async def stream_events(trace_id: str):
    """SSE 事件流"""
    if harness is None:
        return {"error": "harness not initialized"}

    async def event_generator():
        for event in harness.get_events(trace_id):
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

        q = harness.subscribe_events(trace_id)
        while True:
            event_type = await q.get()
            events = harness.get_events(trace_id)
            if events:
                event = events[-1]
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event_type == "completed":
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")

