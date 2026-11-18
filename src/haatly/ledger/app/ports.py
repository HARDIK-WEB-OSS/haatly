from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from haatly.ledger.domain.model import AccountSpec, Intent, IntentStatus, TransferKind, TransferSpec
from haatly.ledger.domain.outcomes import Settlement
from haatly.platform.kernel import TenantContext, UnitOfWork


class LedgerUnavailableError(ConnectionError):
    pass


class AccountConflictError(ValueError):
    pass


@dataclass(frozen=True)
class TransferRecord:
    id: int
    debit_account_id: int
    credit_account_id: int
    amount: int
    ledger: int
    code: int
    kind: TransferKind
    pending_id: int
    intent_id: UUID | None
    timestamp: int


@dataclass(frozen=True)
class Balance:
    account_id: int
    debits_pending: int
    debits_posted: int
    credits_pending: int
    credits_posted: int


@dataclass(frozen=True)
class DriftEntry:
    transfer_id: int
    intent_id: UUID | None
    reason: str
    detected_at: datetime


@dataclass(frozen=True)
class IntentState:
    intent_id: UUID
    status: IntentStatus


class LedgerGateway(Protocol):
    async def create_accounts(self, accounts: Sequence[AccountSpec]) -> list[str]: ...

    async def create_transfers(
        self, intent_id: UUID, transfers: Sequence[TransferSpec]
    ) -> list[str]: ...

    async def lookup_transfers(self, ids: Sequence[int]) -> list[TransferRecord]: ...

    async def transfers_after(self, timestamp: int, limit: int) -> list[TransferRecord]: ...

    async def lookup_balances(self, ids: Sequence[int]) -> list[Balance]: ...


class IntentRepository(Protocol):
    async def add(self, intent: Intent) -> None: ...

    async def get(self, intent_id: UUID) -> Intent | None: ...

    async def find_by_reference(self, kind: str, reference_id: UUID) -> Intent | None: ...

    async def settle(self, intent_id: UUID, settlement: Settlement, now: datetime) -> bool: ...

    async def stale_pending(self, before: datetime, limit: int) -> list[UUID]: ...

    async def unverified(self, limit: int) -> list[Intent]: ...

    async def mark_verified(self, intent_ids: Sequence[UUID], now: datetime) -> None: ...

    async def states_of_transfers(
        self, transfer_ids: Sequence[int]
    ) -> Mapping[int, IntentState]: ...

    async def cursor(self) -> int: ...

    async def advance_cursor(self, timestamp: int) -> None: ...

    async def record_drift(
        self, transfer_id: int, intent_id: UUID | None, reason: str, now: datetime
    ) -> bool: ...

    async def open_drift(self, limit: int) -> list[DriftEntry]: ...

    async def count_open_drift(self) -> int: ...


class LedgerUnitOfWork(UnitOfWork, Protocol):
    @property
    def intents(self) -> IntentRepository: ...

    async def __aenter__(self) -> Self: ...


class LedgerUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> LedgerUnitOfWork: ...


class BalanceProjection(Protocol):
    name: str

    async def expected(self) -> Mapping[int, int]: ...

    def actual(self, balance: Balance) -> int: ...
