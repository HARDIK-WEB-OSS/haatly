from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.membership.adapters.sql_membership import (
    SqlInvitationRepository,
    SqlMembershipRepository,
    SqlStoreGate,
)
from haatly.platform.public import SqlUnitOfWork, TenantContext


class SqlMembershipUnitOfWork(SqlUnitOfWork):
    @property
    def memberships(self) -> SqlMembershipRepository:
        return SqlMembershipRepository(self.connection)

    @property
    def invitations(self) -> SqlInvitationRepository:
        return SqlInvitationRepository(self.connection)

    @property
    def store_gate(self) -> SqlStoreGate:
        return SqlStoreGate(self.connection)


class SqlMembershipUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self, context: TenantContext) -> SqlMembershipUnitOfWork:
        return SqlMembershipUnitOfWork(self.engine, context)
