from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from haatly.catalog.domain.model import (
    Product,
    ProductChanges,
    ShopProduct,
    ShopVariant,
    Variant,
    VariantChanges,
)
from haatly.platform.kernel import TenantContext, UnitOfWork


class ProductRepository(Protocol):
    async def add(self, product: Product) -> None: ...

    async def get(self, product_id: UUID) -> Product | None: ...

    async def for_store(self, store_id: UUID) -> Sequence[Product]: ...

    async def update(
        self, product_id: UUID, changes: ProductChanges, now: datetime
    ) -> Product | None: ...


class VariantRepository(Protocol):
    async def add(self, variant: Variant) -> None: ...

    async def get(self, variant_id: UUID) -> Variant | None: ...

    async def for_products(self, product_ids: Sequence[UUID]) -> Sequence[Variant]: ...

    async def update(
        self, variant_id: UUID, changes: VariantChanges, now: datetime
    ) -> Variant | None: ...


class StorefrontReader(Protocol):
    async def products(
        self, store_id: UUID, text: str | None, limit: int
    ) -> Sequence[ShopProduct]: ...

    async def product(self, store_id: UUID, product_id: UUID) -> ShopProduct | None: ...

    async def variants(self, product_id: UUID) -> Sequence[ShopVariant]: ...


class CatalogUnitOfWork(UnitOfWork, Protocol):
    @property
    def products(self) -> ProductRepository: ...

    @property
    def variants(self) -> VariantRepository: ...

    @property
    def storefront(self) -> StorefrontReader: ...

    async def __aenter__(self) -> Self: ...


class CatalogUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> CatalogUnitOfWork: ...
