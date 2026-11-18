import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from haatly.ledger.public import (
    IntentApplier,
    LedgerSettings,
    Reconciler,
    SqlLedgerUnitOfWorkFactory,
    ledger_job_handlers,
    open_tigerbeetle,
    reconcile_task,
)
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
async def built_worker(settings: Settings, ledger: LedgerSettings) -> AsyncIterator[Worker]:
    url = database_url(settings, DbRole.APP)
    engine = create_engine(url, pool_size=4)
    clock = SystemClock()
    ids = Uuid7Source(clock)
    uow_factory = SqlUnitOfWorkFactory(engine)
    ledger_uow_factory = SqlLedgerUnitOfWorkFactory(engine)
    delivery = EventDelivery(uow_factory, clock, consumers={})
    wakeup = PgWakeup(plain_dsn(url))
    async with open_tigerbeetle(ledger) as gateway:
        applier = IntentApplier(ledger_uow_factory, gateway, ids, clock)
        reconciler = Reconciler(
            ledger_uow_factory,
            gateway,
            applier,
            clock,
            grace=timedelta(seconds=ledger.ledger_backstop_seconds),
        )
        handlers = {DELIVER_EVENT_JOB: delivery} | ledger_job_handlers(applier, reconciler)
        tasks = (reconcile_task(timedelta(seconds=ledger.ledger_reconcile_every_seconds)),)
        worker = Worker(
            relay=OutboxRelay(uow_factory, ids, clock, Subscriptions()),
            runner=JobRunner(uow_factory, clock, handlers, unit_random),
            scheduler=Scheduler(PgLeaderLock(plain_dsn(url)), uow_factory, ids, clock, tasks),
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


async def run(settings: Settings, ledger: LedgerSettings) -> None:
    stop = asyncio.Event()
    install_stop_handlers(stop)
    async with built_worker(settings, ledger) as worker:
        await worker.run(stop)


def main() -> None:
    asyncio.run(run(Settings(), LedgerSettings()))


if __name__ == "__main__":
    main()
