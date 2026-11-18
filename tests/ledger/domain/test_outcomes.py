import pytest
from hypothesis import given
from hypothesis import strategies as st

from haatly.ledger.domain.model import (
    IntentStatus,
    Ledger,
    TransferKind,
    TransferSpec,
    posting_of,
    voiding_of,
)
from haatly.ledger.domain.outcomes import (
    ResultCountMismatchError,
    Settlement,
    settle_accounts,
    settle_transfers,
)

HOLD = TransferSpec(1, 11, 12, 3, Ledger.INVENTORY, 1, TransferKind.PENDING, timeout_seconds=600)
RECEIPT = TransferSpec(2, 12, 11, 5, Ledger.INVENTORY, 1)
VOID = voiding_of(HOLD, 3)
POST = posting_of(HOLD, 4)


@pytest.mark.parametrize(
    ("transfers", "codes", "expected"),
    [
        ((RECEIPT, HOLD), ("created", "created"), Settlement(IntentStatus.APPLIED, None)),
        ((RECEIPT, HOLD), ("exists", "created"), Settlement(IntentStatus.APPLIED, None)),
        ((VOID,), ("pending_transfer_expired",), Settlement(IntentStatus.APPLIED, None)),
        ((VOID,), ("pending_transfer_already_voided",), Settlement(IntentStatus.APPLIED, None)),
        (
            (POST,),
            ("pending_transfer_expired",),
            Settlement(IntentStatus.REJECTED, "pending_transfer_expired"),
        ),
        (
            (RECEIPT, HOLD),
            ("linked_event_failed", "exceeds_credits"),
            Settlement(IntentStatus.REJECTED, "exceeds_credits"),
        ),
        ((HOLD,), ("id_already_failed",), Settlement(IntentStatus.REJECTED, "id_already_failed")),
        (
            (HOLD,),
            ("exists_with_different_amount",),
            Settlement(IntentStatus.FLAGGED, "exists_with_different_amount"),
        ),
        (
            (HOLD,),
            ("debit_account_not_found",),
            Settlement(IntentStatus.FLAGGED, "debit_account_not_found"),
        ),
        (
            (RECEIPT, HOLD),
            ("linked_event_failed", "linked_event_failed"),
            Settlement(IntentStatus.FLAGGED, "linked_event_failed"),
        ),
    ],
)
def test_transfer_results_settle_the_intent(
    transfers: tuple[TransferSpec, ...], codes: tuple[str, ...], expected: Settlement
) -> None:
    settlement = settle_transfers(transfers, codes)
    assert (settlement.status, settlement.code) == (expected.status, expected.code)
    assert settlement.outcomes == codes


@given(st.lists(st.sampled_from(["created", "exists"]), min_size=1, max_size=5))
def test_any_mix_of_created_and_exists_is_applied(codes: list[str]) -> None:
    transfers = tuple(
        TransferSpec(10 + n, 11, 12, 1, Ledger.INVENTORY, 1) for n in range(len(codes))
    )
    assert settle_transfers(transfers, tuple(codes)).status is IntentStatus.APPLIED


def test_a_resubmitted_chain_that_already_exists_is_applied() -> None:
    settlement = settle_transfers((RECEIPT, HOLD), ("exists", "linked_event_failed"))
    assert settlement == Settlement(IntentStatus.APPLIED, None, ("exists", "exists"))


def test_results_must_match_the_transfers_one_to_one() -> None:
    with pytest.raises(ResultCountMismatchError):
        settle_transfers((HOLD,), ())


@pytest.mark.parametrize("codes", [(), ("created",), ("created", "exists")])
def test_created_or_existing_accounts_let_transfers_proceed(codes: tuple[str, ...]) -> None:
    assert settle_accounts(codes) is None


def test_any_other_account_result_flags_the_intent() -> None:
    settlement = settle_accounts(("created", "exists_with_different_flags"))
    assert settlement == Settlement(IntentStatus.FLAGGED, "account:exists_with_different_flags", ())
