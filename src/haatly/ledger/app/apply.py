from dataclasses import dataclass
from uuid import UUID

from haatly.ledger.app.ports import LedgerGateway, LedgerUnitOfWorkFactory
from haatly.ledger.domain.model import Intent, IntentStatus
from haatly.ledger.domain.outcomes import Settlement, settle_accounts, settle_transfers
from haatly.platform.kernel import SYSTEM, Clock, IdSource, NewEvent, record_event

INTENT_SETTLED = "ledger.intent_settled"


class UnknownIntentError(LookupError):
    pass


def settled_event(intent: Intent, settlement: Settlement) -> NewEvent:
    return NewEvent(
        INTENT_SETTLED,
        intent.id,
        {
            "kind": intent.kind,
            "reference_id": str(intent.reference_id),
            "status": settlement.status.value,
            "code": settlement.code,
        },
    )


@dataclass
class IntentApplier:
    uow_factory: LedgerUnitOfWorkFactory
    gateway: LedgerGateway
    ids: IdSource
    clock: Clock

    async def load(self, intent_id: UUID) -> Intent:
        async with self.uow_factory(SYSTEM) as uow:
            intent = await uow.intents.get(intent_id)
        if intent is None:
            raise UnknownIntentError(str(intent_id))
        return intent

    async def submit(self, intent: Intent) -> Settlement:
        refused = settle_accounts(await self.gateway.create_accounts(intent.accounts))
        if refused is not None:
            return refused
        codes = await self.gateway.create_transfers(intent.id, intent.transfers)
        return settle_transfers(intent.transfers, codes)

    async def apply(self, intent_id: UUID) -> IntentStatus:
        intent = await self.load(intent_id)
        if intent.status is not IntentStatus.PENDING:
            return intent.status
        settlement = await self.submit(intent)
        async with self.uow_factory(SYSTEM) as uow:
            if await uow.intents.settle(intent.id, settlement, self.clock.now()):
                await record_event(uow, self.ids, self.clock, settled_event(intent, settlement))
        return settlement.status
