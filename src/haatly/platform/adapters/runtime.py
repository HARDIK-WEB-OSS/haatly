import os
import random
from datetime import UTC, datetime
from uuid import UUID

from haatly.platform.domain.clock import Clock
from haatly.platform.domain.ids import RANDOM_BYTES, unix_millis, uuid7

SECURE_RANDOM = random.SystemRandom()


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Uuid7Source:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def new_id(self) -> UUID:
        return uuid7(unix_millis(self.clock.now()), os.urandom(RANDOM_BYTES))


def unit_random() -> float:
    return SECURE_RANDOM.random()
