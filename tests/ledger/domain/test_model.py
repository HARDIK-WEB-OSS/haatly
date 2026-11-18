from datetime import UTC, datetime
from uuid import UUID

import pytest

from haatly.ledger.domain.ids import U128_MAX
from haatly.ledger.domain.model import (
    MAX_AMOUNT,
    MAX_TIMEOUT_SECONDS,
    AccountFlag,
    AccountSpec,
    Intent,
    IntentStatus,
    InvalidLedgerSpecError,
    Ledger,
    NewIntent,
    TransferKind,
    TransferSpec,
    posting_of,
    voiding_of,
)

REFERENCE = UUID("018f2f3e-5a00-7000-8000-000000000001")
STOCK = AccountSpec(
    11, Ledger.INVENTORY, 1, frozenset({AccountFlag.DEBITS_MUST_NOT_EXCEED_CREDITS})
)
SOLD = AccountSpec(12, Ledger.INVENTORY, 2)
CASH = AccountSpec(13, Ledger.INR, 1)
HOLD = TransferSpec(
    101, STOCK.id, SOLD.id, 3, Ledger.INVENTORY, 1, TransferKind.PENDING, timeout_seconds=600
)


def receipt(transfer_id: int = 100, amount: int = 5) -> TransferSpec:
    return TransferSpec(transfer_id, SOLD.id, STOCK.id, amount, Ledger.INVENTORY, 1)


def test_posting_and_voiding_copy_the_pending_transfer() -> None:
    post = posting_of(HOLD, 102)
    void = voiding_of(HOLD, 103)
    assert (post.kind, post.pending_id, post.amount) == (TransferKind.POST, HOLD.id, 3)
    assert (void.kind, void.pending_id, void.amount) == (TransferKind.VOID, HOLD.id, 3)
    assert (post.debit_account_id, post.credit_account_id) == (STOCK.id, SOLD.id)
    assert post.touches == ()
    assert HOLD.touches == (STOCK.id, SOLD.id)


@pytest.mark.parametrize(
    "build",
    [
        lambda: AccountSpec(0, Ledger.INR, 1),
        lambda: AccountSpec(U128_MAX, Ledger.INR, 1),
        lambda: AccountSpec(1, Ledger.INR, 0),
        lambda: AccountSpec(1, Ledger.INR, 65536),
        lambda: AccountSpec(
            1,
            Ledger.INR,
            1,
            frozenset(
                {
                    AccountFlag.DEBITS_MUST_NOT_EXCEED_CREDITS,
                    AccountFlag.CREDITS_MUST_NOT_EXCEED_DEBITS,
                }
            ),
        ),
    ],
)
def test_invalid_accounts_are_refused(build: object) -> None:
    with pytest.raises(InvalidLedgerSpecError):
        build()  # type: ignore[operator]


@pytest.mark.parametrize(
    "build",
    [
        lambda: TransferSpec(0, 1, 2, 1, Ledger.INR, 1),
        lambda: TransferSpec(5, 1, 2, 1, Ledger.INR, 0),
        lambda: TransferSpec(5, 1, 2, 0, Ledger.INR, 1),
        lambda: TransferSpec(5, 1, 2, MAX_AMOUNT + 1, Ledger.INR, 1),
        lambda: TransferSpec(5, 1, 1, 1, Ledger.INR, 1),
        lambda: TransferSpec(5, 0, 2, 1, Ledger.INR, 1),
        lambda: TransferSpec(5, 1, 2, 1, Ledger.INR, 1, pending_id=4),
        lambda: TransferSpec(5, 1, 2, 1, Ledger.INR, 1, timeout_seconds=1),
        lambda: TransferSpec(
            5, 1, 2, 1, Ledger.INR, 1, TransferKind.PENDING, timeout_seconds=MAX_TIMEOUT_SECONDS + 1
        ),
        lambda: TransferSpec(5, 1, 2, 1, Ledger.INR, 1, TransferKind.PENDING, timeout_seconds=-1),
        lambda: TransferSpec(5, 1, 2, 1, Ledger.INR, 1, TransferKind.POST),
        lambda: TransferSpec(5, 1, 2, 1, Ledger.INR, 1, TransferKind.VOID, pending_id=5),
        lambda: TransferSpec(5, 1, 2, -1, Ledger.INR, 1, TransferKind.VOID, pending_id=4),
    ],
)
def test_invalid_transfers_are_refused(build: object) -> None:
    with pytest.raises(InvalidLedgerSpecError):
        build()  # type: ignore[operator]


def test_a_well_formed_intent_is_accepted() -> None:
    intent = NewIntent("inventory.hold", REFERENCE, (receipt(), HOLD), (STOCK, SOLD))
    assert intent.transfer_ids == (100, HOLD.id)


@pytest.mark.parametrize(
    ("kind", "transfers", "accounts"),
    [
        ("", (receipt(),), (STOCK, SOLD)),
        ("x" * 101, (receipt(),), (STOCK, SOLD)),
        ("inventory.hold", (), ()),
        ("inventory.hold", tuple(receipt(100 + n) for n in range(101)), (STOCK, SOLD)),
        ("inventory.hold", (receipt(), receipt()), (STOCK, SOLD)),
        ("inventory.hold", (receipt(),), (STOCK, SOLD, STOCK)),
        ("inventory.hold", (receipt(),), (STOCK,)),
        ("inventory.hold", (receipt(),), (STOCK, AccountSpec(SOLD.id, Ledger.INR, 2))),
        ("inventory.hold", (voiding_of(HOLD, 103), receipt()), (STOCK, SOLD)),
    ],
)
def test_malformed_intents_are_refused(
    kind: str, transfers: tuple[TransferSpec, ...], accounts: tuple[AccountSpec, ...]
) -> None:
    with pytest.raises(InvalidLedgerSpecError):
        NewIntent(kind, REFERENCE, transfers, accounts)


def test_a_void_may_stand_alone_without_accounts() -> None:
    assert NewIntent("inventory.release", REFERENCE, (voiding_of(HOLD, 103),)).accounts == ()


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("created", (103,)), ("exists", (103,)), ("pending_transfer_expired", ())],
)
def test_an_intent_expects_only_transfers_that_landed_in_the_ledger(
    outcome: str, expected: tuple[int, ...]
) -> None:
    intent = Intent(
        id=REFERENCE,
        kind="inventory.release",
        reference_id=REFERENCE,
        status=IntentStatus.APPLIED,
        transfers=(voiding_of(HOLD, 103),),
        accounts=(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        outcomes=(outcome,),
    )
    assert intent.expected_in_ledger == expected
