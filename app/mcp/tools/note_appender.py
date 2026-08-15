"""备注追加工具：将处理备注真实追加到告警记录的 notes 字段"""

import logging

from app.services.alert_store import store as alert_store

logger = logging.getLogger(__name__)


async def append_note(params: dict) -> dict:
    """追加处理备注到告警记录（alerts.notes）"""
    alert_id = params.get("alert_id", "")
    note = params.get("note", "")

    if not alert_id:
        return {"alert_id": "", "note_length": 0, "status": "missing_alert_id", "persisted": False}

    ok = alert_store.append_note(alert_id, note)
    status = "appended" if ok else "alert_not_found"
    logger.info("[NOTE] alert_id=%s status=%s note=%s", alert_id, status, note[:80])

    return {
        "alert_id": alert_id,
        "note_length": len(note),
        "status": status,
        "persisted": ok,
    }
