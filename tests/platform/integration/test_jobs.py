import asyncio
from collections import Counter
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.platform.public import (
    SYSTEM,
    FrozenClock,
    Job,
    JobRunner,
    NewJob,
    SqlUnitOfWorkFactory,
    Uuid7Source,
    enqueue_job,
    list_dead_jobs,
    retry_dead_job,
)

pytestmark = pytest.mark.anyio

LEASE = timedelta(minutes=5)


class ProviderDownError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("provider down")


WORK = NewJob("test.work")


async def enqueue_many(
    uow_factory: SqlUnitOfWorkFactory, clock: FrozenClock, count: int, job: NewJob = WORK
) -> None:
    ids = Uuid7Source(clock)
    async with uow_factory(SYSTEM) as uow:
        for _ in range(count):
            await enqueue_job(uow, ids, clock, job)


async def claim(uow_factory: SqlUnitOfWorkFactory, clock: FrozenClock, limit: int) -> list[Job]:
    async with uow_factory(SYSTEM) as uow:
        return await uow.jobs.claim(clock.now(), LEASE, limit)


async def status_counts(engine: AsyncEngine) -> Counter[str]:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        rows = await connection.execute(text("SELECT status FROM platform.jobs"))
        return Counter(row.status for row in rows)


async def test_dedupe_key_admits_one_job(uow_factory: SqlUnitOfWorkFactory) -> None:
    clock = FrozenClock()
    ids = Uuid7Source(clock)
    async with uow_factory(SYSTEM) as uow:
        first = await enqueue_job(uow, ids, clock, NewJob("test.work", dedupe_key="once"))
        second = await enqueue_job(uow, ids, clock, NewJob("test.work", dedupe_key="once"))
    assert first is not None
    assert second is None


async def test_concurrent_claimers_never_share_a_job(uow_factory: SqlUnitOfWorkFactory) -> None:
    clock = FrozenClock()
    await enqueue_many(uow_factory, clock, 60)
    batches = await asyncio.gather(*(claim(uow_factory, clock, 5) for _ in range(15)))
    claimed = [job.id for batch in batches for job in batch]
    assert len(claimed) == 60
    assert len(set(claimed)) == 60


async def test_jobs_wait_until_run_after(uow_factory: SqlUnitOfWorkFactory) -> None:
    clock = FrozenClock()
    await enqueue_many(uow_factory, clock, 1, NewJob("test.work", delay=timedelta(seconds=30)))
    assert await claim(uow_factory, clock, 10) == []
    clock.advance(timedelta(seconds=30))
    assert len(await claim(uow_factory, clock, 10)) == 1


async def test_expired_lease_is_reclaimed_and_stale_owner_cannot_finish(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    clock = FrozenClock()
    await enqueue_many(uow_factory, clock, 1)
    [stale] = await claim(uow_factory, clock, 1)
    assert await claim(uow_factory, clock, 1) == []
    clock.advance(LEASE)
    [reclaimed] = await claim(uow_factory, clock, 1)
    assert (reclaimed.id, reclaimed.attempts) == (stale.id, 2)
    async with uow_factory(SYSTEM) as uow:
        assert not await uow.jobs.mark_succeeded(stale, clock.now())
        assert await uow.jobs.mark_succeeded(reclaimed, clock.now())


async def test_runner_marks_success(
    uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine
) -> None:
    clock = FrozenClock()
    seen: list[Job] = []

    async def handler(job: Job) -> None:
        seen.append(job)

    await enqueue_many(uow_factory, clock, 3)
    runner = JobRunner(uow_factory, clock, {"test.work": handler}, lambda: 0.5)
    assert await runner.run_batch() == 3
    assert len(seen) == 3
    assert await status_counts(app_engine) == Counter({"succeeded": 3})


async def test_failures_back_off_then_die_and_can_be_revived(
    uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine
) -> None:
    clock = FrozenClock()

    async def failing(_: Job) -> None:
        raise ProviderDownError

    await enqueue_many(uow_factory, clock, 1, NewJob("test.work", max_attempts=2))
    runner = JobRunner(uow_factory, clock, {"test.work": failing}, lambda: 1.0)
    assert await runner.run_batch() == 1
    assert await runner.run_batch() == 0
    clock.advance(runner.policy.ceiling(1))
    assert await runner.run_batch() == 1
    [dead] = await list_dead_jobs(uow_factory)
    assert (dead.attempts, dead.last_error) == (2, "ProviderDownError: provider down")
    assert await retry_dead_job(uow_factory, clock, dead.id)
    assert not await retry_dead_job(uow_factory, clock, dead.id)
    assert await status_counts(app_engine) == Counter({"queued": 1})


async def test_unknown_job_kind_is_recorded_as_a_failure(
    uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine
) -> None:
    clock = FrozenClock()
    await enqueue_many(uow_factory, clock, 1, NewJob("test.work", max_attempts=1))
    await JobRunner(uow_factory, clock, {}, lambda: 0.0).run_batch()
    [dead] = await list_dead_jobs(uow_factory)
    assert dead.last_error == "no handler for job kind 'test.work'"
