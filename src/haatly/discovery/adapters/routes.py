from collections.abc import Sequence
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from haatly.discovery.app.discovery import Discovery, LocationNotFoundError, Origin
from haatly.discovery.domain.model import (
    MAX_RADIUS_KM,
    InvalidLocationError,
    NearbyProduct,
    NearbyStore,
    PinCode,
    Point,
    StoreLocation,
)
from haatly.platform.public import ApiError, NonBlank, NulStripped, TenantContext, bounded
from haatly.stores.public import ContextDep, SellerContextDep

PinText = Annotated[str, NulStripped, bounded(6), NonBlank]
SearchText = Annotated[str, NulStripped, bounded(100), NonBlank]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
RadiusKm = Annotated[float, Field(gt=0, le=MAX_RADIUS_KM)]


class LocationIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"pin_code": "{pin_code}"}]}
    )
    pin_code: PinText
    latitude: Latitude | None = None
    longitude: Longitude | None = None


class LocationOut(BaseModel):
    pin_code: str
    latitude: float
    longitude: float

    @classmethod
    def of(cls, location: StoreLocation) -> "LocationOut":
        return cls(
            pin_code=location.pin_code,
            latitude=location.point.latitude,
            longitude=location.point.longitude,
        )


class PinOut(BaseModel):
    pin_code: str
    district: str
    state: str


class NearbyStoreOut(BaseModel):
    id: UUID
    name: str
    slug: str
    distance_km: float

    @classmethod
    def of(cls, store: NearbyStore) -> "NearbyStoreOut":
        return cls(id=store.id, name=store.name, slug=store.slug, distance_km=store.distance_km)


class NearbyProductOut(BaseModel):
    id: UUID
    store_id: UUID
    store_name: str
    title: str
    price_from_paise: int | None
    distance_km: float

    @classmethod
    def of(cls, product: NearbyProduct) -> "NearbyProductOut":
        return cls(
            id=product.id,
            store_id=product.store_id,
            store_name=product.store_name,
            title=product.title,
            price_from_paise=product.price_from_paise,
            distance_km=product.distance_km,
        )


def discovery_of(request: Request) -> Discovery:
    discovery: Discovery = request.app.state.discovery
    return discovery


DiscoveryDep = Annotated[Discovery, Depends(discovery_of)]
NOT_FOUND = ApiError(status.HTTP_404_NOT_FOUND, "not_found", "not found")


def point_of(latitude: float | None, longitude: float | None) -> Point | None:
    if latitude is None and longitude is None:
        return None
    if latitude is None or longitude is None:
        raise InvalidLocationError("give both latitude and longitude")
    return Point(latitude, longitude)


def origin_of(
    pin_code: Annotated[PinText | None, Query()] = None,
    latitude: Annotated[Latitude | None, Query()] = None,
    longitude: Annotated[Longitude | None, Query()] = None,
) -> Origin:
    try:
        return Origin(pin_code, point_of(latitude, longitude))
    except InvalidLocationError as error:
        raise ApiError(422, "validation_error", str(error)) from error


OriginDep = Annotated[Origin, Depends(origin_of)]


async def located[T](work: Any) -> T:
    try:
        result: T = await work
    except InvalidLocationError as error:
        raise ApiError(422, "validation_error", str(error)) from error
    except LocationNotFoundError as error:
        raise NOT_FOUND from error
    return result


router = APIRouter()


@router.get("/pin-codes/{pin_code}")
async def pin_code(pin_code: str, discovery: DiscoveryDep) -> PinOut:
    found: PinCode = await located(discovery.pin(pin_code))
    return PinOut(pin_code=found.pin_code, district=found.district, state=found.state)


@router.put("/seller/store/location")
async def set_location(
    body: LocationIn, context: SellerContextDep, discovery: DiscoveryDep
) -> LocationOut:
    location: StoreLocation = await located(place(discovery, context, body))
    return LocationOut.of(location)


async def place(discovery: Discovery, context: TenantContext, body: LocationIn) -> StoreLocation:
    point = point_of(body.latitude, body.longitude)
    return await discovery.set_location(context, body.pin_code, point)


@router.get("/seller/store/location")
async def store_location(context: SellerContextDep, discovery: DiscoveryDep) -> LocationOut:
    location: StoreLocation = await located(discovery.location(context))
    return LocationOut.of(location)


@router.get("/discover/stores")
async def discover_stores(
    context: ContextDep,
    origin: OriginDep,
    discovery: DiscoveryDep,
    radius_km: Annotated[RadiusKm, Query()] = 10.0,
) -> list[NearbyStoreOut]:
    stores: Sequence[NearbyStore] = await located(discovery.stores_near(context, origin, radius_km))
    return [NearbyStoreOut.of(store) for store in stores]


@router.get("/discover/products")
async def discover_products(
    q: Annotated[SearchText, Query()],
    context: ContextDep,
    origin: OriginDep,
    discovery: DiscoveryDep,
    radius_km: Annotated[RadiusKm, Query()] = 10.0,
) -> list[NearbyProductOut]:
    products: Sequence[NearbyProduct] = await located(
        discovery.products_near(context, q, origin, radius_km)
    )
    return [NearbyProductOut.of(product) for product in products]
