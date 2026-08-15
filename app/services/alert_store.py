"""告警存储服务 —— SQLite 实现"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "alerts.db"


@dataclass
class AlertRecord:
    """一条告警记录（含处理结果）"""
    id: str = ""
    title: str = ""
    source: str = ""
    severity: str = "warning"
    error_type: str = ""
    message: str = ""
    status: str = "processing"  # processing | completed | error
    trace_id: str = ""
    create_time: datetime = field(default_factory=datetime.now)

    # 处理结果（status=completed 时填充）
    conclusion: str = ""
    disposition_plan: str = ""
    has_safety_intercept: bool = False
    safety_reason: str = ""
    duration_ms: int = 0
    skill_results: list[dict] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)
    handover_summary: str = ""
    adoption_rounds: int = 0
    related_cases: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


class AlertStore:
    """告警存储（SQLite 实现）"""

    def __init__(self, db_path: str = str(DB_PATH)):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                source TEXT DEFAULT '',
                severity TEXT DEFAULT 'warning',
                error_type TEXT DEFAULT '',
                message TEXT DEFAULT '',
                status TEXT DEFAULT 'processing',
                trace_id TEXT DEFAULT '',
                create_time TEXT DEFAULT '',
                conclusion TEXT DEFAULT '',
                disposition_plan TEXT DEFAULT '',
                has_safety_intercept INTEGER DEFAULT 0,
                safety_reason TEXT DEFAULT '',
                duration_ms INTEGER DEFAULT 0,
                skill_results TEXT DEFAULT '[]',
                raw_data TEXT DEFAULT '{}'
            )
        """)
        # 兼容已有表（升级时加列）
        try:
            self._conn.execute("ALTER TABLE alerts ADD COLUMN raw_data TEXT DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass  # 列已存在
        try:
            self._conn.execute("ALTER TABLE alerts ADD COLUMN handover_summary TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE alerts ADD COLUMN adoption_rounds INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE alerts ADD COLUMN related_cases TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE alerts ADD COLUMN notes TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()


    # ── 写入告警数据 ──────────────────────────────────────────
    def create(self, data: dict) -> AlertRecord:
        raw_data = data.get("raw_data", {})
        record = AlertRecord(
            id=data.get("id") or f"alert-{uuid.uuid4().hex[:8]}",
            title=data.get("title", ""),
            source=data.get("source", ""),
            severity=data.get("severity", "warning"),
            error_type=data.get("error_type", ""),
            message=data.get("message", ""),
            status="processing",
            create_time=datetime.now(),
            raw_data=raw_data,
        )
        self._conn.execute(
            """INSERT INTO alerts
               (id, title, source, severity, error_type, message, status, create_time, raw_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.id, record.title, record.source, record.severity,
             record.error_type, record.message,
             record.status, record.create_time.isoformat(),
             json.dumps(raw_data, ensure_ascii=False)),
        )
        self._conn.commit()
        return record


    # ──完成任务之后更新数据 ──────────────────────────────────────────
    def complete(self, alert_id: str, trace_id: str, report, skill_results: Optional[list[dict]] = None,
                 agent_title: str = "", agent_severity: str = "", agent_error_type: str = "",
                 handover_summary: str = "", adoption_rounds: int = 0,
                 related_cases: Optional[list[dict]] = None) -> None:
        self._conn.execute(
            """UPDATE alerts SET
               status='completed', trace_id=?, conclusion=?, disposition_plan=?,
               has_safety_intercept=?, safety_reason=?, duration_ms=?,
               skill_results=?, title=?, severity=?, error_type=?,
               handover_summary=?, adoption_rounds=?, related_cases=?
               WHERE id=?""",
            (trace_id, report.conclusion, report.disposition_plan,
             int(report.has_safety_intercept), report.safety_reason or "",
             report.duration_ms,
             json.dumps(skill_results or [], ensure_ascii=False),
             agent_title or "", agent_severity or "", agent_error_type or "",
             handover_summary, adoption_rounds,
             json.dumps(related_cases or [], ensure_ascii=False),
             alert_id),
        )
        self._conn.commit()

    def set_trace(self, alert_id: str, trace_id: str) -> None:
        """提前写入 trace_id，便于 SSE 连接"""
        self._conn.execute(
            "UPDATE alerts SET trace_id=? WHERE id=?", (trace_id, alert_id)
        )
        self._conn.commit()

    def fail(self, alert_id: str, error: str = "") -> None:
        self._conn.execute("UPDATE alerts SET status='error' WHERE id=?", (alert_id,))
        self._conn.commit()

    def append_note(self, alert_id: str, note: str) -> bool:
        """向告警记录追加一条处理备注；记录不存在返回 False"""
        record = self.get(alert_id)
        if record is None:
            return False
        notes = list(record.notes)
        notes.append({
            "content": note,
            "created_at": datetime.now().isoformat(),
        })
        self._conn.execute(
            "UPDATE alerts SET notes=? WHERE id=?",
            (json.dumps(notes, ensure_ascii=False), alert_id),
        )
        self._conn.commit()
        return True


    # ── 查询 ──────────────────────────────────────────
    def list(self) -> list[AlertRecord]:
        rows = self._conn.execute(
            "SELECT * FROM alerts ORDER BY create_time DESC"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get(self, alert_id: str) -> Optional[AlertRecord]:
        row = self._conn.execute(
            "SELECT * FROM alerts WHERE id=?", (alert_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None
    def get_by_trace_id(self, trace_id: str) -> Optional[AlertRecord]:
        row = self._conn.execute(
            "SELECT * FROM alerts WHERE trace_id=?", (trace_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None


    # ── 处理每一行数据 ──────────────────────────────────────────
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AlertRecord:
        d = dict(row)
        return AlertRecord(
            id=d["id"],
            title=d["title"],
            source=d["source"],
            severity=d["severity"],
            error_type=d["error_type"],
            message=d["message"],
            status=d["status"],
            trace_id=d.get("trace_id", ""),
            create_time=datetime.fromisoformat(d["create_time"]) if d.get("create_time") else datetime.now(),
            conclusion=d.get("conclusion", ""),
            disposition_plan=d.get("disposition_plan", ""),
            has_safety_intercept=bool(d.get("has_safety_intercept", 0)),
            safety_reason=d.get("safety_reason", ""),
            duration_ms=d.get("duration_ms", 0),
            skill_results=json.loads(d.get("skill_results", "[]")),
            raw_data=json.loads(d.get("raw_data", "{}")),
            handover_summary=d.get("handover_summary", ""),
            adoption_rounds=d.get("adoption_rounds", 0),
            related_cases=json.loads(d.get("related_cases", "[]")),
            notes=json.loads(d.get("notes", "[]")),
        )


# 全局单例
store = AlertStore()
