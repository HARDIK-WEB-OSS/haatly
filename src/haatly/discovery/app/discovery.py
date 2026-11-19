from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from haatly.discovery.app.ports import DiscoveryUnitOfWork, DiscoveryUnitOfWorkFactory
from haatly.discovery.domain.model import (
    MAX_RADIUS_KM,
    InvalidLocationError,
    NearbyProduct,
    NearbyStore,
    PinCode,
    Point,
    PostOffice,
    StoreLocation,
    centroids,
    check_pin,
)
from haatly.platform.kernel import SYSTEM, ActorRole, Clock, TenantContext

MAX_RESULTS = 50


class LocationNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class Origin:
    pin_code: str | None = None
    point: Point | None = None


@dataclass(frozen=True)
class ImportSummary:
    version: str
    rows: int
    pins: int
    imported: bool


def check_radius(radius_km: float) -> float:
    if not 0 < radius_km <= MAX_RADIUS_KM:
        raise InvalidLocationError("radius is out of range")
    return radius_km * 1000


@dataclass
class Discovery:
    uow_factory: DiscoveryUnitOfWorkFactory
    clock: Clock

    async def pin(self, raw: str) -> PinCode:
        async with self.uow_factory(SYSTEM) as uow:
            found = await uow.pins.get(check_pin(raw))
        if found is None:
            raise LocationNotFoundError
        return found

    async def known_pin(self, uow: DiscoveryUnitOfWork, raw: str) -> PinCode:
        found = await uow.pins.get(check_pin(raw))
        if found is None:
            raise InvalidLocationError("unknown PIN code")
        return found

    async def set_location(
        self, context: TenantContext, raw_pin: str, point: Point | None
    ) -> StoreLocation:
        if context.role is not ActorRole.SELLER or context.store_id is None:
            raise LocationNotFoundError
        async with self.uow_factory(context) as uow:
            pin = await self.known_pin(uow, raw_pin)
            location = StoreLocation(context.store_id, pin.pin_code, point or pin.centroid)
            await uow.locations.put(location, self.clock.now())
        return location

    async def location(self, context: TenantContext) -> StoreLocation:
        if context.store_id is None:
            raise LocationNotFoundError
        async with self.uow_factory(context) as uow:
            found = await uow.locations.get(context.store_id)
        if found is None:
            raise LocationNotFoundError
        return found

    async def resolve(self, uow: DiscoveryUnitOfWork, origin: Origin) -> Point:
        if origin.pin_code is not None:
            return (await self.known_pin(uow, origin.pin_code)).centroid
        if origin.point is not None:
            return origin.point
        raise InvalidLocationError("give a PIN code or a point")

    async def stores_near(
        self, context: TenantContext, origin: Origin, radius_km: float
    ) -> Sequence[NearbyStore]:
        radius_m = check_radius(radius_km)
        async with self.uow_factory(context) as uow:
            point = await self.resolve(uow, origin)
            return await uow.nearby.stores(point, radius_m, MAX_RESULTS)

    async def products_near(
        self, context: TenantContext, text: str, origin: Origin, radius_km: float
    ) -> Sequence[NearbyProduct]:
        radius_m = check_radius(radius_km)
        async with self.uow_factory(context) as uow:
            point = await self.resolve(uow, origin)
            return await uow.nearby.products(text, point, radius_m, MAX_RESULTS)

    async def import_pins(
        self, offices: Iterable[PostOffice], version: str, rows: int
    ) -> ImportSummary:
        pins = centroids(offices)
        async with self.uow_factory(SYSTEM) as uow:
            imported = await uow.pins.record_import(version, rows, len(pins), self.clock.now())
            if imported:
                await uow.pins.upsert(pins, version)
        return ImportSummary(version, rows, len(pins), imported)
