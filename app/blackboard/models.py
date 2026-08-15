"""黑板数据模型：Task / Artifact / BoardEvent"""


import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """黑板上的一个任务"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""  # log_analysis | knowledge_retrieval | safety_check
    status: TaskStatus = TaskStatus.PENDING
    input: dict = field(default_factory=dict)
    output: Optional[dict] = None
    claimed_by: Optional[str] = None
    artifact_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Artifact:
    """Agent 产出的工作产物"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    agent_name: str = ""
    type: str = ""
    content: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class BoardEvent:
    """黑板事件"""
    event_type: str  # task_published | task_claimed | task_completed | task_failed
    task: Task
    timestamp: datetime = field(default_factory=datetime.now)
