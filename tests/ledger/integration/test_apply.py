import asyncio
from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

import pytest
import tigerbeetle as tb

from haatly.ledger.public import (
    INTENT_SETTLED,
    Balance,
    IntentApplier,
    IntentStatus,
    LedgerGateway,
    LedgerUnavailableError,
    NewIntent,
    SqlLedgerUnitOfWorkFactory,
    UnknownIntentError,
    record_intent,
)
from haatly.platform.public import SYSTEM, FrozenClock, OutboxEvent, Uuid7Source
from tests.ledger.scenario import Inventory

pytestmark = pytest.mark.anyio

BACKSTOP = timedelta(seconds=30)


async def record(
    factory: SqlLedgerUnitOfWorkFactory, ids: Uuid7Source, clock: FrozenClock, new: NewIntent
) -> UUID:
    async with factory(SYSTEM) as uow:
        return (await record_intent(uow, ids, clock, new, BACKSTOP)).id


async def settled_events(factory: SqlLedgerUnitOfWorkFactory) -> list[OutboxEvent]:
    async with factory(SYSTEM) as uow:
        events = await uow.outbox.claim_unpublished(1000)
    return [event for event in events if event.topic == INTENT_SETTLED]


async def stock_balance(gateway: LedgerGateway, inventory: Inventory) -> Balance:
    return (await gateway.lookup_balances((inventory.stock.id,)))[0]


