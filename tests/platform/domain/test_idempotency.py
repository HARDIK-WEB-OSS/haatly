from datetime import UTC, datetime, timedelta

import pytest

from haatly.platform.domain.idempotency import (
    BodyMismatch,
    Decision,
    IdempotencyState,
    InFlight,
    Proceed,
    Replay,
    StoredRequest,
    StoredResponse,
    decide,
    request_hash,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)
HASH = request_hash(b"cart-1")
OTHER_HASH = "0" * 64
RESPONSE = StoredResponse(201, "order-x")


def stored(
    state: IdempotencyState, *, lease: timedelta = timedelta(minutes=1), body_hash: str = HASH
) -> StoredRequest:
    response = RESPONSE if state is IdempotencyState.COMPLETED else None
    return StoredRequest(body_hash, state, NOW + lease, response)


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, Proceed()),
        (stored(IdempotencyState.IN_PROGRESS), InFlight()),
        (stored(IdempotencyState.IN_PROGRESS, lease=timedelta(0)), Proceed()),
        (stored(IdempotencyState.COMPLETED), Replay(RESPONSE)),
        (stored(IdempotencyState.COMPLETED, lease=-timedelta(days=1)), Replay(RESPONSE)),
        (stored(IdempotencyState.IN_PROGRESS, body_hash=OTHER_HASH), BodyMismatch()),
        (stored(IdempotencyState.COMPLETED, body_hash=OTHER_HASH), BodyMismatch()),
    ],
)
def test_decide(existing: StoredRequest | None, expected: Decision) -> None:
    assert decide(existing, HASH, NOW) == expected


def test_request_hash_is_hex_sha256() -> None:
    assert request_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
