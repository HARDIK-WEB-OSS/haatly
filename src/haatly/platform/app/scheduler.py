from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from haatly.platform.app.ports import IdSource, LeaderLock, UnitOfWorkFactory
from haatly.platform.domain.clock import Clock
from haatly.platform.domain.jobs import JsonObject, NewJob
from haatly.platform.domain.tenancy import SYSTEM


@dataclass(frozen=True)
class PeriodicTask:
    name: str
    every: timedelta
    kind: str
    payload: JsonObject = field(default_factory=dict)

    def slot(self, now: datetime) -> int:
        return int(now.timestamp() // self.every.total_seconds())

    def job_for(self, now: datetime) -> NewJob:
        return NewJob(
            kind=self.kind,
            payload=self.payload,
            dedupe_key=f"schedule:{self.name}:{self.slot(now)}",
        )


@dataclass
class Scheduler:
    lock: LeaderLock
    uow_factory: UnitOfWorkFactory
    ids: IdSource
    clock: Clock
    tasks: Sequence[PeriodicTask]

    async def tick(self) -> int:
        if not await self.lock.try_acquire():
            return 0
        now = self.clock.now()
        enqueued = 0
        async with self.uow_factory(SYSTEM) as uow:
            for task in self.tasks:
                if await uow.jobs.enqueue(self.ids.new_id(), task.job_for(now), now):
                    enqueued += 1
        return enqueued

    async def resign(self) -> None:
        await self.lock.release()
