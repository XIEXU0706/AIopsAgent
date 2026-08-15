"""通知存储服务 —— SQLite 实现（预警发送落库）"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "notifications.db"


@dataclass
class NotificationRecord:
    id: str = ""
    trace_id: str = ""
    alert_id: str = ""
    channel: str = "default"
    title: str = ""
    message: str = ""
    status: str = "sent"  # sent | queued | failed
    webhook_status: str = ""
    create_time: datetime = field(default_factory=datetime.now)


class NotificationStore:
    """通知记录存储（SQLite）"""

    def __init__(self, db_path: str = str(DB_PATH)):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                trace_id TEXT DEFAULT '',
                alert_id TEXT DEFAULT '',
                channel TEXT DEFAULT 'default',
                title TEXT DEFAULT '',
                message TEXT DEFAULT '',
                status TEXT DEFAULT 'sent',
                webhook_status TEXT DEFAULT '',
                create_time TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    def create(self, record: NotificationRecord) -> NotificationRecord:
        if not record.id:
            record.id = f"notify-{uuid.uuid4().hex[:8]}"
        self._conn.execute(
            """INSERT INTO notifications
               (id, trace_id, alert_id, channel, title, message, status, webhook_status, create_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.id, record.trace_id, record.alert_id, record.channel,
             record.title, record.message, record.status, record.webhook_status,
             record.create_time.isoformat()),
        )
        self._conn.commit()
        return record

    def list(self) -> list[NotificationRecord]:
        rows = self._conn.execute(
            "SELECT * FROM notifications ORDER BY create_time DESC"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> NotificationRecord:
        d = dict(row)
        return NotificationRecord(
            id=d["id"],
            trace_id=d.get("trace_id", ""),
            alert_id=d.get("alert_id", ""),
            channel=d.get("channel", "default"),
            title=d.get("title", ""),
            message=d.get("message", ""),
            status=d.get("status", "sent"),
            webhook_status=d.get("webhook_status", ""),
            create_time=datetime.fromisoformat(d["create_time"]) if d.get("create_time") else datetime.now(),
        )


# 全局单例
notification_store = NotificationStore()
