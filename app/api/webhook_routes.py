"""Alertmanager Webhook 对接 —— 接收 Prometheus Alertmanager 推送的标准告警

对接方式（Alertmanager alertmanager.yml）：
    receivers:
      - name: "mindbridge"
        webhook_configs:
          - url: "http://<host>:9092/api/v1/webhook/alertmanager"
            send_resolved: true

转换规则：
  - 每条 firing 告警转换为内部告警格式（source=alertmanager），
    labels/annotations 保留在 raw_data 中，供 RetrievalAgent 提取检索关键词
  - resolved（恢复）通知不触发处置链路，仅计数返回
  - 重复 fingerprint（同一告警重复推送）自动换新 id 入库，不报错
"""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from app.api.alert_routes import submit_alert

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhook", tags=["webhook"])


class AlertmanagerPayload(BaseModel):
    """Alertmanager v4 webhook payload（字段宽松兼容，多余字段忽略）"""

    version: str = "4"
    status: str = "firing"
    receiver: str = ""
    groupKey: str = ""
    truncatedAlerts: int = 0
    commonLabels: dict = {}
    commonAnnotations: dict = {}
    externalURL: str = ""
    alerts: list[dict] = []


def _convert_alert(item: dict, external_url: str) -> dict:
    """把一条 Alertmanager alert 转换为内部告警格式"""
    labels = item.get("labels") or {}
    annotations = item.get("annotations") or {}
    fingerprint = item.get("fingerprint") or f"am-{uuid.uuid4().hex[:12]}"
    alertname = labels.get("alertname", "")
    summary = annotations.get("summary", "") or annotations.get("description", "")

    return {
        "id": fingerprint,
        "source": "alertmanager",
        "title": alertname,
        "message": summary or alertname,
        # labels/annotations 完整保留：RetrievalAgent 会提取 labels 值做检索关键词
        "raw_data": {
            "alertname": alertname,
            "status": item.get("status", "firing"),
            "labels": labels,
            "annotations": annotations,
            "startsAt": item.get("startsAt", ""),
            "endsAt": item.get("endsAt", ""),
            "generatorURL": item.get("generatorURL", ""),
            "externalURL": external_url,
            "groupKey": item.get("groupKey", ""),
        },
    }


@router.post("/alertmanager", status_code=202)
async def alertmanager_webhook(payload: AlertmanagerPayload, background: BackgroundTasks):
    """接收 Alertmanager webhook 推送，逐条转换并进入多 Agent 处置链路"""
    accepted, skipped = 0, 0

    for item in payload.alerts:
        # 恢复通知不触发处置（告警闭环由监控侧负责）
        if item.get("status") == "resolved":
            skipped += 1
            continue
        try:
            submit_alert(_convert_alert(item, payload.externalURL), background)
            accepted += 1
        except Exception as e:
            logger.exception("Alertmanager alert 接入失败: %s", e)

    logger.info(
        "Alertmanager webhook: accepted=%d skipped_resolved=%d (groupKey=%s)",
        accepted, skipped, payload.groupKey,
    )
    return {
        "status": "ok",
        "accepted": accepted,
        "skipped_resolved": skipped,
    }
