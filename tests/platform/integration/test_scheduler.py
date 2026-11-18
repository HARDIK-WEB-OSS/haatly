import asyncio
import time
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.platform.public import (
    SYSTEM,
    DbRole,
    FrozenClock,
    NewJob,
    PeriodicTask,
    PgLeaderLock,
    PgWakeup,
    Scheduler,
    Settings,
    SqlUnitOfWorkFactory,
    Uuid7Source,
    database_url,
    enqueue_job,
    plain_dsn,
)

pytestmark = pytest.mark.anyio

TASK = PeriodicTask("reconcile", timedelta(minutes=10), "test.reconcile")


def dsn(settings: Settings) -> str:
    return plain_dsn(database_url(settings, DbRole.APP))


def scheduler(
    migrated: Settings, uow_factory: SqlUnitOfWorkFactory, clock: FrozenClock
) -> Scheduler:
    lock = PgLeaderLock(dsn(migrated))
    return Scheduler(lock, uow_factory, Uuid7Source(clock), clock, tasks=(TASK,))


async def job_count(engine: AsyncEngine) -> object:
    async with engine.connect() as connection:
        return (await connection.execute(text("SELECT count(*) FROM platform.jobs"))).scalar()


async def test_only_one_scheduler_leads(
    migrated: Settings, uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine
) -> None:
    clock = FrozenClock()
    first, second = scheduler(migrated, uow_factory, clock), scheduler(migrated, uow_factory, clock)
    try:
        assert await first.tick() == 1
        assert await second.tick() == 0
        assert await first.tick() == 0
        clock.advance(TASK.every)
        assert await first.tick() == 1
        await first.resign()
        assert await second.tick() == 0
        clock.advance(TASK.every)
        assert await second.tick() == 1
    finally:
        await first.resign()
        await second.resign()
    assert await job_count(app_engine) == 3


async def test_leadership_is_lost_with_the_connection_and_regained(
    migrated: Settings, app_engine: AsyncEngine
) -> None:
    lock, rival = PgLeaderLock(dsn(migrated)), PgLeaderLock(dsn(migrated))
    try:
        assert await lock.try_acquire()
        assert not await rival.try_acquire()
        backend = lock.connection.get_server_pid()
        async with app_engine.connect() as connection:
            terminate = text("SELECT pg_terminate_backend(:pid, 5000)")
            assert (await connection.execute(terminate, {"pid": backend})).scalar() is True
        assert await rival.try_acquire()
        assert not await lock.try_acquire()
    finally:
        await lock.release()
        await rival.release()


async def test_wakeup_returns_early_when_work_is_enqueued(
    migrated: Settings, uow_factory: SqlUnitOfWorkFactory
) -> None:
    wakeup = PgWakeup(dsn(migrated))
    clock = FrozenClock()
    try:
        await wakeup.ensure_listening()
        started = time.monotonic()
        waiting = asyncio.create_task(wakeup.wait(max_wait=20))
        await asyncio.sleep(0.2)
        async with uow_factory(SYSTEM) as uow:
            await enqueue_job(uow, Uuid7Source(clock), clock, NewJob("test.wake"))
        await waiting
        assert time.monotonic() - started < 10
    finally:
        await wakeup.close()


async def test_wakeup_times_out_quietly(migrated: Settings) -> None:
    wakeup = PgWakeup(dsn(migrated))
    try:
        await wakeup.wait(max_wait=0.1)
    finally:
        await wakeup.close()
    await wakeup.close()
