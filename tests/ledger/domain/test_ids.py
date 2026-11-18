import hashlib
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from haatly.ledger.domain.ids import (
    LOW_BITS,
    U128_MAX,
    NotUuid7Error,
    ReservedIdError,
    compose_id,
    derive_id,
    id_from_uuid,
    uuid_from_id,
)

MAX_MILLIS = (1 << 48) - 1
operations = st.text(min_size=1, max_size=40)


@st.composite
def uuid7s(draw: st.DrawFn) -> UUID:
    millis = draw(st.integers(min_value=1, max_value=MAX_MILLIS))
    tail = bytearray(draw(st.binary(min_size=10, max_size=10)))
    tail[0] = 0x70 | (tail[0] & 0x0F)
    tail[2] = 0x80 | (tail[2] & 0x3F)
    return UUID(bytes=millis.to_bytes(6, "big") + bytes(tail))


@given(uuid7s(), operations)
def test_the_same_operation_always_gets_the_same_id(domain_id: UUID, operation: str) -> None:
    assert derive_id(domain_id, operation) == derive_id(UUID(str(domain_id)), operation)


@given(uuid7s(), operations)
def test_the_top_48_bits_are_the_domain_timestamp(domain_id: UUID, operation: str) -> None:
    millis = int.from_bytes(domain_id.bytes[:6], "big")
    assert derive_id(domain_id, operation) >> LOW_BITS == millis


@given(uuid7s(), operations, operations)
def test_different_operations_get_different_ids(domain_id: UUID, first: str, second: str) -> None:
    if first != second:
        assert derive_id(domain_id, first) != derive_id(domain_id, second)


@given(uuid7s(), uuid7s(), operations)
def test_different_domain_objects_get_different_ids(first: UUID, second: UUID, op: str) -> None:
    if first != second:
        assert derive_id(first, op) != derive_id(second, op)


@given(uuid7s(), operations)
def test_derived_ids_are_never_reserved(domain_id: UUID, operation: str) -> None:
    assert 0 < derive_id(domain_id, operation) < U128_MAX


@given(uuid7s(), uuid7s(), operations)
def test_ids_sort_by_domain_timestamp(first: UUID, second: UUID, operation: str) -> None:
    if first.bytes[:6] < second.bytes[:6]:
        assert derive_id(first, operation) < derive_id(second, operation)


def test_the_derivation_matches_adr_0009() -> None:
    domain_id = UUID("018f2f3e-5a00-7000-8000-000000000001")
    digest = hashlib.sha256(domain_id.bytes + b"inventory.hold").digest()
    expected = (0x018F2F3E5A00 << 80) | int.from_bytes(digest[:10], "big")
    assert derive_id(domain_id, "inventory.hold") == expected


def test_only_uuid7_domain_ids_are_accepted() -> None:
    with pytest.raises(NotUuid7Error):
        derive_id(UUID("6ba7b810-9dad-41d1-80b4-00c04fd430c8"), "inventory.hold")


def test_operation_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="operation"):
        derive_id(UUID("018f2f3e-5a00-7000-8000-000000000001"), "")


@pytest.mark.parametrize(("millis", "low"), [(0, 0), (MAX_MILLIS, (1 << LOW_BITS) - 1)])
def test_reserved_ids_are_refused(millis: int, low: int) -> None:
    with pytest.raises(ReservedIdError):
        compose_id(millis, low)


@given(st.integers(min_value=1, max_value=U128_MAX - 1))
def test_ids_round_trip_through_uuid_storage(value: int) -> None:
    assert id_from_uuid(uuid_from_id(value)) == value
