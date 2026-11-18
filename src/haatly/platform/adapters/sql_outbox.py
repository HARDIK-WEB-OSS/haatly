from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Row, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.platform.adapters.tables import outbox_events, processed_events
from haatly.platform.domain.events import OutboxEvent

EVENT_COLUMNS = (
    outbox_events.c.id,
    outbox_events.c.topic,
    outbox_events.c.aggregate_id,
    outbox_events.c.payload,
    outbox_events.c.occurred_at,
)


def event_from_row(row: Row[tuple[UUID, str, UUID, dict[str, object], datetime]]) -> OutboxEvent:
    return OutboxEvent(row.id, row.topic, row.aggregate_id, row.payload, row.occurred_at)


class SqlOutboxRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def append(self, event: OutboxEvent) -> None:
        await self.connection.execute(
            insert(outbox_events).values(
                id=event.id,
                topic=event.topic,
                aggregate_id=event.aggregate_id,
                payload=dict(event.payload),
                occurred_at=event.occurred_at,
            )
        )

    async def claim_unpublished(self, limit: int) -> list[OutboxEvent]:
        statement = (
            select(*EVENT_COLUMNS)
            .where(outbox_events.c.published_at.is_(None))
            .order_by(outbox_events.c.occurred_at, outbox_events.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [event_from_row(row) for row in (await self.connection.execute(statement)).all()]

    async def mark_published(self, event_ids: Sequence[UUID], now: datetime) -> None:
        if not event_ids:
            return
        await self.connection.execute(
            update(outbox_events).where(outbox_events.c.id.in_(event_ids)).values(published_at=now)
        )

    async def get(self, event_id: UUID) -> OutboxEvent | None:
        statement = select(*EVENT_COLUMNS).where(outbox_events.c.id == event_id)
        row = (await self.connection.execute(statement)).first()
        return None if row is None else event_from_row(row)


class SqlProcessedEventRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def mark(self, consumer: str, event_id: UUID, now: datetime) -> bool:
        statement = (
            insert(processed_events)
            .values(consumer=consumer, event_id=event_id, processed_at=now)
            .on_conflict_do_nothing()
            .returning(processed_events.c.event_id)
        )
        return (await self.connection.execute(statement)).first() is not None
