from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import IntEnum, StrEnum
from uuid import UUID

from haatly.ledger.domain.ids import is_valid_id

MAX_AMOUNT = (1 << 63) - 1
MAX_CODE = (1 << 16) - 1
MAX_TIMEOUT_SECONDS = (1 << 32) - 1
MAX_KIND_LENGTH = 100
MAX_TRANSFERS = 100
LANDED = frozenset({"created", "exists"})


class InvalidLedgerSpecError(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise InvalidLedgerSpecError(reason)


class Ledger(IntEnum):
    INR = 1
    INVENTORY = 2


class AccountFlag(StrEnum):
    DEBITS_MUST_NOT_EXCEED_CREDITS = "debits_must_not_exceed_credits"
    CREDITS_MUST_NOT_EXCEED_DEBITS = "credits_must_not_exceed_debits"
    HISTORY = "history"


class TransferKind(StrEnum):
    SINGLE = "single"
    PENDING = "pending"
    POST = "post_pending"
    VOID = "void_pending"


RESOLVING = frozenset({TransferKind.POST, TransferKind.VOID})
EXCLUSIVE_ACCOUNT_FLAGS = frozenset(
    {AccountFlag.DEBITS_MUST_NOT_EXCEED_CREDITS, AccountFlag.CREDITS_MUST_NOT_EXCEED_DEBITS}
)


@dataclass(frozen=True)
class AccountSpec:
    id: int
    ledger: Ledger
    code: int
    flags: frozenset[AccountFlag] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        require(is_valid_id(self.id), "account id is reserved or out of range")
        require(1 <= self.code <= MAX_CODE, "account code is out of range")
        require(not EXCLUSIVE_ACCOUNT_FLAGS.issubset(self.flags), "account balance flags conflict")


@dataclass(frozen=True)
class TransferSpec:
    id: int
    debit_account_id: int
    credit_account_id: int
    amount: int
    ledger: Ledger
    code: int
    kind: TransferKind = TransferKind.SINGLE
    pending_id: int = 0
    timeout_seconds: int = 0

    def __post_init__(self) -> None:
        require(is_valid_id(self.id), "transfer id is reserved or out of range")
        require(1 <= self.code <= MAX_CODE, "transfer code is out of range")
        require(0 <= self.amount <= MAX_AMOUNT, "transfer amount is out of range")
        if self.kind in RESOLVING:
            require(is_valid_id(self.pending_id), "post or void needs the pending transfer id")
            require(self.pending_id != self.id, "post or void cannot resolve itself")
        else:
            self.check_movement()
        timeout_allowed = self.kind is TransferKind.PENDING
        require(
            0 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS
            and (timeout_allowed or self.timeout_seconds == 0),
            "timeout is only for pending transfers",
        )

    def check_movement(self) -> None:
        require(self.amount > 0, "a movement needs a positive amount")
        require(self.pending_id == 0, "only post or void refers to a pending transfer")
        require(
            is_valid_id(self.debit_account_id) and is_valid_id(self.credit_account_id),
            "a movement needs both accounts",
        )
        require(self.debit_account_id != self.credit_account_id, "accounts must differ")

    @property
    def touches(self) -> tuple[int, ...]:
        if self.kind in RESOLVING:
            return ()
        return (self.debit_account_id, self.credit_account_id)


def resolution_of(pending: TransferSpec, transfer_id: int, kind: TransferKind) -> TransferSpec:
    return replace(pending, id=transfer_id, kind=kind, pending_id=pending.id, timeout_seconds=0)


def posting_of(pending: TransferSpec, transfer_id: int) -> TransferSpec:
    return resolution_of(pending, transfer_id, TransferKind.POST)


def voiding_of(pending: TransferSpec, transfer_id: int) -> TransferSpec:
    return resolution_of(pending, transfer_id, TransferKind.VOID)


def check_accounts_cover(
    transfers: tuple[TransferSpec, ...], accounts: tuple[AccountSpec, ...]
) -> None:
    ledgers = {account.id: account.ledger for account in accounts}
    require(len(ledgers) == len(accounts), "accounts must be distinct")
    for transfer in transfers:
        for account_id in transfer.touches:
            require(account_id in ledgers, "every touched account must be declared")
            require(ledgers[account_id] is transfer.ledger, "accounts must share the ledger")


@dataclass(frozen=True)
class NewIntent:
    kind: str
    reference_id: UUID
    transfers: tuple[TransferSpec, ...]
    accounts: tuple[AccountSpec, ...] = ()

    def __post_init__(self) -> None:
        require(0 < len(self.kind) <= MAX_KIND_LENGTH, "intent kind length is out of range")
        require(0 < len(self.transfers) <= MAX_TRANSFERS, "intent transfer count is out of range")
        require(len(set(self.transfer_ids)) == len(self.transfers), "transfer ids must differ")
        voids = sum(transfer.kind is TransferKind.VOID for transfer in self.transfers)
        require(voids == 0 or len(self.transfers) == 1, "a void must be its own intent")
        check_accounts_cover(self.transfers, self.accounts)

    @property
    def transfer_ids(self) -> tuple[int, ...]:
        return tuple(transfer.id for transfer in self.transfers)


class IntentStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    FLAGGED = "flagged"


@dataclass(frozen=True)
class Intent:
    id: UUID
    kind: str
    reference_id: UUID
    status: IntentStatus
    transfers: tuple[TransferSpec, ...]
    accounts: tuple[AccountSpec, ...]
    created_at: datetime
    result_code: str | None = None
    outcomes: tuple[str, ...] = ()

    @property
    def expected_in_ledger(self) -> tuple[int, ...]:
        return tuple(
            transfer.id
            for transfer, outcome in zip(self.transfers, self.outcomes, strict=False)
            if outcome in LANDED
        )

    def matches(self, new: NewIntent) -> bool:
        return (self.kind, self.reference_id, self.transfers, self.accounts) == (
            new.kind,
            new.reference_id,
            new.transfers,
            new.accounts,
        )
