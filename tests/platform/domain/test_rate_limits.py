from datetime import UTC, datetime, timedelta

from haatly.platform.domain.rate_limits import RateDecision, RateLimitRule, decide

RULE = RateLimitRule("login.ip", 3, timedelta(minutes=15))
NOW = datetime(2026, 11, 18, 10, 7, 30, tzinfo=UTC)


def test_windows_are_aligned_to_their_length() -> None:
    assert RULE.window_start(NOW) == datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    assert RULE.key("10.0.0.1") == "login.ip:10.0.0.1"


def test_counts_up_to_the_limit_are_allowed_and_retry_after_is_the_rest_of_the_window() -> None:
    assert decide(RULE, 3, NOW) == RateDecision(True, 450)
    assert decide(RULE, 4, NOW) == RateDecision(False, 450)


def test_retry_after_is_never_zero() -> None:
    end = datetime(2026, 11, 18, 10, 14, 59, 999_000, tzinfo=UTC)
    assert decide(RULE, 9, end).retry_after_seconds == 1
