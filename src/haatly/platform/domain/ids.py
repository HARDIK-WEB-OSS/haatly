from datetime import datetime
from uuid import UUID

from haatly.platform.domain.clock import require_aware

RANDOM_BYTES = 10
MAX_UNIX_MILLIS = (1 << 48) - 1


class InvalidIdInputError(ValueError):
    pass


def unix_millis(moment: datetime) -> int:
    return int(require_aware(moment).timestamp() * 1000)


def uuid7(unix_ms: int, random: bytes) -> UUID:
    if not 0 <= unix_ms <= MAX_UNIX_MILLIS:
        raise InvalidIdInputError("timestamp out of range for UUIDv7")
    if len(random) != RANDOM_BYTES:
        raise InvalidIdInputError("UUIDv7 needs exactly 10 random bytes")
    raw = bytearray(unix_ms.to_bytes(6, "big") + random)
    raw[6] = 0x70 | (raw[6] & 0x0F)
    raw[8] = 0x80 | (raw[8] & 0x3F)
    return UUID(bytes=bytes(raw))


def uuid7_millis(value: UUID) -> int:
    return int.from_bytes(value.bytes[:6], "big")
