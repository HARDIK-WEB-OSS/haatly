from datetime import UTC, datetime, timedelta, timezone

import pytest

from haatly.platform.domain.clock import FrozenClock, NaiveDatetimeError, require_aware


def test_frozen_clock_moves_only_when_advanced() -> None:
    clock = FrozenClock(datetime(2026, 5, 1, tzinfo=UTC))
    assert clock.now() == clock.now()
    clock.advance(timedelta(seconds=90))
    assert clock.now() == datetime(2026, 5, 1, 0, 1, 30, tzinfo=UTC)


def test_default_frozen_clock_is_aware() -> None:
    assert FrozenClock().now().tzinfo is UTC


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(NaiveDatetimeError):
        FrozenClock(datetime(2026, 5, 1))  # noqa: DTZ001


def test_aware_datetimes_are_normalised_to_utc() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    normalised = require_aware(datetime(2026, 5, 1, 5, 30, tzinfo=india))
    assert normalised == datetime(2026, 5, 1, tzinfo=UTC)
    assert normalised.tzinfo is UTC
