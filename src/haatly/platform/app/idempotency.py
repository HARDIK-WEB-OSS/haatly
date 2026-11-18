from dataclasses import dataclass
from datetime import timedelta

from haatly.platform.app.ports import UnitOfWorkFactory
from haatly.platform.domain.clock import Clock
from haatly.platform.domain.idempotency import (
    Decision,
    Proceed,
    StoredResponse,
    decide,
    request_hash,
)
from haatly.platform.domain.tenancy import TenantContext

MAX_KEY_LENGTH = 255


class InvalidIdempotencyKeyError(ValueError):
    pass


def validate_key(key: str) -> str:
    if not key or len(key) > MAX_KEY_LENGTH or not key.isprintable():
        raise InvalidIdempotencyKeyError(key[:MAX_KEY_LENGTH])
    return key


@dataclass
class IdempotencyGate:
    uow_factory: UnitOfWorkFactory
    clock: Clock
    lease: timedelta = timedelta(minutes=2)

    async def begin(self, context: TenantContext, scope: str, key: str, body: bytes) -> Decision:
        validate_key(key)
        incoming = request_hash(body)
        async with self.uow_factory(context) as uow:
            await uow.idempotency.lock(scope, key)
            now = self.clock.now()
            decision = decide(await uow.idempotency.find(scope, key), incoming, now)
            if isinstance(decision, Proceed):
                await uow.idempotency.start(scope, key, incoming, now + self.lease, now)
        return decision

    async def complete(
        self, context: TenantContext, scope: str, key: str, response: StoredResponse
    ) -> None:
        async with self.uow_factory(context) as uow:
            await uow.idempotency.complete(scope, key, response, self.clock.now())

    async def abandon(self, context: TenantContext, scope: str, key: str) -> None:
        async with self.uow_factory(context) as uow:
            await uow.idempotency.abandon(scope, key)