async def test_applying_an_intent_writes_the_ledger_and_announces_it(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(5)
    intent_id = await record(ledger_uow_factory, ids, clock, new)
    assert await applier.apply(intent_id, SYSTEM) is IntentStatus.APPLIED
    assert await applier.apply(intent_id, SYSTEM) is IntentStatus.APPLIED
    assert (await stock_balance(gateway, inventory)).credits_posted == 5
    events = await settled_events(ledger_uow_factory)
    assert [(event.aggregate_id, event.payload) for event in events] == [
        (
            intent_id,
            {
                "kind": "inventory.receive",
                "reference_id": str(new.reference_id),
                "status": "applied",
                "code": None,
            },
        )
    ]
    async with ledger_uow_factory(SYSTEM) as uow:
        stored = await uow.intents.get(intent_id)
    assert stored is not None
    assert (stored.status, stored.outcomes) == (IntentStatus.APPLIED, ("created",))


async def test_a_hold_beyond_stock_is_rejected_with_the_ledger_code(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    await applier.apply(await record(ledger_uow_factory, ids, clock, inventory.receive(2)), SYSTEM)
    hold_id = await record(ledger_uow_factory, ids, clock, inventory.hold(3))
    assert await applier.apply(hold_id, SYSTEM) is IntentStatus.REJECTED
    async with ledger_uow_factory(SYSTEM) as uow:
        stored = await uow.intents.get(hold_id)
    assert stored is not None
    assert stored.result_code == "exceeds_credits"
    assert (await stock_balance(gateway, inventory)).debits_pending == 0
    payloads = [event.payload for event in await settled_events(ledger_uow_factory)]
    assert payloads[-1]["status"] == "rejected"


async def test_concurrent_applies_of_one_intent_settle_it_once(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    intent_id = await record(ledger_uow_factory, ids, clock, inventory.receive(7))
    statuses = await asyncio.gather(*(applier.apply(intent_id, SYSTEM) for _ in range(20)))
    assert set(statuses) == {IntentStatus.APPLIED}
    assert (await stock_balance(gateway, inventory)).credits_posted == 7
    assert len(await settled_events(ledger_uow_factory)) == 1


async def test_fifty_buyers_racing_for_the_last_unit_get_one_hold(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    await applier.apply(await record(ledger_uow_factory, ids, clock, inventory.receive(1)), SYSTEM)
    holds = [
        await record(ledger_uow_factory, ids, clock, inventory.hold(1, 600)) for _ in range(50)
    ]
    statuses = await asyncio.gather(*(applier.apply(hold, SYSTEM) for hold in holds))
    assert statuses.count(IntentStatus.APPLIED) == 1
    assert statuses.count(IntentStatus.REJECTED) == 49
    balance = await stock_balance(gateway, inventory)
    assert (balance.credits_posted, balance.debits_pending) == (1, 1)


async def test_capture_posts_a_live_hold(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    await applier.apply(await record(ledger_uow_factory, ids, clock, inventory.receive(3)), SYSTEM)
    hold = inventory.hold(2, 600)
    await applier.apply(await record(ledger_uow_factory, ids, clock, hold), SYSTEM)
    capture_id = await record(ledger_uow_factory, ids, clock, inventory.capture(hold))
    assert await applier.apply(capture_id, SYSTEM) is IntentStatus.APPLIED
    balance = await stock_balance(gateway, inventory)
    assert (balance.debits_pending, balance.debits_posted) == (0, 2)


async def test_an_expired_hold_counts_as_released_but_cannot_be_captured(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    await applier.apply(await record(ledger_uow_factory, ids, clock, inventory.receive(2)), SYSTEM)
    released, captured = inventory.hold(1), inventory.hold(1)
    await applier.apply(await record(ledger_uow_factory, ids, clock, released), SYSTEM)
    await applier.apply(await record(ledger_uow_factory, ids, clock, captured), SYSTEM)
    assert (await stock_balance(gateway, inventory)).debits_pending == 2
    await asyncio.sleep(inventory.hold_timeout_seconds + 1.5)
    assert (await stock_balance(gateway, inventory)).debits_pending == 0
    release_id = await record(ledger_uow_factory, ids, clock, inventory.release(released))
    capture_id = await record(ledger_uow_factory, ids, clock, inventory.capture(captured))
    assert await applier.apply(release_id, SYSTEM) is IntentStatus.APPLIED
    assert await applier.apply(capture_id, SYSTEM) is IntentStatus.REJECTED
    async with ledger_uow_factory(SYSTEM) as uow:
        release = await uow.intents.get(release_id)
        capture = await uow.intents.get(capture_id)
    assert release is not None
    assert capture is not None
    assert release.outcomes == ("pending_transfer_expired",)
    assert capture.result_code == "pending_transfer_expired"


async def test_an_account_defined_differently_in_the_ledger_flags_the_intent(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    raw_tigerbeetle: tb.ClientAsync,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    stray = tb.Account(id=inventory.stock.id, ledger=inventory.stock.ledger, code=99)
    await raw_tigerbeetle.create_accounts([stray])
    intent_id = await record(ledger_uow_factory, ids, clock, inventory.receive(5))
    assert await applier.apply(intent_id, SYSTEM) is IntentStatus.FLAGGED
    async with ledger_uow_factory(SYSTEM) as uow:
        stored = await uow.intents.get(intent_id)
    assert stored is not None
    assert stored.result_code == "account:exists_with_different_flags"
    assert await raw_tigerbeetle.lookup_transfers([inventory.receive(5).transfers[0].id]) == []


async def test_unknown_intents_are_refused(applier: IntentApplier, ids: Uuid7Source) -> None:
    with pytest.raises(UnknownIntentError):
        await applier.apply(ids.new_id(), SYSTEM)


class DownGateway:
    def __init__(self, real: LedgerGateway) -> None:
        self.real = real

    async def create_accounts(self, _: Sequence[object]) -> list[str]:
        raise LedgerUnavailableError


async def test_an_unreachable_ledger_leaves_the_intent_pending(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    down = IntentApplier(ledger_uow_factory, DownGateway(gateway), ids, clock)  # type: ignore[arg-type]
    intent_id = await record(ledger_uow_factory, ids, clock, inventory.receive(5))
    with pytest.raises(LedgerUnavailableError):
        await down.apply(intent_id, SYSTEM)
    async with ledger_uow_factory(SYSTEM) as uow:
        stored = await uow.intents.get(intent_id)
    assert stored is not None
    assert stored.status is IntentStatus.PENDING
