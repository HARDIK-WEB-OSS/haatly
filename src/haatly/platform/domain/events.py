from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from haatly.platform.domain.jobs import JsonObject

DELIVER_EVENT_JOB = "platform.deliver_event"


@dataclass(frozen=True)
class NewEvent:
    topic: str
    aggregate_id: UUID
    payload: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class OutboxEvent:
    id: UUID
    topic: str
    aggregate_id: UUID
    payload: JsonObject
    occurred_at: datetime


@dataclass(frozen=True)
class Subscriptions:
    consumers_by_topic: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def consumers_of(self, topic: str) -> tuple[str, ...]:
        return self.consumers_by_topic.get(topic, ())


def delivery_dedupe_key(consumer: str, event_id: UUID) -> str:
    return f"deliver:{consumer}:{event_id}"
