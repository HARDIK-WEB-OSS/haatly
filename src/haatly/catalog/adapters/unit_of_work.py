from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.catalog.adapters.sql_catalog import (
    SqlProductRepository,
    SqlStorefrontReader,
    SqlVariantRepository,
)
from haatly.platform.public import SqlUnitOfWork, TenantContext


class SqlCatalogUnitOfWork(SqlUnitOfWork):
    @property
    def products(self) -> SqlProductRepository:
        return SqlProductRepository(self.connection)

    @property
    def variants(self) -> SqlVariantRepository:
        return SqlVariantRepository(self.connection)

    @property
    def storefront(self) -> SqlStorefrontReader:
        return SqlStorefrontReader(self.connection)


class SqlCatalogUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self, context: TenantContext) -> SqlCatalogUnitOfWork:
        return SqlCatalogUnitOfWork(self.engine, context)
