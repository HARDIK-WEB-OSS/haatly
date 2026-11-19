from dataclasses import dataclass

from haatly.platform.app.ports import IdSource, UnitOfWork, UnitOfWorkFactory
from haatly.platform.domain.audit import AuditAction, AuditEntry
from haatly.platform.domain.clock import Clock
from haatly.platform.domain.rate_limits import (
    RateDecision,
    RateLimitedError,
    RateLimitRule,
    decide,
)
from haatly.platform.domain.tenancy import SYSTEM


async def record_audit(uow: UnitOfWork, ids: IdSource, clock: Clock, what: AuditAction) -> None:
    await uow.audit.append(AuditEntry(ids.new_id(), clock.now(), uow.context, what))


@dataclass
class RateLimiter:
    uow_factory: UnitOfWorkFactory
    clock: Clock

    async def hit(self, rule: RateLimitRule, subject: str) -> RateDecision:
        now = self.clock.now()
        async with self.uow_factory(SYSTEM) as uow:
            count = await uow.rate_limits.hit(rule.key(subject), rule.window_start(now))
        return decide(rule, count, now)

    async def enforce(self, rule: RateLimitRule, subject: str) -> None:
        decision = await self.hit(rule, subject)
        if not decision.allowed:
            raise RateLimitedError(decision.retry_after_seconds)
