from datetime import timedelta

import pytest

from haatly.ledger.public import (
    APPLY_INTENT_JOB,
    AccountConflictError,
    AccountSpec,
    IntentConflictError,
    IntentStatus,
    Ledger,
    NewIntent,
    Settlement,
    SqlLedgerUnitOfWorkFactory,
    record_intent,
)
from haatly.platform.public import SYSTEM, FrozenClock, Uuid7Source
from tests.ledger.scenario import Inventory

pytestmark = pytest.mark.anyio

BACKSTOP = timedelta(seconds=30)


async def record(
    factory: SqlLedgerUnitOfWorkFactory, ids: Uuid7Source, clock: FrozenClock, new: NewIntent
) -> object:
    async with factory(SYSTEM) as uow:
        return await record_intent(uow, ids, clock, new, BACKSTOP)


async def test_an_intent_is_recorded_pending_with_a_backstop_job(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(5)
    async with ledger_uow_factory(SYSTEM) as uow:
        intent = await record_intent(uow, ids, clock, new, BACKSTOP)
    async with ledger_uow_factory(SYSTEM) as uow:
        stored = await uow.intents.get(intent.id)
        early = await uow.jobs.claim(clock.now(), timedelta(minutes=1), 10)
        due = await uow.jobs.claim(clock.now() + BACKSTOP, timedelta(minutes=1), 10)
    assert stored == intent
    assert (stored.status, stored.transfers, stored.accounts) == (
        IntentStatus.PENDING,
        new.transfers,
        new.accounts,
    )
    assert early == []
    assert [(job.kind, job.payload) for job in due] == [
        (APPLY_INTENT_JOB, {"intent_id": str(intent.id)})
    ]


async def test_recording_the_same_intent_twice_returns_the_first(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(5)
    first = await record(ledger_uow_factory, ids, clock, new)
    second = await record(ledger_uow_factory, ids, clock, new)
    assert first == second
    async with ledger_uow_factory(SYSTEM) as uow:
        jobs = await uow.jobs.claim(clock.now() + BACKSTOP, timedelta(minutes=1), 10)
    assert len(jobs) == 1


async def test_a_different_intent_for_the_same_reference_is_refused(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(5)
    await record(ledger_uow_factory, ids, clock, new)
    changed = NewIntent(new.kind, new.reference_id, inventory.receive(6).transfers, new.accounts)
    with pytest.raises(IntentConflictError):
        await record(ledger_uow_factory, ids, clock, changed)


async def test_an_account_cannot_be_redefined(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    await record(ledger_uow_factory, ids, clock, inventory.receive(5))
    receipt = inventory.receive(1)
    loose_stock = AccountSpec(inventory.stock.id, Ledger.INVENTORY, inventory.stock.code)
    redefined = NewIntent(
        receipt.kind,
        receipt.reference_id,
        receipt.transfers,
        (inventory.received, loose_stock, inventory.sold),
    )
    with pytest.raises(AccountConflictError):
        await record(ledger_uow_factory, ids, clock, redefined)


async def test_a_rolled_back_caller_leaves_no_intent_and_no_job(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(5)

    async def record_then_fail() -> None:
        async with ledger_uow_factory(SYSTEM) as uow:
            await record_intent(uow, ids, clock, new, BACKSTOP)
            raise RuntimeError

    with pytest.raises(RuntimeError):
        await record_then_fail()
    async with ledger_uow_factory(SYSTEM) as uow:
        assert await uow.intents.find_by_reference(new.kind, new.reference_id) is None
        assert await uow.jobs.claim(clock.now() + BACKSTOP, timedelta(minutes=1), 10) == []


async def test_missing_intents_read_as_none(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory, ids: Uuid7Source
) -> None:
    async with ledger_uow_factory(SYSTEM) as uow:
        assert await uow.intents.get(ids.new_id()) is None


async def test_only_the_first_settlement_of_an_intent_counts(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    async with ledger_uow_factory(SYSTEM) as uow:
        intent = await record_intent(uow, ids, clock, inventory.receive(5), BACKSTOP)
    applied = Settlement(IntentStatus.APPLIED, None, ("created",))
    rejected = Settlement(IntentStatus.REJECTED, "exceeds_credits", ("exceeds_credits",))
    async with ledger_uow_factory(SYSTEM) as uow:
        assert await uow.intents.settle(intent.id, applied, clock.now()) is True
        assert await uow.intents.settle(intent.id, rejected, clock.now()) is False
        stored = await uow.intents.get(intent.id)
    assert stored is not None
    assert (stored.status, stored.outcomes) == (IntentStatus.APPLIED, ("created",))
