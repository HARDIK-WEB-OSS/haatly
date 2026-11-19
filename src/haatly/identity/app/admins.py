from dataclasses import dataclass
from enum import StrEnum

from haatly.identity.app.ports import IdentityUnitOfWorkFactory
from haatly.identity.domain.model import normalize_email
from haatly.platform.kernel import SYSTEM, AuditAction, Clock, IdSource, record_audit

ADMIN_GRANTED = "identity.admin_granted"


class UnknownUserError(Exception):
    pass


class AdminGrant(StrEnum):
    GRANTED = "granted"
    ALREADY_ADMIN = "already_admin"


@dataclass
class AdminGrants:
    uow_factory: IdentityUnitOfWorkFactory
    ids: IdSource
    clock: Clock

    async def grant(self, raw_email: str, database_role: str) -> AdminGrant:
        email = normalize_email(raw_email)
        async with self.uow_factory(SYSTEM) as uow:
            user = await uow.users.by_email(email)
            if user is None:
                raise UnknownUserError
            if user.is_admin:
                return AdminGrant.ALREADY_ADMIN
            await uow.users.set_admin(user.id, self.clock.now())
            details = {"via": "make-admin", "database_role": database_role}
            what = AuditAction(ADMIN_GRANTED, "user", user.id, details)
            await record_audit(uow, self.ids, self.clock, what)
        return AdminGrant.GRANTED
