"""运行 Trace 模型"""


import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RunTrace:
    """一条全链路 Trace"""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    alert_id: Optional[str] = None
    session_id: Optional[str] = None
    spans: list[dict] = field(default_factory=list)
    status: str = "running"  # running | completed | failed
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
