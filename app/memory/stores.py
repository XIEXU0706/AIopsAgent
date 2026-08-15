"""记忆存储后端：InMemoryStore（开发用） + RedisStore / SqliteStore（生产用）"""


import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class MemoryStore(ABC):
    """记忆存储抽象接口"""

    @abstractmethod
    async def get(self, key: str) -> Optional[dict]: ...

    @abstractmethod
    async def set(self, key: str, value: dict, ttl: int = 0) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...


class InMemoryStore(MemoryStore):
    """内存存储（开发/测试用）"""

    def __init__(self):
        self._data: dict[str, dict] = {}

    async def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    async def set(self, key: str, value: dict, ttl: int = 0) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self._data


class RedisStore(MemoryStore):
    """Redis 存储后端"""

    def __init__(self, redis_url: str = ""):
        self._redis_url = redis_url
        self._redis = None

    async def _get_client(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url or "redis://localhost:6379/0",
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def get(self, key: str) -> Optional[dict]:
        try:
            r = await self._get_client()
            data = await r.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.warning("Redis get failed: %s", e)
            return None

    async def set(self, key: str, value: dict, ttl: int = 3600) -> None:
        try:
            r = await self._get_client()
            await r.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.warning("Redis set failed: %s", e)

    async def delete(self, key: str) -> None:
        try:
            r = await self._get_client()
            await r.delete(key)
        except Exception as e:
            logger.warning("Redis delete failed: %s", e)

    async def exists(self, key: str) -> bool:
        try:
            r = await self._get_client()
            return await r.exists(key) > 0
        except Exception as e:
            logger.warning("Redis exists failed: %s", e)
            return False


class SqliteStore(MemoryStore):
    """SQLite 存储后端：重启不丢失，无需额外服务，开发/单实例生产通用

    同一张表同时存放记忆数据（memory:{session_id}）与会话元数据（session:{id}），
    后者的 key 以 "session:" 前缀区分。
    """

    def __init__(self, db_path: str = ""):
        self._db_path = db_path or str(DATA_DIR / "chat.db")
        self._conn = None
        self._lock = asyncio.Lock()

    async def _get_conn(self):
        if self._conn is None:
            import aiosqlite
            self._conn = await aiosqlite.connect(self._db_path)
            await self._conn.execute(
                """CREATE TABLE IF NOT EXISTS chat_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            await self._conn.commit()
        return self._conn

    async def get(self, key: str) -> Optional[dict]:
        async with self._lock:
            conn = await self._get_conn()
            cur = await conn.execute(
                "SELECT value FROM chat_kv WHERE key = ?", (key,))
            row = await cur.fetchone()
            await cur.close()
            return json.loads(row[0]) if row else None

    async def set(self, key: str, value: dict, ttl: int = 0) -> None:
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute(
                """INSERT INTO chat_kv (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), datetime.now().isoformat()),
            )
            await conn.commit()

    async def delete(self, key: str) -> None:
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute("DELETE FROM chat_kv WHERE key = ?", (key,))
            await conn.commit()

    async def exists(self, key: str) -> bool:
        async with self._lock:
            conn = await self._get_conn()
            cur = await conn.execute("SELECT 1 FROM chat_kv WHERE key = ?", (key,))
            row = await cur.fetchone()
            await cur.close()
            return row is not None

    async def close(self) -> None:
        """关闭连接（释放 aiosqlite 后台线程，否则进程退出会挂住）"""
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    # ── 会话元数据（key = "session:{id}"） ──────────────────────
    async def list_sessions(self) -> list[dict]:
        async with self._lock:
            conn = await self._get_conn()
            cur = await conn.execute(
                "SELECT value FROM chat_kv WHERE key LIKE 'session:%'")
            rows = await cur.fetchall()
            await cur.close()
            return [json.loads(r[0]) for r in rows]


