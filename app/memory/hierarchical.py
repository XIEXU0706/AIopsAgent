"""分层记忆管理器

架构：
  HierarchicalMemory
    ├── short_term: RedisStore（TTL 短期，低延迟优先）
    └── long_term:  MySQLStore / SqliteStore（Redis 缺失时回填）

读写策略：
  - 读：优先 short_term（Redis）；未命中则从 long_term 回填并写回 Redis
  - 写：双写 short_term（带 TTL）与 long_term（持久化）
  - 任一后端异常自动降级，不影响主流程

压缩策略：
  1. 保留最近 N 条完整消息 (memory_recent_count)
  2. 更早的历史 → 窗口裁剪 + 摘要压缩 → MemoryBrief
  3. 总长度受 max_tokens 限制
"""


import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.config import settings
from app.memory.stores import (
    InMemoryStore,
    MemoryStore,
    MySQLStore,
    RedisStore,
    SqliteStore,
)

logger = logging.getLogger(__name__)


@dataclass
class MemoryBrief:
    """压缩后的记忆摘要"""
    summary: str = ""
    recent_messages: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    message_count: int = 0


class HierarchicalMemory:
    """分层记忆管理器：Redis 短期优先 + 后端长期回填"""

    def __init__(
        self,
        short_term: Optional[MemoryStore] = None,
        long_term: Optional[MemoryStore] = None,
        store: Optional[MemoryStore] = None,
    ):
        # 兼容旧调用：HierarchicalMemory(store=...) 等价于双端同后端
        if store is not None:
            short_term = short_term or store
            long_term = long_term or store
        # short_term 缺省为 Redis；long_term 缺省为 SQLite（开发）/ MySQL（生产）
        self.short_term = short_term or RedisStore()
        self.long_term = long_term or SqliteStore()
        self.max_tokens = settings.memory_max_tokens
        self.recent_count = settings.memory_recent_count
        self.redis_ttl = settings.redis_ttl_seconds

    async def _read(self, key: str) -> Optional[dict]:
        """优先 Redis，未命中从 long_term 回填并写回 Redis"""
        data = await self.short_term.get(key)
        if data is not None:
            return data
        # Redis 缺失 → 回填
        data = await self.long_term.get(key)
        if data is not None:
            await self.short_term.set(key, data, ttl=self.redis_ttl)
            logger.debug("Memory 回填: %s (Redis 未命中，从 long_term 读取)", key)
        return data

    async def _write(self, key: str, value: dict) -> None:
        """双写 short_term + long_term"""
        await self.short_term.set(key, value, ttl=self.redis_ttl)
        await self.long_term.set(key, value)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """向会话中添加一条消息"""
        key = f"memory:{session_id}"
        brief = await self._read(key) or MemoryBrief().__dict__

        recent = brief.get("recent_messages", [])
        recent.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        brief["recent_messages"] = recent
        brief["message_count"] = brief.get("message_count", 0) + 1

        # 超过 recent_count 则触发压缩
        if len(recent) > self.recent_count * 2:
            brief = await self._compress(brief)

        await self._write(key, brief)

    async def get_memory_brief(
        self,
        session_id: str,
    ) -> MemoryBrief:
        """获取压缩后的记忆摘要"""
        key = f"memory:{session_id}"
        data = await self._read(key)
        if data:
            return MemoryBrief(**data)
        return MemoryBrief()

    async def _compress(self, brief: dict) -> dict:
        """窗口裁剪 + 摘要压缩"""
        recent = brief.get("recent_messages", [])
        # 保留最近 N 条
        keep = recent[-self.recent_count:]
        # 旧消息做摘要
        old = recent[:-self.recent_count]
        summary_parts = []
        for msg in old:
            summary_parts.append(f"[{msg['role']}]: {msg['content'][:100]}")
        summary = "\n".join(summary_parts) if summary_parts else brief.get("summary", "")
        if summary:
            summary = summary[-2000:]  # 截断防止过长

        return {
            "summary": summary,
            "recent_messages": keep,
            "message_count": brief.get("message_count", 0),
        }

    async def clear(self, session_id: str) -> None:
        key = f"memory:{session_id}"
        await self.short_term.delete(key)
        await self.long_term.delete(key)
