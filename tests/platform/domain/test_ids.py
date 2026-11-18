from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from haatly.platform.domain.ids import (
    MAX_UNIX_MILLIS,
    InvalidIdInputError,
    unix_millis,
    uuid7,
    uuid7_millis,
)

millis = st.integers(min_value=0, max_value=MAX_UNIX_MILLIS)
random_tails = st.binary(min_size=10, max_size=10)


@given(millis, random_tails)
def test_uuid7_has_version_and_variant_bits(unix_ms: int, tail: bytes) -> None:
    value = uuid7(unix_ms, tail)
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


@given(millis, random_tails)
def test_uuid7_round_trips_its_timestamp(unix_ms: int, tail: bytes) -> None:
    assert uuid7_millis(uuid7(unix_ms, tail)) == unix_ms


@given(millis, millis, random_tails, random_tails)
def test_uuid7_sorts_by_millisecond(first: int, second: int, a: bytes, b: bytes) -> None:
    if first < second:
        assert uuid7(first, a) < uuid7(second, b)


@pytest.mark.parametrize(
    ("unix_ms", "tail"),
    [(-1, bytes(10)), (MAX_UNIX_MILLIS + 1, bytes(10)), (0, bytes(9))],
)
def test_uuid7_rejects_bad_input(unix_ms: int, tail: bytes) -> None:
    with pytest.raises(InvalidIdInputError):
        uuid7(unix_ms, tail)


def test_unix_millis_reads_aware_datetimes() -> None:
    assert unix_millis(datetime(1970, 1, 1, 0, 0, 1, 500_000, tzinfo=UTC)) == 1500
