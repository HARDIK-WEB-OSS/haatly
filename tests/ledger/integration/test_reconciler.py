import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest

from haatly.ledger.public import (
    UNKNOWN_TRANSFER,
    AccountSpec,
    Balance,
    BalanceMismatch,
    Intent,
    IntentApplier,
    IntentStatus,
    LedgerGateway,
    NewIntent,
    Reconciler,
    Settlement,
    SqlLedgerUnitOfWorkFactory,
    TransferRecord,
    TransferSpec,
    record_intent,
)
from haatly.platform.public import SYSTEM, FrozenClock, Uuid7Source
from tests.ledger.scenario import Inventory

pytestmark = pytest.mark.anyio

BACKSTOP = timedelta(seconds=30)
PAST_GRACE = timedelta(seconds=31)


class SimulatedCrashError(RuntimeError):
    pass


@dataclass
class CrashingGateway:
    real: LedgerGateway
    after_ledger_write: bool

    async def create_accounts(self, accounts: Sequence[AccountSpec]) -> list[str]:
        return await self.real.create_accounts(accounts)

    async def create_transfers(
        self, intent_id: UUID, transfers: Sequence[TransferSpec]
    ) -> list[str]:
        if self.after_ledger_write:
            await self.real.create_transfers(intent_id, transfers)
        raise SimulatedCrashError

    async def lookup_transfers(self, ids: Sequence[int]) -> list[TransferRecord]:
        return await self.real.lookup_transfers(ids)

    async def transfers_after(self, timestamp: int, limit: int) -> list[TransferRecord]:
        return await self.real.transfers_after(timestamp, limit)

    async def lookup_balances(self, ids: Sequence[int]) -> list[Balance]:
        return await self.real.lookup_balances(ids)


def crashing_applier(applier: IntentApplier, *, after_ledger_write: bool) -> IntentApplier:
    gateway = CrashingGateway(applier.gateway, after_ledger_write)
    return IntentApplier(applier.uow_factory, gateway, applier.ids, applier.clock)


async def record(
    factory: SqlLedgerUnitOfWorkFactory, ids: Uuid7Source, clock: FrozenClock, new: NewIntent
) -> UUID:
    async with factory(SYSTEM) as uow:
        return (await record_intent(uow, ids, clock, new, BACKSTOP)).id


async def stored(factory: SqlLedgerUnitOfWorkFactory, intent_id: UUID) -> Intent:
    async with factory(SYSTEM) as uow:
        intent = await uow.intents.get(intent_id)
    assert intent is not None
    return intent


@dataclass
class StockProjection:
    name: str
    expected_by_account: Mapping[int, int]

    async def expected(self) -> Mapping[int, int]:
        return self.expected_by_account

    def actual(self, balance: Balance) -> int:
        return balance.credits_posted - balance.debits_posted - balance.debits_pending


