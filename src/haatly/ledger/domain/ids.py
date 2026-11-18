import hashlib
from uuid import UUID

LOW_BITS = 80
LOW_MASK = (1 << LOW_BITS) - 1
U128_MAX = (1 << 128) - 1
TIMESTAMP_BYTES = 6


class NotUuid7Error(ValueError):
    pass


class ReservedIdError(ValueError):
    pass


def compose_id(unix_ms: int, low: int) -> int:
    value = (unix_ms << LOW_BITS) | (low & LOW_MASK)
    if value in (0, U128_MAX):
        raise ReservedIdError(value)
    return value


def derive_id(domain_id: UUID, operation: str) -> int:
    if domain_id.version != 7:
        raise NotUuid7Error(str(domain_id))
    if not operation:
        raise ValueError("operation must not be empty")
    digest = hashlib.sha256(domain_id.bytes + operation.encode()).digest()
    unix_ms = int.from_bytes(domain_id.bytes[:TIMESTAMP_BYTES], "big")
    return compose_id(unix_ms, int.from_bytes(digest[: LOW_BITS // 8], "big"))


def is_valid_id(value: int) -> bool:
    return 0 < value < U128_MAX


def uuid_from_id(value: int) -> UUID:
    return UUID(int=value)


def id_from_uuid(value: UUID) -> int:
    return value.int
