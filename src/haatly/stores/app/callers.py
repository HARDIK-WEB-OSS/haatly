from dataclasses import dataclass, field

from haatly.identity.kernel import Principal, SessionPolicy, resolve_session
from haatly.platform.kernel import PUBLIC, SYSTEM, ActorRole, Clock, TenantContext
from haatly.stores.app.ports import CallerUnitOfWork, CallerUnitOfWorkFactory


async def context_of(uow: CallerUnitOfWork, principal: Principal) -> TenantContext:
    if principal.is_admin:
        return TenantContext(ActorRole.ADMIN, principal.user_id)
    owned = await uow.stores.owned_by(principal.user_id)
    if owned is None:
        return TenantContext(ActorRole.BUYER, principal.user_id)
    return TenantContext(ActorRole.SELLER, principal.user_id, owned.id)


@dataclass
class Callers:
    uow_factory: CallerUnitOfWorkFactory
    clock: Clock
    policy: SessionPolicy = field(default_factory=SessionPolicy)

    async def context_for(self, presented: str | None) -> TenantContext:
        if presented is None:
            return PUBLIC
        async with self.uow_factory(SYSTEM) as uow:
            principal = await resolve_session(
                uow.sessions, presented, self.clock.now(), self.policy
            )
            if principal is None:
                return PUBLIC
            return await context_of(uow, principal)
