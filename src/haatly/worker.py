import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from haatly.platform.public import (
    DELIVER_EVENT_JOB,
    DbRole,
    EventDelivery,
    JobRunner,
    OutboxRelay,
    PgLeaderLock,
    PgWakeup,
    Scheduler,
    Settings,
    SqlUnitOfWorkFactory,
    Subscriptions,
    SystemClock,
    Uuid7Source,
    Worker,
    create_engine,
    database_url,
    plain_dsn,
    unit_random,
)


@asynccontextmanager
async def built_worker(settings: Settings) -> AsyncIterator[Worker]:
    url = database_url(settings, DbRole.APP)
    engine = create_engine(url, pool_size=4)
    clock = SystemClock()
    ids = Uuid7Source(clock)
    uow_factory = SqlUnitOfWorkFactory(engine)
    delivery = EventDelivery(uow_factory, clock, consumers={})
    wakeup = PgWakeup(plain_dsn(url))
    worker = Worker(
        relay=OutboxRelay(uow_factory, ids, clock, Subscriptions()),
        runner=JobRunner(uow_factory, clock, {DELIVER_EVENT_JOB: delivery}, unit_random),
        scheduler=Scheduler(PgLeaderLock(plain_dsn(url)), uow_factory, ids, clock, tasks=()),
        wakeup=wakeup,
        idle_seconds=settings.worker_idle_seconds,
    )
    try:
        yield worker
    finally:
        await worker.scheduler.resign()
        await wakeup.close()
        await engine.dispose()


def install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal_number, stop.set)


async def run(settings: Settings) -> None:
    stop = asyncio.Event()
    install_stop_handlers(stop)
    async with built_worker(settings) as worker:
        await worker.run(stop)


def main() -> None:
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
