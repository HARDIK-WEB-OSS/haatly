import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class IdempotencyState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True)
class StoredResponse:
    status: int
    body: str


@dataclass(frozen=True)
class StoredRequest:
    request_hash: str
    state: IdempotencyState
    lease_expires_at: datetime
    response: StoredResponse | None = None


@dataclass(frozen=True)
class Proceed:
    pass


@dataclass(frozen=True)
class Replay:
    response: StoredResponse


@dataclass(frozen=True)
class InFlight:
    pass


@dataclass(frozen=True)
class BodyMismatch:
    pass


Decision = Proceed | Replay | InFlight | BodyMismatch


def request_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def decide(stored: StoredRequest | None, incoming_hash: str, now: datetime) -> Decision:
    if stored is None:
        return Proceed()
    if stored.request_hash != incoming_hash:
        return BodyMismatch()
    if stored.state is IdempotencyState.COMPLETED and stored.response is not None:
        return Replay(stored.response)
    if stored.lease_expires_at <= now:
        return Proceed()
    return InFlight()
