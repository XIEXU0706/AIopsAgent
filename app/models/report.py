"""处置报告模型"""


import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class DispositionReport:
    """一次告警处置的完整报告"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    alert_id: str = ""
    conclusion: str = ""
    disposition_plan: str = ""
    has_safety_intercept: bool = False
    safety_reason: Optional[str] = None
    agent_traces: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    status: str = "success"  # success | intercepted | error
    created_at: datetime = field(default_factory=datetime.now)
