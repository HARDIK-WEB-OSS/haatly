from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from haatly.catalog.app.ports import CatalogUnitOfWork, CatalogUnitOfWorkFactory
from haatly.catalog.domain.model import (
    Product,
    ProductChanges,
    ProductStatus,
    ProductVisibility,
    SellerProduct,
    ShopProduct,
    ShopProductDetail,
    Variant,
    VariantChanges,
    VariantStatus,
    check_price,
)
from haatly.platform.kernel import ActorRole, Clock, IdSource, TenantContext

MAX_LISTING = 100


class CatalogNotFoundError(LookupError):
    pass


def require_store(context: TenantContext) -> UUID:
    if context.role is not ActorRole.SELLER or context.store_id is None:
        raise CatalogNotFoundError
    return context.store_id


@dataclass
class Catalog:
    uow_factory: CatalogUnitOfWorkFactory
    ids: IdSource
    clock: Clock

    async def create_product(
        self, context: TenantContext, title: str, description: str, visibility: ProductVisibility
    ) -> Product:
        product = Product(
            self.ids.new_id(),
            require_store(context),
            title,
            description,
            visibility,
            ProductStatus.DRAFT,
            self.clock.now(),
        )
        async with self.uow_factory(context) as uow:
            await uow.products.add(product)
        return product

    async def update_product(
        self, context: TenantContext, product_id: UUID, changes: ProductChanges
    ) -> Product:
        require_store(context)
        async with self.uow_factory(context) as uow:
            updated = await uow.products.update(product_id, changes, self.clock.now())
        if updated is None:
            raise CatalogNotFoundError
        return updated

    async def add_variant(
        self, context: TenantContext, product_id: UUID, label: str, price_paise: int
    ) -> Variant:
        store_id = require_store(context)
        variant = Variant(
            self.ids.new_id(),
            store_id,
            product_id,
            label,
            VariantStatus.ACTIVE,
            check_price(price_paise),
            self.clock.now(),
        )
        async with self.uow_factory(context) as uow:
            if await uow.products.get(product_id) is None:
                raise CatalogNotFoundError
            await uow.variants.add(variant)
        return variant

    async def update_variant(
        self, context: TenantContext, variant_id: UUID, changes: VariantChanges
    ) -> Variant:
        require_store(context)
        if changes.price_paise is not None:
            check_price(changes.price_paise)
        async with self.uow_factory(context) as uow:
            updated = await uow.variants.update(variant_id, changes, self.clock.now())
        if updated is None:
            raise CatalogNotFoundError
        return updated

    async def with_variants(
        self, uow: CatalogUnitOfWork, products: Sequence[Product]
    ) -> list[SellerProduct]:
        variants = await uow.variants.for_products([product.id for product in products])
        return [
            SellerProduct(product, tuple(v for v in variants if v.product_id == product.id))
            for product in products
        ]

    async def seller_products(self, context: TenantContext) -> list[SellerProduct]:
        store_id = require_store(context)
        async with self.uow_factory(context) as uow:
            return await self.with_variants(uow, await uow.products.for_store(store_id))

    async def seller_product(self, context: TenantContext, product_id: UUID) -> SellerProduct:
        require_store(context)
        async with self.uow_factory(context) as uow:
            product = await uow.products.get(product_id)
            if product is None:
                raise CatalogNotFoundError
            return (await self.with_variants(uow, [product]))[0]

    async def storefront(
        self, context: TenantContext, store_id: UUID, text: str | None
    ) -> Sequence[ShopProduct]:
        async with self.uow_factory(context) as uow:
            return await uow.storefront.products(store_id, text, MAX_LISTING)

    async def storefront_product(
        self, context: TenantContext, store_id: UUID, product_id: UUID
    ) -> ShopProductDetail:
        async with self.uow_factory(context) as uow:
            product = await uow.storefront.product(store_id, product_id)
            if product is None:
                raise CatalogNotFoundError
            variants = await uow.storefront.variants(product_id)
        return ShopProductDetail(product, tuple(variants))