async def test_a_crash_before_the_ledger_call_is_repaired_with_zero_drift(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    reconciler: Reconciler,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(5)
    intent_id = await record(ledger_uow_factory, ids, clock, new)
    with pytest.raises(SimulatedCrashError):
        await crashing_applier(applier, after_ledger_write=False).apply(intent_id)
    assert await gateway.lookup_transfers(new.transfer_ids) == []

    within_grace = await reconciler.run()
    assert within_grace.settled_pending == 0
    assert (await stored(ledger_uow_factory, intent_id)).status is IntentStatus.PENDING

    clock.advance(PAST_GRACE)
    report = await reconciler.run()
    assert report.settled_pending == 1
    assert report.clean
    assert (await stored(ledger_uow_factory, intent_id)).status is IntentStatus.APPLIED
    assert [record.id for record in await gateway.lookup_transfers(new.transfer_ids)] == list(
        new.transfer_ids
    )


async def test_a_crash_after_the_ledger_call_is_adopted_without_waiting(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    reconciler: Reconciler,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    intent_id = await record(ledger_uow_factory, ids, clock, inventory.receive(5))
    with pytest.raises(SimulatedCrashError):
        await crashing_applier(applier, after_ledger_write=True).apply(intent_id)
    report = await reconciler.run()
    assert (report.adopted, report.settled_pending, report.clean) == (1, 0, True)
    assert (await stored(ledger_uow_factory, intent_id)).status is IntentStatus.APPLIED
    assert (await reconciler.run()).scanned == 0


async def test_an_applied_intent_missing_from_the_ledger_is_replayed(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    reconciler: Reconciler,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(4)
    intent_id = await record(ledger_uow_factory, ids, clock, new)
    await mark_applied_without_ledger(ledger_uow_factory, intent_id, clock)
    report = await reconciler.run()
    assert (report.reapplied, report.clean) == (1, True)
    assert (await gateway.lookup_balances((inventory.stock.id,)))[0].credits_posted == 4
    assert (await reconciler.run()).reapplied == 0


async def mark_applied_without_ledger(
    factory: SqlLedgerUnitOfWorkFactory, intent_id: UUID, clock: FrozenClock
) -> None:
    async with factory(SYSTEM) as uow:
        await uow.intents.settle(
            intent_id, Settlement(IntentStatus.APPLIED, None, ("created",)), clock.now()
        )


async def test_a_replay_that_now_fails_is_reported_as_drift(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    reconciler: Reconciler,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    intent_id = await record(ledger_uow_factory, ids, clock, inventory.hold(1, 600))
    await mark_applied_without_ledger(ledger_uow_factory, intent_id, clock)
    report = await reconciler.run()
    assert (report.reapplied, report.new_drift, report.open_drift) == (0, 1, 1)
    assert not report.clean
    async with ledger_uow_factory(SYSTEM) as uow:
        drift = await uow.intents.open_drift(10)
    assert [entry.reason for entry in drift] == ["replay_failed:exceeds_credits"]
    assert (await reconciler.run()).new_drift == 0


async def test_an_unknown_ledger_transfer_is_flagged_and_never_touched(
    reconciler: Reconciler,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    inventory: Inventory,
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
) -> None:
    receipt = inventory.receive(3)
    await gateway.create_accounts(receipt.accounts)
    await gateway.create_transfers(ids.new_id(), receipt.transfers)
    report = await reconciler.run()
    assert (report.scanned, report.new_drift, report.open_drift) == (1, 1, 1)
    async with ledger_uow_factory(SYSTEM) as uow:
        drift = await uow.intents.open_drift(10)
    assert [(entry.transfer_id, entry.reason) for entry in drift] == [
        (receipt.transfers[0].id, "unknown_transfer")
    ]
    second = await reconciler.run()
    assert (second.scanned, second.new_drift, second.open_drift) == (0, 0, 1)
    assert (await gateway.lookup_balances((inventory.stock.id,)))[0].credits_posted == 3


async def test_drift_is_counted_once_per_transfer(
    reconciler: Reconciler, ids: Uuid7Source, ledger_uow_factory: SqlLedgerUnitOfWorkFactory
) -> None:
    intent_id = ids.new_id()
    await reconciler.flag(42, intent_id, UNKNOWN_TRANSFER)
    await reconciler.flag(42, intent_id, UNKNOWN_TRANSFER)
    assert reconciler.drift_found == 1
    async with ledger_uow_factory(SYSTEM) as uow:
        assert await uow.intents.count_open_drift() == 1


async def test_a_transfer_for_a_rejected_intent_is_drift(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    reconciler: Reconciler,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    new = inventory.receive(2)
    intent_id = await record(ledger_uow_factory, ids, clock, new)
    async with ledger_uow_factory(SYSTEM) as uow:
        await uow.intents.settle(
            intent_id,
            Settlement(IntentStatus.REJECTED, "exceeds_credits", ("exceeds_credits",)),
            clock.now(),
        )
    await gateway.create_accounts(new.accounts)
    await gateway.create_transfers(intent_id, new.transfers)
    report = await reconciler.run()
    assert report.new_drift == 1
    async with ledger_uow_factory(SYSTEM) as uow:
        drift = await uow.intents.open_drift(10)
    assert [entry.reason for entry in drift] == ["transfer_for_rejected_intent"]


async def test_balances_are_compared_with_projections(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    reconciler: Reconciler,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    await applier.apply(await record(ledger_uow_factory, ids, clock, inventory.receive(5)))
    reconciler.projections = (
        StockProjection("display_stock", {inventory.stock.id: 5}),
        StockProjection("stale_stock", {inventory.stock.id: 4}),
    )
    report = await reconciler.run()
    assert report.mismatches == (BalanceMismatch("stale_stock", inventory.stock.id, 4, 5),)
    assert not report.clean


async def apply_with_random_crash(
    rng: random.Random,
    factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    new: NewIntent,
    appliers: Sequence[IntentApplier],
) -> None:
    intent_id = await record(factory, ids, clock, new)
    try:
        await rng.choice(appliers).apply(intent_id)
    except SimulatedCrashError:
        return


def exit_phases(inventory: Inventory, rng: random.Random) -> list[list[NewIntent]]:
    receipts = [inventory.receive(rng.randint(1, 5)) for _ in range(15)]
    holds = [inventory.hold(1, 600) for _ in range(15)]
    follow_ups = [inventory.capture(hold) for hold in holds[:5]]
    follow_ups += [inventory.release(hold) for hold in holds[5:10]]
    return [receipts, holds, follow_ups]


async def test_exit_random_crashes_between_commit_and_ledger_leave_zero_drift(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    reconciler: Reconciler,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    rng = random.Random(20261118)
    appliers = (
        applier,
        crashing_applier(applier, after_ledger_write=False),
        crashing_applier(applier, after_ledger_write=True),
    )
    phases = exit_phases(inventory, rng)
    received = sum(receipt.transfers[0].amount for receipt in phases[0])
    available = received - 5 - 5
    reconciler.projections = (StockProjection("display_stock", {inventory.stock.id: available}),)

    reports = []
    for phase in phases:
        for new in phase:
            await apply_with_random_crash(rng, ledger_uow_factory, ids, clock, new, appliers)
        clock.advance(PAST_GRACE)
        reports.append(await reconciler.run())

    final = reports[-1]
    assert final.clean, final
    assert sum(report.settled_pending + report.adopted for report in reports) > 0
    async with ledger_uow_factory(SYSTEM) as uow:
        assert await uow.intents.stale_pending(clock.now() + timedelta(days=1), 100) == []
    balance = (await gateway.lookup_balances((inventory.stock.id,)))[0]
    assert (balance.credits_posted, balance.debits_posted, balance.debits_pending) == (
        received,
        5,
        5,
    )
