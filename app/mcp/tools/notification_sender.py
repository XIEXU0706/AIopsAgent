"""通知发送工具：落库 + 可选真实 Webhook 推送"""

import logging

import httpx

from app.config import settings
from app.services.notification_store import NotificationRecord, notification_store

logger = logging.getLogger(__name__)


async def _deliver_webhook(payload: dict) -> str:
    """向配置的 Webhook 地址真实 POST；失败不抛异常（保持失败隔离）"""
    url = settings.notification_webhook_url
    if not url:
        return "not_configured"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload)
        logger.info("[NOTIFICATION] webhook %s -> %s", url, resp.status_code)
        return str(resp.status_code)
    except Exception as e:
        logger.warning("[NOTIFICATION] webhook failed: %s", e)
        return "error"


async def send_notification(params: dict) -> dict:
    """发送处置完成通知：写入 notifications 表，配置了 Webhook 则真实推送"""
    channel = params.get("channel", "default")
    title = params.get("title", "")
    message = params.get("message", "")

    payload = {
        "channel": channel,
        "title": title,
        "message": message,
        "trace_id": params.get("trace_id", ""),
        "alert_id": params.get("alert_id", ""),
        "sent_at": __import__("datetime").datetime.now().isoformat(),
    }

    webhook_status = await _deliver_webhook(payload)

    record = notification_store.create(NotificationRecord(
        trace_id=params.get("trace_id", ""),
        alert_id=params.get("alert_id", ""),
        channel=channel,
        title=title,
        message=message,
        status="sent",
        webhook_status=webhook_status,
    ))

    return {
        "channel": channel,
        "title": title,
        "message": message,
        "status": "sent",
        "record_id": record.id,
        "webhook_status": webhook_status,
        "persisted": True,
    }
