from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.ledger.adapters.sql_intents import SqlIntentRepository
from haatly.platform.public import SqlUnitOfWork, TenantContext


class SqlLedgerUnitOfWork(SqlUnitOfWork):
    @property
    def intents(self) -> SqlIntentRepository:
        return SqlIntentRepository(self.connection)


class SqlLedgerUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self, context: TenantContext) -> SqlLedgerUnitOfWork:
        return SqlLedgerUnitOfWork(self.engine, context)
