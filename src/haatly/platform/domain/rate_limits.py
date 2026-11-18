import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    limit: int
    window: timedelta

    def window_start(self, now: datetime) -> datetime:
        seconds = int(self.window.total_seconds())
        start = int(now.timestamp()) // seconds * seconds
        return datetime.fromtimestamp(start, UTC)

    def key(self, subject: str) -> str:
        return f"{self.name}:{subject}"


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_seconds: int


def decide(rule: RateLimitRule, count: int, now: datetime) -> RateDecision:
    remaining = rule.window_start(now) + rule.window - now
    return RateDecision(count <= rule.limit, max(1, math.ceil(remaining.total_seconds())))
