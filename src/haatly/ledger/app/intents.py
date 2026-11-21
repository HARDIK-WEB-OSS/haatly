from datetime import timedelta

from haatly.ledger.app.ports import LedgerUnitOfWork
from haatly.ledger.domain.model import Intent, IntentStatus, NewIntent
from haatly.platform.kernel import Clock, IdSource, NewJob, enqueue_job

APPLY_INTENT_JOB = "ledger.apply_intent"


class IntentConflictError(ValueError):
    def __init__(self) -> None:
        super().__init__("intent conflict")


def backstop_job(intent: Intent, delay: timedelta) -> NewJob:
    return NewJob(
        kind=APPLY_INTENT_JOB,
        payload={"intent_id": str(intent.id)},
        dedupe_key=f"{APPLY_INTENT_JOB}:{intent.id}",
        delay=delay,
    )


async def record_intent(
    uow: LedgerUnitOfWork, ids: IdSource, clock: Clock, new: NewIntent, backstop: timedelta
) -> Intent:
    existing = await uow.intents.find_by_reference(new.kind, new.reference_id)
    if existing is not None:
        if not existing.matches(new):
            raise IntentConflictError
        return existing
    intent = Intent(
        id=ids.new_id(),
        kind=new.kind,
        reference_id=new.reference_id,
        status=IntentStatus.PENDING,
        transfers=new.transfers,
        accounts=new.accounts,
        created_at=clock.now(),
    )
    await uow.intents.add(intent)
    await enqueue_job(uow, ids, clock, backstop_job(intent, backstop))
    return intent
