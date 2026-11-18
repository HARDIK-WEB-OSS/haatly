from datetime import UTC, datetime, timedelta
from uuid import UUID

from haatly.platform.domain.backoff import BackoffPolicy
from haatly.platform.domain.events import Subscriptions, delivery_dedupe_key
from haatly.platform.domain.jobs import Bury, Job, RetryAt, after_failure

NOW = datetime(2026, 6, 1, tzinfo=UTC)
JOB_ID = UUID("018f0000-0000-7000-8000-000000000003")
POLICY = BackoffPolicy(base=timedelta(seconds=10), cap=timedelta(minutes=5))


def job(attempts: int, max_attempts: int = 3) -> Job:
    return Job(JOB_ID, "kind", {}, attempts, max_attempts)


def test_failure_before_the_last_attempt_retries_with_backoff() -> None:
    assert after_failure(job(2), NOW, POLICY, lambda: 1.0) == RetryAt(NOW + timedelta(seconds=20))


def test_failure_on_the_last_attempt_buries_the_job() -> None:
    assert after_failure(job(3), NOW, POLICY, lambda: 1.0) == Bury()


def test_subscriptions_list_consumers_per_topic() -> None:
    subscriptions = Subscriptions({"order.paid": ("ledger", "notifications")})
    assert subscriptions.consumers_of("order.paid") == ("ledger", "notifications")
    assert subscriptions.consumers_of("order.shipped") == ()


def test_delivery_dedupe_key_names_consumer_and_event() -> None:
    assert delivery_dedupe_key("ledger", JOB_ID) == f"deliver:ledger:{JOB_ID}"
