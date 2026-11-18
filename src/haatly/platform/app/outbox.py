from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from haatly.platform.app.ports import EventConsumer, IdSource, UnitOfWork, UnitOfWorkFactory
from haatly.platform.domain.clock import Clock
from haatly.platform.domain.events import (
    DELIVER_EVENT_JOB,
    NewEvent,
    OutboxEvent,
    Subscriptions,
    delivery_dedupe_key,
)
from haatly.platform.domain.jobs import Job, NewJob
from haatly.platform.domain.tenancy import SYSTEM


class UnknownConsumerError(LookupError):
    pass


class MissingEventError(LookupError):
    pass


async def record_event(uow: UnitOfWork, ids: IdSource, clock: Clock, event: NewEvent) -> UUID:
    stored = OutboxEvent(ids.new_id(), event.topic, event.aggregate_id, event.payload, clock.now())
    await uow.outbox.append(stored)
    return stored.id


def delivery_job(consumer: str, event: OutboxEvent) -> NewJob:
    return NewJob(
        kind=DELIVER_EVENT_JOB,
        payload={"consumer": consumer, "event_id": str(event.id)},
        dedupe_key=delivery_dedupe_key(consumer, event.id),
    )


@dataclass
class OutboxRelay:
    uow_factory: UnitOfWorkFactory
    ids: IdSource
    clock: Clock
    subscriptions: Subscriptions
    batch_size: int = 100

    async def relay_batch(self) -> int:
        async with self.uow_factory(SYSTEM) as uow:
            events = await uow.outbox.claim_unpublished(self.batch_size)
            now = self.clock.now()
            for event in events:
                for consumer in self.subscriptions.consumers_of(event.topic):
                    await uow.jobs.enqueue(self.ids.new_id(), delivery_job(consumer, event), now)
            await uow.outbox.mark_published([event.id for event in events], now)
        return len(events)


@dataclass
class EventDelivery:
    uow_factory: UnitOfWorkFactory
    clock: Clock
    consumers: Mapping[str, EventConsumer]

    async def __call__(self, job: Job) -> None:
        consumer_name = str(job.payload["consumer"])
        consumer = self.consumers.get(consumer_name)
        if consumer is None:
            raise UnknownConsumerError(consumer_name)
        event_id = UUID(str(job.payload["event_id"]))
        async with self.uow_factory(SYSTEM) as uow:
            event = await uow.outbox.get(event_id)
            if event is None:
                raise MissingEventError(str(event_id))
            if await uow.processed.mark(consumer_name, event_id, self.clock.now()):
                await consumer(uow, event)
