from dataclasses import dataclass
from uuid import UUID

from haatly.ledger.public import (
    AccountFlag,
    AccountSpec,
    Ledger,
    NewIntent,
    TransferKind,
    TransferSpec,
    derive_id,
    posting_of,
    voiding_of,
)
from haatly.platform.public import IdSource

RECEIVED_CODE = 1
STOCK_CODE = 2
SOLD_CODE = 3
RECEIPT_CODE = 10
HOLD_CODE = 11


@dataclass(frozen=True)
class Inventory:
    variant_id: UUID
    received: AccountSpec
    stock: AccountSpec
    sold: AccountSpec
    hold_timeout_seconds: int
    ids: IdSource

    @classmethod
    def fresh(cls, ids: IdSource, hold_timeout_seconds: int) -> "Inventory":
        variant_id = ids.new_id()
        return cls(
            variant_id=variant_id,
            received=AccountSpec(
                derive_id(variant_id, "account.received"), Ledger.INVENTORY, RECEIVED_CODE
            ),
            stock=AccountSpec(
                derive_id(variant_id, "account.stock"),
                Ledger.INVENTORY,
                STOCK_CODE,
                frozenset({AccountFlag.DEBITS_MUST_NOT_EXCEED_CREDITS}),
            ),
            sold=AccountSpec(derive_id(variant_id, "account.sold"), Ledger.INVENTORY, SOLD_CODE),
            hold_timeout_seconds=hold_timeout_seconds,
            ids=ids,
        )

    @property
    def accounts(self) -> tuple[AccountSpec, ...]:
        return (self.received, self.stock, self.sold)

    def receive(self, units: int) -> NewIntent:
        reference = self.ids.new_id()
        transfer = TransferSpec(
            derive_id(reference, "inventory.receive"),
            self.received.id,
            self.stock.id,
            units,
            Ledger.INVENTORY,
            RECEIPT_CODE,
        )
        return NewIntent("inventory.receive", reference, (transfer,), self.accounts)

    def hold(self, units: int, timeout_seconds: int | None = None) -> NewIntent:
        reference = self.ids.new_id()
        transfer = TransferSpec(
            derive_id(reference, "inventory.hold"),
            self.stock.id,
            self.sold.id,
            units,
            Ledger.INVENTORY,
            HOLD_CODE,
            TransferKind.PENDING,
            timeout_seconds=(
                self.hold_timeout_seconds if timeout_seconds is None else timeout_seconds
            ),
        )
        return NewIntent("inventory.hold", reference, (transfer,), self.accounts)

    def capture(self, hold: NewIntent) -> NewIntent:
        transfer = posting_of(hold.transfers[0], derive_id(hold.reference_id, "inventory.capture"))
        return NewIntent("inventory.capture", hold.reference_id, (transfer,))

    def release(self, hold: NewIntent) -> NewIntent:
        transfer = voiding_of(hold.transfers[0], derive_id(hold.reference_id, "inventory.release"))
        return NewIntent("inventory.release", hold.reference_id, (transfer,))
