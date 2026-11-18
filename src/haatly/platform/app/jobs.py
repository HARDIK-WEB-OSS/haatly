from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from haatly.platform.app.ports import (
    DeadJob,
    IdSource,
    JobHandler,
    UnitOfWork,
    UnitOfWorkFactory,
    UnitRandom,
)
from haatly.platform.domain.backoff import BackoffPolicy
from haatly.platform.domain.clock import Clock
from haatly.platform.domain.jobs import Bury, Job, NewJob, after_failure
from haatly.platform.domain.tenancy import SYSTEM

MAX_ERROR_LENGTH = 2000


async def enqueue_job(uow: UnitOfWork, ids: IdSource, clock: Clock, job: NewJob) -> UUID | None:
    job_id = ids.new_id()
    inserted = await uow.jobs.enqueue(job_id, job, clock.now())
    return job_id if inserted else None


def describe_failure(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]


@dataclass
class JobRunner:
    uow_factory: UnitOfWorkFactory
    clock: Clock
    handlers: Mapping[str, JobHandler]
    unit_random: UnitRandom
    policy: BackoffPolicy = field(default_factory=BackoffPolicy)
    lease: timedelta = timedelta(minutes=5)
    batch_size: int = 10

    async def run_batch(self) -> int:
        async with self.uow_factory(SYSTEM) as uow:
            claimed = await uow.jobs.claim(self.clock.now(), self.lease, self.batch_size)
        for job in claimed:
            await self.execute(job)
        return len(claimed)

    async def execute(self, job: Job) -> None:
        handler = self.handlers.get(job.kind)
        if handler is None:
            await self.record_failure(job, f"no handler for job kind {job.kind!r}")
            return
        try:
            await handler(job)
        except Exception as error:  # noqa: BLE001
            await self.record_failure(job, describe_failure(error))
            return
        async with self.uow_factory(SYSTEM) as uow:
            await uow.jobs.mark_succeeded(job, self.clock.now())

    async def record_failure(self, job: Job, error: str) -> None:
        now = self.clock.now()
        outcome = after_failure(job, now, self.policy, self.unit_random)
        async with self.uow_factory(SYSTEM) as uow:
            if isinstance(outcome, Bury):
                await uow.jobs.bury(job, error, now)
            else:
                await uow.jobs.reschedule(job, outcome.run_after, error, now)


async def list_dead_jobs(uow_factory: UnitOfWorkFactory, limit: int = 100) -> list[DeadJob]:
    async with uow_factory(SYSTEM) as uow:
        return await uow.jobs.list_dead(limit)


async def retry_dead_job(uow_factory: UnitOfWorkFactory, clock: Clock, job_id: UUID) -> bool:
    async with uow_factory(SYSTEM) as uow:
        return await uow.jobs.revive(job_id, clock.now())
