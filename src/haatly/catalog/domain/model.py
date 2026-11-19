from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

MAX_PRICE_PAISE = 100_000_000_000


class InvalidPriceError(ValueError):
    pass


def check_price(paise: int) -> int:
    if isinstance(paise, bool) or not 0 < paise <= MAX_PRICE_PAISE:
        raise InvalidPriceError
    return paise


class ProductVisibility(StrEnum):
    PUBLIC = "public"
    MEMBERS = "members"


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class VariantStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Product:
    id: UUID
    store_id: UUID
    title: str
    description: str
    visibility: ProductVisibility
    status: ProductStatus
    created_at: datetime


@dataclass(frozen=True)
class Variant:
    id: UUID
    store_id: UUID
    product_id: UUID
    label: str
    status: VariantStatus
    price_paise: int
    created_at: datetime


@dataclass(frozen=True)
class ProductChanges:
    title: str | None = None
    description: str | None = None
    visibility: ProductVisibility | None = None
    status: ProductStatus | None = None

    def values(self) -> dict[str, object]:
        return {key: value for key, value in vars(self).items() if value is not None}


@dataclass(frozen=True)
class VariantChanges:
    label: str | None = None
    status: VariantStatus | None = None
    price_paise: int | None = None

    def variant_values(self) -> dict[str, object]:
        return {
            key: value for key in ("label", "status") if (value := getattr(self, key)) is not None
        }


@dataclass(frozen=True)
class SellerProduct:
    product: Product
    variants: tuple[Variant, ...]


@dataclass(frozen=True)
class ShopVariant:
    id: UUID
    label: str
    price_paise: int | None


@dataclass(frozen=True)
class ShopProduct:
    id: UUID
    store_id: UUID
    title: str
    description: str
    visibility: ProductVisibility
    price_from_paise: int | None


@dataclass(frozen=True)
class ShopProductDetail:
    product: ShopProduct
    variants: tuple[ShopVariant, ...]
