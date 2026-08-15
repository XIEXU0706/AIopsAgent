"""
CollaborationBlackboard —— 多 Agent 协作的事件驱动黑板

设计思路（HEARSAY-II Blackboard Pattern）：
  1. CoordinatorAgent 向黑板发布 Task
  2. 各 Specialist Agent 根据自己的能力 Claim 任务
  3. 执行完成后将结果以 Artifact 形式写回黑板
  4. 其他 Agent 监听黑板事件，驱动下一步协作

线程安全：
  - 所有公开方法都通过 asyncio.Lock 保护
  - 事件通知通过 asyncio.Queue 实现，支持多消费者
"""

import asyncio
import logging
from typing import Optional

from app.blackboard.models import Artifact, BoardEvent, Task, TaskStatus

logger = logging.getLogger(__name__)


class CollaborationBlackboard:
    """事件驱动的多 Agent 协作黑板"""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._lock = asyncio.Lock()
        self._subscribers: list[asyncio.Queue] = []

    # ── Task 操作 ───────────────────────────────────────────

    async def publish_task(self, task: Task) -> str:
        """发布一个任务到黑板，触发 task_published 事件"""
        async with self._lock:
            self._tasks[task.id] = task
        await self._emit(BoardEvent(event_type="task_published", task=task))
        logger.info("Task published: %s (type=%s)", task.id, task.type)
        return task.id

    async def claim_task(self, task_id: str, agent_name: str) -> bool:
        """Agent 认领一个 PENDING 任务（原子操作）

        Returns:
            True 认领成功, False 已被别人认领
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.PENDING:
                return False
            task.status = TaskStatus.CLAIMED
            task.claimed_by = agent_name
            task.updated_at = __import__("datetime").datetime.now()
        await self._emit(BoardEvent(event_type="task_claimed", task=task))
        logger.info("Task claimed: %s by %s", task_id, agent_name)
        return True

    async def complete_task(self, task_id: str, artifact: Artifact) -> None:
        """完成任务并产出 Artifact"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")
            task.status = TaskStatus.COMPLETED
            task.output = artifact.content
            task.artifact_ids.append(artifact.id)
            task.updated_at = __import__("datetime").datetime.now()
            self._artifacts[artifact.id] = artifact
        await self._emit(BoardEvent(event_type="task_completed", task=task))
        logger.info("Task completed: %s (artifact=%s)", task_id, artifact.id)

    async def fail_task(self, task_id: str, error: str) -> None:
        """标记任务失败"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED
            task.output = {"error": error}
            task.updated_at = __import__("datetime").datetime.now()
        await self._emit(BoardEvent(event_type="task_failed", task=task))
        logger.error("Task failed: %s error=%s", task_id, error)

    # ── 读取 ───────────────────────────────────────────────

    async def get_task(self, task_id: str) -> Optional[Task]:
        async with self._lock:
            return self._tasks.get(task_id)

    async def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        async with self._lock:
            return self._artifacts.get(artifact_id)

    async def get_pending_tasks(self, task_type: Optional[str] = None) -> list[Task]:
        """获取所有 PENDING 状态的任务，可选按类型过滤"""
        async with self._lock:
            tasks = [
                t for t in self._tasks.values()
                if t.status == TaskStatus.PENDING
            ]
            if task_type:
                tasks = [t for t in tasks if t.type == task_type]
            return tasks

    def has_tasks_of_type(self, task_type: str) -> bool:
        """同步检查是否有某类型的 PENDING 任务（用于 Agent 快速判断）"""
        return any(
            t.type == task_type and t.status == TaskStatus.PENDING
            for t in self._tasks.values()
        )

    # ── 事件订阅 ───────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """订阅黑板事件，返回一个 asyncio.Queue"""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def _emit(self, event: BoardEvent) -> None:
        """向所有订阅者广播事件"""
        for q in self._subscribers:
            q.put_nowait(event)