class MySQLStore(MemoryStore):
    """MySQL 存储后端：生产环境长期持久化，作为 Redis 缺失时的回填层

    与 SqliteStore 接口一致（get/set/delete/exists/list_sessions），
    底层使用 aiomysql 异步驱动，连接复用单个 Pool。

    容灾：任意一次 MySQL 操作失败（连不上/库不存在/超时）会自动切换到
    内置 SqliteStore 兜底，保证会话读写不丢；恢复前持续走兜底，不影响主流程。
    """

    def __init__(self, dsn: str = "", fallback: Optional["SqliteStore"] = None):
        # dsn 形如：mysql://user:pass@host:3306/db
        self._dsn = dsn or "mysql://root:123456@localhost:3306/aiopsAgent"
        self._pool = None
        self._lock = asyncio.Lock()
        # 兜底后端：MySQL 不可用时所有读写转交它，避免会话丢失
        self._fallback = fallback or SqliteStore(db_path=str(DATA_DIR / "chat_fallback.db"))

    @staticmethod
    def _parse_dsn(dsn: str) -> dict:
        # 轻量解析，避免引入额外依赖
        m = re.match(r"mysql://([^:]+):([^@]+)@([^:/]+):?(\d*)/(\w+)", dsn)
        if not m:
            raise ValueError(f"无法解析 MySQL DSN: {dsn}")
        return {
            "user": m.group(1),
            "password": m.group(2),
            "host": m.group(3),
            "port": int(m.group(4) or 3306),
            "db": m.group(5),
        }

    @staticmethod
    def _extract_session_id(key: str) -> str:
        """从 memory:{sid} / session:{sid} 等 key 中提取会话 ID，无则留空"""
        for prefix in ("memory:", "session:"):
            if key.startswith(prefix):
                return key[len(prefix):]
        return ""

    async def _get_pool(self):
        if self._pool is None:
            import aiomysql
            cfg = self._parse_dsn(self._dsn)
            self._pool = await aiomysql.create_pool(
                host=cfg["host"], port=cfg["port"], user=cfg["user"],
                password=cfg["password"], db=cfg["db"], autocommit=True,
            )
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    # 热表：承载当前活跃会话；session_id 列便于按会话归档，
                    # updated_at 列加索引便于按时间范围查询/清理
                    await cur.execute(
                        """CREATE TABLE IF NOT EXISTS chat_kv (
                            `key` VARCHAR(255) PRIMARY KEY,
                            session_id VARCHAR(255) NOT NULL DEFAULT '',
                            value LONGTEXT NOT NULL,
                            updated_at VARCHAR(32) NOT NULL,
                            INDEX idx_session (session_id),
                            INDEX idx_updated (updated_at)
                        )"""
                    )
                    # 归档表：结构与热表一致，存放超过保留期的历史会话
                    await cur.execute(
                        """CREATE TABLE IF NOT EXISTS chat_kv_archive (
                            `key` VARCHAR(255) PRIMARY KEY,
                            session_id VARCHAR(255) NOT NULL DEFAULT '',
                            value LONGTEXT NOT NULL,
                            updated_at VARCHAR(32) NOT NULL,
                            archived_at VARCHAR(32) NOT NULL,
                            INDEX idx_session (session_id),
                            INDEX idx_updated (updated_at)
                        )"""
                    )
        return self._pool

    async def get(self, key: str) -> Optional[dict]:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT value FROM chat_kv WHERE `key` = %s", (key,))
                    row = await cur.fetchone()
                    return json.loads(row[0]) if row else None
        except Exception as e:
            logger.warning("MySQL get failed，降级到 SQLite：%s", e)
            return await self._fallback.get(key)

    async def set(self, key: str, value: dict, ttl: int = 0) -> None:
        try:
            pool = await self._get_pool()
            sid = self._extract_session_id(key)
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """INSERT INTO chat_kv (`key`, session_id, value, updated_at)
                           VALUES (%s, %s, %s, %s)
                           ON DUPLICATE KEY UPDATE
                               session_id = VALUES(session_id),
                               value = VALUES(value),
                               updated_at = VALUES(updated_at)""",
                        (key, sid, json.dumps(value, ensure_ascii=False),
                         datetime.now().isoformat()),
                    )
        except Exception as e:
            logger.warning("MySQL set failed，降级到 SQLite：%s", e)
            await self._fallback.set(key, value, ttl)

    async def delete(self, key: str) -> None:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "DELETE FROM chat_kv WHERE `key` = %s", (key,))
        except Exception as e:
            logger.warning("MySQL delete failed，降级到 SQLite：%s", e)
            await self._fallback.delete(key)

    async def exists(self, key: str) -> bool:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT 1 FROM chat_kv WHERE `key` = %s", (key,))
                    return await cur.fetchone() is not None
        except Exception as e:
            logger.warning("MySQL exists failed，降级到 SQLite：%s", e)
            return await self._fallback.exists(key)

    async def list_sessions(self) -> list[dict]:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT value FROM chat_kv WHERE `key` LIKE 'session:%'")
                    rows = await cur.fetchall()
                    return [json.loads(r[0]) for r in rows]
        except Exception as e:
            logger.warning("MySQL list_sessions failed，降级到 SQLite：%s", e)
            return await self._fallback.list_sessions()

    async def archive_old_sessions(self, days: int) -> int:
        """将 updated_at 早于 days 天前的记录移入归档表

        返回归档的记录条数；days<=0 时直接跳过（关闭归档）。
        cutoff 采用 ISO 字符串比较：格式固定为 'YYYY-MM-DDTHH:MM:SS...'，
        字典序即时间序，可直接用于 WHERE 比较。
        """
        if days <= 0:
            return 0
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """INSERT INTO chat_kv_archive
                             (`key`, session_id, value, updated_at, archived_at)
                           SELECT `key`, session_id, value, updated_at, %s
                           FROM chat_kv
                           WHERE updated_at < %s""",
                        (datetime.now().isoformat(), cutoff),
                    )
                    await cur.execute(
                        "DELETE FROM chat_kv WHERE updated_at < %s", (cutoff,))
                    return cur.rowcount
        except Exception as e:
            logger.warning("MySQL archive_old_sessions failed: %s", e)
            return 0
