from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from haatly.platform.domain.audit import AuditEntry
from haatly.platform.domain.events import OutboxEvent
from haatly.platform.domain.idempotency import StoredRequest, StoredResponse
from haatly.platform.domain.jobs import Job, NewJob
from haatly.platform.domain.tenancy import TenantContext


@dataclass(frozen=True)
class DeadJob:
    id: UUID
    kind: str
    attempts: int
    last_error: str | None
    updated_at: datetime


class JobRepository(Protocol):
    async def enqueue(self, job_id: UUID, job: NewJob, now: datetime) -> bool: ...

    async def claim(self, now: datetime, lease: timedelta, limit: int) -> list[Job]: ...

    async def mark_succeeded(self, job: Job, now: datetime) -> bool: ...

    async def reschedule(
        self, job: Job, run_after: datetime, error: str, now: datetime
    ) -> bool: ...

    async def bury(self, job: Job, error: str, now: datetime) -> bool: ...

    async def list_dead(self, limit: int) -> list[DeadJob]: ...

    async def revive(self, job_id: UUID, now: datetime) -> bool: ...


class OutboxRepository(Protocol):
    async def append(self, event: OutboxEvent) -> None: ...

    async def claim_unpublished(self, limit: int) -> list[OutboxEvent]: ...

    async def mark_published(self, event_ids: Sequence[UUID], now: datetime) -> None: ...

    async def get(self, event_id: UUID) -> OutboxEvent | None: ...


class ProcessedEventRepository(Protocol):
    async def mark(self, consumer: str, event_id: UUID, now: datetime) -> bool: ...


class IdempotencyRepository(Protocol):
    async def lock(self, scope: str, key: str) -> None: ...

    async def find(self, scope: str, key: str) -> StoredRequest | None: ...

    async def start(
        self, scope: str, key: str, request_hash: str, lease_expires_at: datetime, now: datetime
    ) -> None: ...

    async def complete(
        self, scope: str, key: str, response: StoredResponse, now: datetime
    ) -> None: ...

    async def abandon(self, scope: str, key: str) -> None: ...


class AuditRepository(Protocol):
    async def append(self, entry: AuditEntry) -> None: ...


class RateLimitRepository(Protocol):
    async def hit(self, key: str, window_start: datetime) -> int: ...


class UnitOfWork(Protocol):
    @property
    def context(self) -> TenantContext: ...

    @property
    def jobs(self) -> JobRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    @property
    def processed(self) -> ProcessedEventRepository: ...

    @property
    def idempotency(self) -> IdempotencyRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def rate_limits(self) -> RateLimitRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> UnitOfWork: ...


class IdSource(Protocol):
    def new_id(self) -> UUID: ...


class LeaderLock(Protocol):
    async def try_acquire(self) -> bool: ...

    async def release(self) -> None: ...


class Wakeup(Protocol):
    async def wait(self, max_wait: float) -> None: ...


type JobHandler = Callable[[Job], Awaitable[None]]
type EventConsumer = Callable[[UnitOfWork, OutboxEvent], Awaitable[None]]
type UnitRandom = Callable[[], float]
