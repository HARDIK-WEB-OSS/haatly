from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True)
class BackoffPolicy:
    base: timedelta = timedelta(seconds=2)
    cap: timedelta = timedelta(minutes=30)

    def ceiling(self, attempt: int) -> timedelta:
        exponent = min(max(attempt - 1, 0), 64)
        grown = (self.base // MICROSECOND) << exponent
        return min(timedelta(microseconds=grown), self.cap) if grown < 1 << 64 else self.cap

    def delay(self, attempt: int, unit_random: Callable[[], float]) -> timedelta:
        fraction = min(max(unit_random(), 0.0), 1.0)
        return self.ceiling(attempt) * fraction
