from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from haatly.ledger.app.apply import IntentApplier
from haatly.ledger.app.ports import (
    Balance,
    BalanceProjection,
    LedgerGateway,
    LedgerUnitOfWorkFactory,
    TransferRecord,
)
from haatly.ledger.domain.model import Intent, IntentStatus
from haatly.platform.kernel import SYSTEM, Clock

UNKNOWN_TRANSFER = "unknown_transfer"


@dataclass(frozen=True)
class BalanceMismatch:
    projection: str
    account_id: int
    expected: int
    actual: int


@dataclass(frozen=True)
class ReconcileReport:
    settled_pending: int
    reapplied: int
    scanned: int
    adopted: int
    new_drift: int
    open_drift: int
    mismatches: tuple[BalanceMismatch, ...]

    @property
    def clean(self) -> bool:
        return self.open_drift == 0 and not self.mismatches


@dataclass
class Reconciler:
    uow_factory: LedgerUnitOfWorkFactory
    gateway: LedgerGateway
    applier: IntentApplier
    clock: Clock
    projections: Sequence[BalanceProjection] = ()
    grace: timedelta = timedelta(seconds=30)
    batch_size: int = 500
    drift_found: int = field(default=0, init=False)

    async def run(self) -> ReconcileReport:
        self.drift_found = 0
        settled = await self.settle_stale_pending()
        reapplied = await self.verify_applied()
        scanned, adopted = await self.scan_ledger()
        mismatches = await self.compare_balances()
        async with self.uow_factory(SYSTEM) as uow:
            open_drift = await uow.intents.count_open_drift()
        return ReconcileReport(
            settled, reapplied, scanned, adopted, self.drift_found, open_drift, mismatches
        )

    async def flag(self, transfer_id: int, intent_id: UUID | None, reason: str) -> None:
        async with self.uow_factory(SYSTEM) as uow:
            if await uow.intents.record_drift(transfer_id, intent_id, reason, self.clock.now()):
                self.drift_found += 1

    async def settle_stale_pending(self) -> int:
        async with self.uow_factory(SYSTEM) as uow:
            stale = await uow.intents.stale_pending(self.clock.now() - self.grace, self.batch_size)
        for intent_id in stale:
            await self.applier.apply(intent_id)
        return len(stale)

    async def verify_applied(self) -> int:
        async with self.uow_factory(SYSTEM) as uow:
            intents = await uow.intents.unverified(self.batch_size)
        reapplied = 0
        for intent in intents:
            reapplied += await self.verify(intent)
        async with self.uow_factory(SYSTEM) as uow:
            await uow.intents.mark_verified([intent.id for intent in intents], self.clock.now())
        return reapplied

    async def verify(self, intent: Intent) -> int:
        expected = intent.expected_in_ledger
        found = await self.gateway.lookup_transfers(expected)
        if len(found) == len(expected):
            return 0
        replay = await self.applier.submit(intent)
        if replay.status is IntentStatus.APPLIED:
            return 1
        await self.flag(expected[0], intent.id, f"replay_failed:{replay.code}")
        return 0

    async def scan_ledger(self) -> tuple[int, int]:
        scanned = adopted = 0
        async with self.uow_factory(SYSTEM) as uow:
            cursor = await uow.intents.cursor()
        while True:
            records = await self.gateway.transfers_after(cursor, self.batch_size)
            if not records:
                return scanned, adopted
            adopted += await self.account_for(records)
            scanned += len(records)
            cursor = records[-1].timestamp
            async with self.uow_factory(SYSTEM) as uow:
                await uow.intents.advance_cursor(cursor)

    async def account_for(self, records: Sequence[TransferRecord]) -> int:
        async with self.uow_factory(SYSTEM) as uow:
            states = await uow.intents.states_of_transfers([record.id for record in records])
        adopted = 0
        for record in records:
            state = states.get(record.id)
            if state is None:
                await self.flag(record.id, record.intent_id, UNKNOWN_TRANSFER)
            elif state.status is IntentStatus.PENDING:
                await self.applier.apply(state.intent_id)
                adopted += 1
            elif state.status is not IntentStatus.APPLIED:
                await self.flag(
                    record.id, state.intent_id, f"transfer_for_{state.status.value}_intent"
                )
        return adopted

    async def compare_balances(self) -> tuple[BalanceMismatch, ...]:
        mismatches: list[BalanceMismatch] = []
        for projection in self.projections:
            expected = await projection.expected()
            found = {
                balance.account_id: balance
                for balance in await self.gateway.lookup_balances(list(expected))
            }
            for account_id, wanted in expected.items():
                balance = found.get(account_id, Balance(account_id, 0, 0, 0, 0))
                actual = projection.actual(balance)
                if actual != wanted:
                    mismatches.append(BalanceMismatch(projection.name, account_id, wanted, actual))
        return tuple(mismatches)
