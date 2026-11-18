from uuid import UUID

import pytest

from haatly.platform.app.outbox import MissingEventError, UnknownConsumerError, delivery_job
from haatly.platform.public import (
    DELIVER_EVENT_JOB,
    SYSTEM,
    EventDelivery,
    FrozenClock,
    Job,
    JobRunner,
    NewEvent,
    OutboxEvent,
    OutboxRelay,
    SqlUnitOfWorkFactory,
    Subscriptions,
    UnitOfWork,
    Uuid7Source,
    record_event,
)

pytestmark = pytest.mark.anyio

AGGREGATE = UUID("018f0000-0000-7000-8000-0000000000c1")
SUBSCRIPTIONS = Subscriptions({"order.paid": ("ledger", "notifications")})


class Recorder:
    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []

    async def __call__(self, _: UnitOfWork, event: OutboxEvent) -> None:
        self.events.append(event)


async def publish(uow_factory: SqlUnitOfWorkFactory, clock: FrozenClock, topic: str) -> UUID:
    async with uow_factory(SYSTEM) as uow:
        return await record_event(
            uow, Uuid7Source(clock), clock, NewEvent(topic, AGGREGATE, {"amount_paise": 125_00})
        )


async def test_relay_fans_out_one_delivery_job_per_consumer(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    clock = FrozenClock()
    await publish(uow_factory, clock, "order.paid")
    await publish(uow_factory, clock, "order.unwatched")
    relay = OutboxRelay(uow_factory, Uuid7Source(clock), clock, SUBSCRIPTIONS)
    assert await relay.relay_batch() == 2
    assert await relay.relay_batch() == 0
    async with uow_factory(SYSTEM) as uow:
        jobs = await uow.jobs.claim(clock.now(), clock.now() - clock.now(), 10)
    assert sorted(str(job.payload["consumer"]) for job in jobs) == ["ledger", "notifications"]


async def test_event_rolled_back_with_its_transaction_is_never_relayed(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    clock = FrozenClock()

    async def record_then_fail() -> None:
        async with uow_factory(SYSTEM) as uow:
            await record_event(uow, Uuid7Source(clock), clock, NewEvent("order.paid", AGGREGATE))
            raise RuntimeError

    with pytest.raises(RuntimeError):
        await record_then_fail()
    relay = OutboxRelay(uow_factory, Uuid7Source(clock), clock, SUBSCRIPTIONS)
    assert await relay.relay_batch() == 0


async def test_consumer_sees_each_event_once_even_if_delivery_repeats(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    clock = FrozenClock()
    event_id = await publish(uow_factory, clock, "order.paid")
    ledger = Recorder()
    delivery = EventDelivery(uow_factory, clock, {"ledger": ledger})
    async with uow_factory(SYSTEM) as uow:
        event = await uow.outbox.get(event_id)
    assert event is not None
    job = Job(event_id, DELIVER_EVENT_JOB, delivery_job("ledger", event).payload, 1, 10)
    await delivery(job)
    await delivery(job)
    assert [seen.payload for seen in ledger.events] == [{"amount_paise": 125_00}]


async def test_relayed_events_reach_consumers_through_the_runner(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    clock = FrozenClock()
    await publish(uow_factory, clock, "order.paid")
    ledger, notifications = Recorder(), Recorder()
    delivery = EventDelivery(uow_factory, clock, {"ledger": ledger, "notifications": notifications})
    await OutboxRelay(uow_factory, Uuid7Source(clock), clock, SUBSCRIPTIONS).relay_batch()
    runner = JobRunner(uow_factory, clock, {DELIVER_EVENT_JOB: delivery}, lambda: 0.0)
    assert await runner.run_batch() == 2
    assert len(ledger.events) == len(notifications.events) == 1


async def test_delivery_rejects_unknown_consumers_and_missing_events(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    clock = FrozenClock()
    delivery = EventDelivery(uow_factory, clock, {"ledger": Recorder()})
    missing = str(AGGREGATE)
    with pytest.raises(UnknownConsumerError):
        await delivery(
            Job(AGGREGATE, DELIVER_EVENT_JOB, {"consumer": "nobody", "event_id": missing}, 1, 1)
        )
    with pytest.raises(MissingEventError):
        await delivery(
            Job(AGGREGATE, DELIVER_EVENT_JOB, {"consumer": "ledger", "event_id": missing}, 1, 1)
        )
