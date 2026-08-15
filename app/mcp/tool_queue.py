"""MCP 异步工具队列

特性：
  - 幂等创建（同 id 不重复入队）
  - 令牌桶限流
  - 指数退避重试
  - Dead Letter Queue
"""


import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ToolStatus(str, Enum):
    PENDING = "pending"  # 等待执行
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


@dataclass
class ToolTask:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    params: dict = field(default_factory=dict)
    status: ToolStatus = ToolStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ToolResult:
    success: bool = True
    data: Any = None
    error: str = ""


# 令牌桶
class TokenBucket:
    def __init__(self, rate: float = 10, burst: int = 20):
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last_refill = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class AsyncToolQueue:
    """异步工具队列"""
    def __init__(self, rate: float = 10, burst: int = 20, max_dlq: int = 100):
        self._queue: asyncio.Queue[ToolTask] = asyncio.Queue()
        self._completed: dict[str, ToolResult] = {}
        self._rate_limiter = TokenBucket(rate, burst)
        self._dlq: list[ToolTask] = []
        self._max_dlq = max_dlq
        self._handlers: dict[str, Callable] = {}
        self._processed_ids: set[str] = set()  # 幂等
        self._running = False

    def register_handler(self, tool_name: str, handler: Callable) -> None:
        self._handlers[tool_name] = handler

    async def enqueue(self, task: ToolTask) -> bool:
        """幂等入队"""
        if task.id in self._processed_ids:
            return False
        self._processed_ids.add(task.id)
        await self._queue.put(task)
        logger.info("Task enqueued: %s (%s)", task.id, task.name)
        return True

    async def process_loop(self):
        """持续处理队列（在后台任务中运行）"""
        self._running = True
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._process(task)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.exception("Process loop error: %s", e)

    def stop(self):
        self._running = False

    async def _process(self, task: ToolTask) -> None:
        if not self._rate_limiter.allow():
            # 限流，重新入队
            await asyncio.sleep(0.5)
            await self._queue.put(task)
            return

        handler = self._handlers.get(task.name)
        if handler is None:
            self._completed[task.id] = ToolResult(success=False, error=f"No handler for {task.name}")
            return

        task.status = ToolStatus.RUNNING
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(task.params)
            else:
                result = handler(task.params)

            task.status = ToolStatus.COMPLETED
            task.result = result if isinstance(result, dict) else {"data": str(result)}
            self._completed[task.id] = ToolResult(success=True, data=task.result)
            logger.info("Task completed: %s", task.id)
        except Exception as e:
            task.retry_count += 1
            task.error = str(e)
            if task.retry_count <= task.max_retries:
                # 指数退避：2^retry 秒
                delay = min(2 ** task.retry_count, 30)
                logger.warning("Task %s failed (retry %d/%d), retrying in %ds: %s",
                               task.id, task.retry_count, task.max_retries, delay, e)
                task.status = ToolStatus.PENDING
                await asyncio.sleep(delay)
                await self._queue.put(task)
            else:
                task.status = ToolStatus.DEAD
                self._dlq.append(task)
                if len(self._dlq) > self._max_dlq:
                    self._dlq.pop(0)
                self._completed[task.id] = ToolResult(success=False, error=str(e))
                logger.error("Task %s moved to DLQ after %d retries: %s",
                             task.id, task.max_retries, e)

    def get_result(self, task_id: str) -> Optional[ToolResult]:
        return self._completed.get(task_id)

    def get_dlq(self) -> list[ToolTask]:
        return list(self._dlq)
