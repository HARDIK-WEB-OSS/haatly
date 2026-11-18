from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol


class NaiveDatetimeError(ValueError):
    def __init__(self) -> None:
        super().__init__("datetimes must be timezone-aware")


class Clock(Protocol):
    def now(self) -> datetime: ...


def require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise NaiveDatetimeError
    return moment.astimezone(UTC)


@dataclass
class FrozenClock:
    moment: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))

    def __post_init__(self) -> None:
        self.moment = require_aware(self.moment)

    def now(self) -> datetime:
        return self.moment

    def advance(self, delta: timedelta) -> None:
        self.moment = self.moment + delta
