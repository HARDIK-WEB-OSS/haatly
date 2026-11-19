from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from haatly.discovery.domain.model import (
    NearbyProduct,
    NearbyStore,
    PinCode,
    Point,
    StoreLocation,
)
from haatly.platform.kernel import TenantContext, UnitOfWork


class PinDirectory(Protocol):
    async def get(self, pin_code: str) -> PinCode | None: ...

    async def upsert(self, pins: Sequence[PinCode], version: str) -> None: ...

    async def record_import(self, version: str, rows: int, pins: int, now: datetime) -> bool: ...


class LocationRepository(Protocol):
    async def put(self, location: StoreLocation, now: datetime) -> None: ...

    async def get(self, store_id: UUID) -> StoreLocation | None: ...


class NearbySearch(Protocol):
    async def stores(self, origin: Point, radius_m: float, limit: int) -> Sequence[NearbyStore]: ...

    async def products(
        self, text: str, origin: Point, radius_m: float, limit: int
    ) -> Sequence[NearbyProduct]: ...


class DiscoveryUnitOfWork(UnitOfWork, Protocol):
    @property
    def pins(self) -> PinDirectory: ...

    @property
    def locations(self) -> LocationRepository: ...

    @property
    def nearby(self) -> NearbySearch: ...

    async def __aenter__(self) -> Self: ...


class DiscoveryUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> DiscoveryUnitOfWork: ...
