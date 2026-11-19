from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.discovery.adapters.sql_discovery import (
    SqlLocationRepository,
    SqlNearbySearch,
    SqlPinDirectory,
)
from haatly.platform.public import SqlUnitOfWork, TenantContext


class SqlDiscoveryUnitOfWork(SqlUnitOfWork):
    @property
    def pins(self) -> SqlPinDirectory:
        return SqlPinDirectory(self.connection)

    @property
    def locations(self) -> SqlLocationRepository:
        return SqlLocationRepository(self.connection)

    @property
    def nearby(self) -> SqlNearbySearch:
        return SqlNearbySearch(self.connection)


class SqlDiscoveryUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self, context: TenantContext) -> SqlDiscoveryUnitOfWork:
        return SqlDiscoveryUnitOfWork(self.engine, context)
