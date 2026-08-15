"""Agent 执行上下文"""


import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExecutionContext:
    """一次 Agent 调用的完整上下文"""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_span_id: Optional[str] = None  # 父级标识符
    agent_name: str = ""
    alert: Optional[dict] = None   # 要处理的告警数据
    session_id: Optional[str] = None
    extra: dict = field(default_factory=dict)
