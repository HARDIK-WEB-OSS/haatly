import asyncio
import contextlib
from typing import Any

import asyncpg

WAKEUP_CHANNEL = "haatly_wakeup"
SCHEDULER_LOCK_KEY = 7_201_100_001


class PgWakeup:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.signal = asyncio.Event()
        self.connection: Any = None

    def on_notify(self, *_: object) -> None:
        self.signal.set()

    async def ensure_listening(self) -> None:
        if self.connection is not None and not self.connection.is_closed():
            return
        self.connection = await asyncpg.connect(self.dsn)
        await self.connection.add_listener(WAKEUP_CHANNEL, self.on_notify)

    async def wait(self, max_wait: float) -> None:
        with contextlib.suppress(OSError, asyncpg.PostgresError):
            await self.ensure_listening()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.signal.wait(), max_wait)
        self.signal.clear()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None


class PgLeaderLock:
    def __init__(self, dsn: str, key: int = SCHEDULER_LOCK_KEY) -> None:
        self.dsn = dsn
        self.key = key
        self.connection: Any = None
        self.held = False

    async def still_connected(self) -> bool:
        if self.connection is None or self.connection.is_closed():
            return False
        try:
            await self.connection.execute("SELECT 1")
        except (OSError, asyncpg.PostgresError):
            return False
        return True

    async def try_acquire(self) -> bool:
        if self.held and await self.still_connected():
            return True
        await self.release()
        self.connection = await asyncpg.connect(self.dsn)
        self.held = bool(
            await self.connection.fetchval("SELECT pg_try_advisory_lock($1)", self.key)
        )
        return self.held

    async def release(self) -> None:
        connection, self.connection, self.held = self.connection, None, False
        if connection is not None and not connection.is_closed():
            await connection.close()
