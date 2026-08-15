"""通知记录 API 路由（预警发送落库可查）"""

from fastapi import APIRouter

from app.services.notification_store import notification_store

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
async def list_notifications():
    """获取通知发送记录列表"""
    return [{
        "id": n.id,
        "trace_id": n.trace_id,
        "alert_id": n.alert_id,
        "channel": n.channel,
        "title": n.title,
        "message": n.message,
        "status": n.status,
        "webhook_status": n.webhook_status,
        "create_time": n.create_time.isoformat(),
    } for n in notification_store.list()]
