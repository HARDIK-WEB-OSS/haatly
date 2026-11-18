from datetime import timedelta

from hypothesis import given
from hypothesis import strategies as st

from haatly.platform.domain.backoff import BackoffPolicy

POLICY = BackoffPolicy(base=timedelta(seconds=2), cap=timedelta(minutes=30))


def test_ceiling_doubles_until_the_cap() -> None:
    ceilings = [POLICY.ceiling(attempt).total_seconds() for attempt in range(1, 13)]
    assert ceilings == [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 1800, 1800]


def test_attempt_zero_uses_the_base() -> None:
    assert POLICY.ceiling(0) == timedelta(seconds=2)


def test_huge_attempt_numbers_stay_at_the_cap() -> None:
    assert POLICY.ceiling(10_000) == POLICY.cap


@given(st.integers(min_value=0, max_value=500), st.floats(min_value=-5, max_value=5))
def test_delay_is_full_jitter_within_the_ceiling(attempt: int, sample: float) -> None:
    delay = POLICY.delay(attempt, lambda: sample)
    assert timedelta(0) <= delay <= POLICY.ceiling(attempt)


def test_delay_scales_the_ceiling_by_the_random_fraction() -> None:
    assert POLICY.delay(3, lambda: 0.5) == timedelta(seconds=4)
