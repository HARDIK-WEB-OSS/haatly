import asyncio
from dataclasses import dataclass

from haatly.platform.app.jobs import JobRunner
from haatly.platform.app.outbox import OutboxRelay
from haatly.platform.app.ports import Wakeup
from haatly.platform.app.scheduler import Scheduler


@dataclass
class Worker:
    relay: OutboxRelay
    runner: JobRunner
    scheduler: Scheduler
    wakeup: Wakeup
    idle_seconds: float = 5.0

    async def run_once(self) -> int:
        scheduled = await self.scheduler.tick()
        relayed = await self.relay.relay_batch()
        executed = await self.runner.run_batch()
        return scheduled + relayed + executed

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                if await self.run_once() == 0:
                    await self.wakeup.wait(self.idle_seconds)
        finally:
            await self.scheduler.resign()
