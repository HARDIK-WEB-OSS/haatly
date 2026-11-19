import re
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

PIN_SHAPE = re.compile(r"^[1-9][0-9]{5}$")
INDIA_LATITUDE = (6.0, 38.0)
INDIA_LONGITUDE = (68.0, 98.0)
MAX_RADIUS_KM = 50.0


class InvalidLocationError(ValueError):
    pass


def check_pin(raw: str) -> str:
    pin = raw.strip()
    if not PIN_SHAPE.fullmatch(pin):
        raise InvalidLocationError("PIN codes are six digits")
    return pin


@dataclass(frozen=True)
class Point:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not in_india(self.latitude, self.longitude):
            raise InvalidLocationError("point is outside India")


def in_india(latitude: float, longitude: float) -> bool:
    return (
        INDIA_LATITUDE[0] <= latitude <= INDIA_LATITUDE[1]
        and INDIA_LONGITUDE[0] <= longitude <= INDIA_LONGITUDE[1]
    )


@dataclass(frozen=True)
class PinCode:
    pin_code: str
    district: str
    state: str
    centroid: Point


@dataclass(frozen=True)
class StoreLocation:
    store_id: UUID
    pin_code: str
    point: Point


@dataclass(frozen=True)
class NearbyStore:
    id: UUID
    name: str
    slug: str
    distance_km: float


@dataclass(frozen=True)
class NearbyProduct:
    id: UUID
    store_id: UUID
    store_name: str
    title: str
    price_from_paise: int | None
    distance_km: float


@dataclass(frozen=True)
class PostOffice:
    pin_code: str
    district: str
    state: str
    latitude: float | None
    longitude: float | None


def centroids(offices: Iterable[PostOffice]) -> list[PinCode]:
    grouped: dict[str, list[PostOffice]] = {}
    for office in offices:
        grouped.setdefault(office.pin_code, []).append(office)
    found = []
    for pin_code, members in sorted(grouped.items()):
        located = [
            (o.latitude, o.longitude)
            for o in members
            if o.latitude is not None
            and o.longitude is not None
            and in_india(o.latitude, o.longitude)
        ]
        if located:
            latitude = sum(point[0] for point in located) / len(located)
            longitude = sum(point[1] for point in located) / len(located)
            first = members[0]
            found.append(PinCode(pin_code, first.district, first.state, Point(latitude, longitude)))
    return found


def rounded_km(meters: float) -> float:
    return round(meters / 1000, 1)
