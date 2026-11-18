from collections.abc import Sequence
from dataclasses import dataclass

from haatly.ledger.domain.model import LANDED, IntentStatus, TransferKind, TransferSpec

LINKED_EVENT_FAILED = "linked_event_failed"
ALREADY_VOIDED = frozenset({"pending_transfer_expired", "pending_transfer_already_voided"})
BUSINESS_REJECTIONS = frozenset(
    {
        "exceeds_credits",
        "exceeds_debits",
        "id_already_failed",
        "pending_transfer_expired",
        "pending_transfer_already_posted",
        "pending_transfer_already_voided",
        "debit_account_already_closed",
        "credit_account_already_closed",
    }
)


class ResultCountMismatchError(ValueError):
    pass


@dataclass(frozen=True)
class Settlement:
    status: IntentStatus
    code: str | None
    outcomes: tuple[str, ...] = ()


def landed(transfer: TransferSpec, code: str) -> bool:
    return code in LANDED or (transfer.kind is TransferKind.VOID and code in ALREADY_VOIDED)


def chain_outcomes(codes: Sequence[str]) -> tuple[str, ...]:
    if codes[0] == "exists" and all(code == LINKED_EVENT_FAILED for code in codes[1:]):
        return ("exists",) * len(codes)
    return tuple(codes)


def settle_transfers(transfers: Sequence[TransferSpec], codes: Sequence[str]) -> Settlement:
    if len(transfers) != len(codes) or not codes:
        raise ResultCountMismatchError(f"{len(transfers)} transfers, {len(codes)} results")
    outcomes = chain_outcomes(codes)
    pairs = list(zip(transfers, outcomes, strict=True))
    failures = [
        code
        for transfer, code in pairs
        if not landed(transfer, code) and code != LINKED_EVENT_FAILED
    ]
    if not failures:
        if all(landed(transfer, code) for transfer, code in pairs):
            return Settlement(IntentStatus.APPLIED, None, outcomes)
        return Settlement(IntentStatus.FLAGGED, LINKED_EVENT_FAILED, outcomes)
    cause = failures[0]
    status = IntentStatus.REJECTED if cause in BUSINESS_REJECTIONS else IntentStatus.FLAGGED
    return Settlement(status, cause, outcomes)


def settle_accounts(codes: Sequence[str]) -> Settlement | None:
    for code in codes:
        if code not in LANDED:
            return Settlement(IntentStatus.FLAGGED, f"account:{code}")
    return None
