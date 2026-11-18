import asyncio
from datetime import timedelta
from uuid import UUID

import pytest

from haatly.platform.public import (
    ActorRole,
    BodyMismatch,
    FrozenClock,
    IdempotencyGate,
    InFlight,
    InvalidIdempotencyKeyError,
    Proceed,
    Replay,
    SqlUnitOfWorkFactory,
    StoredResponse,
    TenantContext,
)

pytestmark = pytest.mark.anyio

BUYER = TenantContext(ActorRole.BUYER, UUID("018f0000-0000-7000-8000-0000000000d1"))
SCOPE = "checkout:018f0000-0000-7000-8000-0000000000d1"
BODY = b'{"variant": "v1", "quantity": 1}'
CREATED = StoredResponse(201, "order-1")


def gate(uow_factory: SqlUnitOfWorkFactory, clock: FrozenClock) -> IdempotencyGate:
    return IdempotencyGate(uow_factory, clock, lease=timedelta(minutes=2))


async def test_first_request_proceeds_and_a_running_duplicate_is_in_flight(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    keys = gate(uow_factory, FrozenClock())
    assert await keys.begin(BUYER, SCOPE, "key-1", BODY) == Proceed()
    assert await keys.begin(BUYER, SCOPE, "key-1", BODY) == InFlight()


async def test_same_key_with_a_different_body_is_a_mismatch(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    keys = gate(uow_factory, FrozenClock())
    await keys.begin(BUYER, SCOPE, "key-1", BODY)
    assert await keys.begin(BUYER, SCOPE, "key-1", BODY + b" ") == BodyMismatch()
    await keys.complete(BUYER, SCOPE, "key-1", CREATED)
    assert await keys.begin(BUYER, SCOPE, "key-1", b"{}") == BodyMismatch()


async def test_completed_request_replays_its_response(uow_factory: SqlUnitOfWorkFactory) -> None:
    keys = gate(uow_factory, FrozenClock())
    await keys.begin(BUYER, SCOPE, "key-1", BODY)
    await keys.complete(BUYER, SCOPE, "key-1", CREATED)
    assert await keys.begin(BUYER, SCOPE, "key-1", BODY) == Replay(CREATED)


async def test_abandoned_or_expired_requests_can_be_retried(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    clock = FrozenClock()
    keys = gate(uow_factory, clock)
    await keys.begin(BUYER, SCOPE, "key-1", BODY)
    await keys.abandon(BUYER, SCOPE, "key-1")
    assert await keys.begin(BUYER, SCOPE, "key-1", BODY) == Proceed()
    clock.advance(timedelta(minutes=2))
    assert await keys.begin(BUYER, SCOPE, "key-1", BODY) == Proceed()


async def test_scopes_keep_identical_keys_apart(uow_factory: SqlUnitOfWorkFactory) -> None:
    keys = gate(uow_factory, FrozenClock())
    await keys.begin(BUYER, SCOPE, "key-1", BODY)
    assert await keys.begin(BUYER, "checkout:someone-else", "key-1", b"other") == Proceed()


async def test_concurrent_duplicates_admit_exactly_one(uow_factory: SqlUnitOfWorkFactory) -> None:
    keys = gate(uow_factory, FrozenClock())
    decisions = await asyncio.gather(*(keys.begin(BUYER, SCOPE, "race", BODY) for _ in range(20)))
    assert decisions.count(Proceed()) == 1
    assert decisions.count(InFlight()) == 19


@pytest.mark.parametrize("key", ["", "x" * 256, "line\nbreak", "nul\x00"])
async def test_malformed_keys_are_rejected(uow_factory: SqlUnitOfWorkFactory, key: str) -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        await gate(uow_factory, FrozenClock()).begin(BUYER, SCOPE, key, BODY)
