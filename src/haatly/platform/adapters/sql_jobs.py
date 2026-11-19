from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, Row, and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.platform.adapters.database import is_unique_violation
from haatly.platform.adapters.tables import jobs
from haatly.platform.app.ports import DeadJob
from haatly.platform.domain.jobs import Job, JobStatus, NewJob

DEDUPE_CONSTRAINT = "uq_jobs_dedupe_key"


def job_from_row(row: Row[tuple[UUID, str, dict[str, object], int, int]]) -> Job:
    return Job(
        id=row.id,
        kind=row.kind,
        payload=row.payload,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
    )


def owned_by(job: Job) -> ColumnElement[bool]:
    return and_(
        jobs.c.id == job.id, jobs.c.status == JobStatus.RUNNING, jobs.c.attempts == job.attempts
    )


class SqlJobRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def enqueue(self, job_id: UUID, job: NewJob, now: datetime) -> bool:
        statement = insert(jobs).values(
            id=job_id,
            kind=job.kind,
            payload=dict(job.payload),
            dedupe_key=job.dedupe_key,
            status=JobStatus.QUEUED,
            attempts=0,
            max_attempts=job.max_attempts,
            run_after=now + job.delay,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self.connection.begin_nested():
                await self.connection.execute(statement)
        except IntegrityError as error:
            if is_unique_violation(error, DEDUPE_CONSTRAINT):
                return False
            raise
        return True

    async def claim(self, now: datetime, lease: timedelta, limit: int) -> list[Job]:
        ready = or_(
            and_(jobs.c.status == JobStatus.QUEUED, jobs.c.run_after <= now),
            and_(jobs.c.status == JobStatus.RUNNING, jobs.c.locked_until <= now),
        )
        candidates = (
            select(jobs.c.id)
            .where(ready)
            .order_by(jobs.c.run_after, jobs.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        statement = (
            update(jobs)
            .where(jobs.c.id.in_(candidates))
            .values(
                status=JobStatus.RUNNING,
                attempts=jobs.c.attempts + 1,
                locked_until=now + lease,
                updated_at=now,
            )
            .returning(jobs.c.id, jobs.c.kind, jobs.c.payload, jobs.c.attempts, jobs.c.max_attempts)
        )
        rows = (await self.connection.execute(statement)).all()
        return sorted((job_from_row(row) for row in rows), key=lambda job: job.id)

    async def finish(self, job: Job, now: datetime, **values: object) -> bool:
        statement = (
            update(jobs).where(owned_by(job)).values(updated_at=now, **values).returning(jobs.c.id)
        )
        return (await self.connection.execute(statement)).first() is not None

    async def mark_succeeded(self, job: Job, now: datetime) -> bool:
        return await self.finish(job, now, status=JobStatus.SUCCEEDED, locked_until=None)

    async def reschedule(self, job: Job, run_after: datetime, error: str, now: datetime) -> bool:
        return await self.finish(
            job,
            now,
            status=JobStatus.QUEUED,
            locked_until=None,
            run_after=run_after,
            last_error=error,
        )

    async def bury(self, job: Job, error: str, now: datetime) -> bool:
        return await self.finish(
            job, now, status=JobStatus.DEAD, locked_until=None, last_error=error
        )

    async def list_dead(self, limit: int) -> list[DeadJob]:
        statement = (
            select(jobs.c.id, jobs.c.kind, jobs.c.attempts, jobs.c.last_error, jobs.c.updated_at)
            .where(jobs.c.status == JobStatus.DEAD)
            .order_by(jobs.c.updated_at.desc(), jobs.c.id)
            .limit(limit)
        )
        rows = (await self.connection.execute(statement)).all()
        return [
            DeadJob(row.id, row.kind, row.attempts, row.last_error, row.updated_at) for row in rows
        ]

    async def revive(self, job_id: UUID, now: datetime) -> bool:
        statement = (
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.status == JobStatus.DEAD)
            .values(status=JobStatus.QUEUED, attempts=0, run_after=now, updated_at=now)
            .returning(jobs.c.id)
        )
        return (await self.connection.execute(statement)).first() is not None
