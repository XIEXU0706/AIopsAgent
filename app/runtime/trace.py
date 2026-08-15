"""Trace 管理 —— 全链路调用追踪"""


from datetime import datetime
from typing import Optional

from app.models.trace import RunTrace


class TraceManager:
    """管理 Trace 的创建"""

    def __init__(self):
        self._traces: dict[str, RunTrace] = {}

    def create_trace(
        self,
        alert_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> RunTrace:
        trace = RunTrace(alert_id=alert_id, session_id=session_id)
        self._traces[trace.trace_id] = trace
        return trace

    def finish_trace(self, trace_id: str, status: str = "completed") -> Optional[RunTrace]:
        trace = self._traces.get(trace_id)
        if trace:
            trace.status = status
            trace.end_time = datetime.now()
        return trace

    def get_trace(self, trace_id: str) -> Optional[RunTrace]:
        return self._traces.get(trace_id)


# 全局单例
trace_manager = TraceManager()
