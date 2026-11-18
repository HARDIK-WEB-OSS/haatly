import logging
from datetime import timedelta

import pytest

from haatly.ledger.public import (
    APPLY_INTENT_JOB,
    RECONCILE_JOB,
    IntentApplier,
    IntentStatus,
    LedgerGateway,
    Reconciler,
    SqlLedgerUnitOfWorkFactory,
    ledger_job_handlers,
    reconcile_task,
    record_intent,
)
from haatly.platform.public import SYSTEM, FrozenClock, JobRunner, Uuid7Source
from tests.ledger.scenario import Inventory

pytestmark = pytest.mark.anyio

BACKSTOP = timedelta(seconds=30)


async def test_the_backstop_job_applies_an_intent_left_pending(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    reconciler: Reconciler,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    async with ledger_uow_factory(SYSTEM) as uow:
        intent = await record_intent(uow, ids, clock, inventory.receive(6), BACKSTOP)
    runner = JobRunner(
        ledger_uow_factory, clock, ledger_job_handlers(applier, reconciler), lambda: 0.0
    )
    assert await runner.run_batch() == 0
    clock.advance(BACKSTOP)
    assert await runner.run_batch() == 1
    async with ledger_uow_factory(SYSTEM) as uow:
        stored = await uow.intents.get(intent.id)
    assert stored is not None
    assert stored.status is IntentStatus.APPLIED
    assert (await gateway.lookup_balances((inventory.stock.id,)))[0].credits_posted == 6


async def test_the_reconcile_job_runs_on_a_schedule_and_reports_drift(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    reconciler: Reconciler,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    task = reconcile_task(timedelta(minutes=1))
    assert (task.kind, task.every) == (RECONCILE_JOB, timedelta(minutes=1))
    handlers = ledger_job_handlers(applier, reconciler)
    assert set(handlers) == {APPLY_INTENT_JOB, RECONCILE_JOB}
    runner = JobRunner(ledger_uow_factory, clock, handlers, lambda: 0.0)

    async with ledger_uow_factory(SYSTEM) as uow:
        await uow.jobs.enqueue(ids.new_id(), task.job_for(clock.now()), clock.now())
    with caplog.at_level(logging.WARNING, logger="haatly.ledger"):
        assert await runner.run_batch() == 1
    assert caplog.records == []

    stray = inventory.receive(1)
    await gateway.create_accounts(stray.accounts)
    await gateway.create_transfers(ids.new_id(), stray.transfers)
    clock.advance(timedelta(minutes=1))
    async with ledger_uow_factory(SYSTEM) as uow:
        await uow.jobs.enqueue(ids.new_id(), task.job_for(clock.now()), clock.now())
    with caplog.at_level(logging.WARNING, logger="haatly.ledger"):
        assert await runner.run_batch() == 1
    assert [record.getMessage() for record in caplog.records] == [
        "ledger reconciliation found drift: open_drift=1 mismatches=0"
    ]
