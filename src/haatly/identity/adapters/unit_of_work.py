from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.identity.adapters.sql_identity import SqlSessionRepository, SqlUserRepository
from haatly.platform.public import SqlUnitOfWork, TenantContext


class SqlIdentityUnitOfWork(SqlUnitOfWork):
    @property
    def users(self) -> SqlUserRepository:
        return SqlUserRepository(self.connection)

    @property
    def sessions(self) -> SqlSessionRepository:
        return SqlSessionRepository(self.connection)


class SqlIdentityUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self, context: TenantContext) -> SqlIdentityUnitOfWork:
        return SqlIdentityUnitOfWork(self.engine, context)
