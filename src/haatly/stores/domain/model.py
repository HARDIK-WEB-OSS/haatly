import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

SLUG_SHAPE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])$")


class InvalidSlugError(ValueError):
    pass


def check_slug(raw: str) -> str:
    slug = raw.strip().lower()
    if not SLUG_SHAPE.fullmatch(slug):
        raise InvalidSlugError
    return slug


class StoreStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class PriceVisibility(StrEnum):
    PUBLIC = "public"
    MEMBERS = "members"


@dataclass(frozen=True)
class Store:
    id: UUID
    owner_user_id: UUID
    name: str
    slug: str
    description: str
    status: StoreStatus
    is_published: bool
    price_visibility: PriceVisibility
    created_at: datetime

    @property
    def is_live(self) -> bool:
        return self.status is StoreStatus.ACTIVE and self.is_published


@dataclass(frozen=True)
class StoreChanges:
    name: str | None = None
    description: str | None = None
    is_published: bool | None = None
    price_visibility: PriceVisibility | None = None

    def values(self) -> dict[str, object]:
        return {key: value for key, value in vars(self).items() if value is not None}
