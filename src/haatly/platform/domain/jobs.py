from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from haatly.platform.domain.backoff import BackoffPolicy

type JsonValue = str | int | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = Mapping[str, JsonValue]


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD = "dead"


@dataclass(frozen=True)
class NewJob:
    kind: str
    payload: JsonObject = field(default_factory=dict)
    dedupe_key: str | None = None
    max_attempts: int = 10
    delay: timedelta = timedelta(0)


@dataclass(frozen=True)
class Job:
    id: UUID
    kind: str
    payload: JsonObject
    attempts: int
    max_attempts: int


@dataclass(frozen=True)
class RetryAt:
    run_after: datetime


@dataclass(frozen=True)
class Bury:
    pass


def after_failure(
    job: Job, now: datetime, policy: BackoffPolicy, unit_random: Callable[[], float]
) -> RetryAt | Bury:
    if job.attempts >= job.max_attempts:
        return Bury()
    return RetryAt(now + policy.delay(job.attempts, unit_random))
