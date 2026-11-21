import logging
from datetime import timedelta
from uuid import UUID

from haatly.ledger.app.apply import IntentApplier
from haatly.ledger.app.intents import APPLY_INTENT_JOB
from haatly.ledger.app.reconciler import Reconciler
from haatly.platform.kernel import SYSTEM, Job, JobHandler, PeriodicTask

RECONCILE_JOB = "ledger.reconcile"

logger = logging.getLogger("haatly.ledger")


def reconcile_task(every: timedelta) -> PeriodicTask:
    return PeriodicTask(name=RECONCILE_JOB, every=every, kind=RECONCILE_JOB)


def ledger_job_handlers(applier: IntentApplier, reconciler: Reconciler) -> dict[str, JobHandler]:
    async def apply_intent(job: Job) -> None:
        await applier.apply(UUID(str(job.payload["intent_id"])), SYSTEM)

    async def reconcile(_: Job) -> None:
        report = await reconciler.run()
        if not report.clean:
            logger.warning(
                "ledger reconciliation found drift: open_drift=%d mismatches=%d",
                report.open_drift,
                len(report.mismatches),
            )

    return {APPLY_INTENT_JOB: apply_intent, RECONCILE_JOB: reconcile}
