from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.platform.public import SqlUnitOfWork, TenantContext
from haatly.stores.adapters.sql_stores import SqlStoreRepository


class SqlStoresUnitOfWork(SqlUnitOfWork):
    @property
    def stores(self) -> SqlStoreRepository:
        return SqlStoreRepository(self.connection)


class SqlStoresUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self, context: TenantContext) -> SqlStoresUnitOfWork:
        return SqlStoresUnitOfWork(self.engine, context)
