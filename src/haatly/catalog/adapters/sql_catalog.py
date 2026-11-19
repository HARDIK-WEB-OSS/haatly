from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Row, and_, cast, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import REGCONFIG, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.catalog.adapters.tables import (
    products,
    storefront_products,
    storefront_variants,
    variant_prices,
    variants,
)
from haatly.catalog.domain.model import (
    Product,
    ProductChanges,
    ProductStatus,
    ProductVisibility,
    ShopProduct,
    ShopVariant,
    Variant,
    VariantChanges,
    VariantStatus,
)

type AnyRow = Row[tuple[Any, ...]]

PRODUCT_COLUMNS = (
    products.c.id,
    products.c.store_id,
    products.c.title,
    products.c.description,
    products.c.visibility,
    products.c.status,
    products.c.created_at,
)
VARIANT_COLUMNS = (
    variants.c.id,
    variants.c.store_id,
    variants.c.product_id,
    variants.c.label,
    variants.c.status,
    variant_prices.c.price_paise,
    variants.c.created_at,
)


def product_from_row(row: AnyRow) -> Product:
    return Product(
        row.id,
        row.store_id,
        row.title,
        row.description,
        ProductVisibility(row.visibility),
        ProductStatus(row.status),
        row.created_at,
    )


def variant_from_row(row: AnyRow) -> Variant:
    return Variant(
        row.id,
        row.store_id,
        row.product_id,
        row.label,
        VariantStatus(row.status),
        row.price_paise,
        row.created_at,
    )


class SqlProductRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def add(self, product: Product) -> None:
        await self.connection.execute(
            insert(products).values(
                id=product.id,
                store_id=product.store_id,
                title=product.title,
                description=product.description,
                visibility=product.visibility.value,
                status=product.status.value,
                created_at=product.created_at,
                updated_at=product.created_at,
            )
        )

    async def get(self, product_id: UUID) -> Product | None:
        statement = select(*PRODUCT_COLUMNS).where(products.c.id == product_id)
        row = (await self.connection.execute(statement)).first()
        return None if row is None else product_from_row(row)

    async def for_store(self, store_id: UUID) -> Sequence[Product]:
        statement = (
            select(*PRODUCT_COLUMNS)
            .where(products.c.store_id == store_id)
            .order_by(products.c.created_at, products.c.id)
        )
        return [product_from_row(row) for row in await self.connection.execute(statement)]

    async def update(
        self, product_id: UUID, changes: ProductChanges, now: datetime
    ) -> Product | None:
        statement = (
            update(products)
            .where(products.c.id == product_id)
            .values(updated_at=now, **changes.values())
            .returning(*PRODUCT_COLUMNS)
        )
        row = (await self.connection.execute(statement)).first()
        return None if row is None else product_from_row(row)


class SqlVariantRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    def priced(self, *conditions: ColumnElement[bool]) -> Any:
        joined = variants.join(
            variant_prices,
            and_(
                variant_prices.c.variant_id == variants.c.id,
                variant_prices.c.store_id == variants.c.store_id,
            ),
        )
        return select(*VARIANT_COLUMNS).select_from(joined).where(*conditions)

    async def add(self, variant: Variant) -> None:
        await self.connection.execute(
            insert(variants).values(
                id=variant.id,
                store_id=variant.store_id,
                product_id=variant.product_id,
                label=variant.label,
                status=variant.status.value,
                created_at=variant.created_at,
                updated_at=variant.created_at,
            )
        )
        await self.connection.execute(
            insert(variant_prices).values(
                variant_id=variant.id,
                store_id=variant.store_id,
                price_paise=variant.price_paise,
                updated_at=variant.created_at,
            )
        )

    async def get(self, variant_id: UUID) -> Variant | None:
        row = (await self.connection.execute(self.priced(variants.c.id == variant_id))).first()
        return None if row is None else variant_from_row(row)

    async def for_products(self, product_ids: Sequence[UUID]) -> Sequence[Variant]:
        statement = self.priced(variants.c.product_id.in_(product_ids)).order_by(
            variants.c.created_at, variants.c.id
        )
        return [variant_from_row(row) for row in await self.connection.execute(statement)]

    async def update(
        self, variant_id: UUID, changes: VariantChanges, now: datetime
    ) -> Variant | None:
        touched = await self.connection.execute(
            update(variants)
            .where(variants.c.id == variant_id)
            .values(updated_at=now, **changes.variant_values())
            .returning(variants.c.id)
        )
        if touched.first() is None:
            return None
        if changes.price_paise is not None:
            await self.connection.execute(
                update(variant_prices)
                .where(variant_prices.c.variant_id == variant_id)
                .values(price_paise=changes.price_paise, updated_at=now)
            )
        return await self.get(variant_id)


def price_from() -> Any:
    return (
        select(func.min(storefront_variants.c.price_paise))
        .where(storefront_variants.c.product_id == storefront_products.c.product_id)
        .scalar_subquery()
    )


def shop_product_from_row(row: AnyRow) -> ShopProduct:
    return ShopProduct(
        row.product_id,
        row.store_id,
        row.title,
        row.description,
        ProductVisibility(row.visibility),
        row.price_from,
    )


SHOP_COLUMNS = (
    storefront_products.c.product_id,
    storefront_products.c.store_id,
    storefront_products.c.title,
    storefront_products.c.description,
    storefront_products.c.visibility,
)


WORD_SIMILARITY_THRESHOLD = "0.5"


async def allow_misspellings(connection: AsyncConnection) -> None:
    setting = func.set_config("pg_trgm.word_similarity_threshold", WORD_SIMILARITY_THRESHOLD, True)
    await connection.execute(select(setting))


def matches(text: str) -> tuple[ColumnElement[bool], Any]:
    query = func.websearch_to_tsquery(cast(literal("simple"), REGCONFIG), text)
    condition = or_(
        storefront_products.c.search.op("@@")(query),
        literal(text).op("<%")(storefront_products.c.title),
    )
    score = func.ts_rank(storefront_products.c.search, query) + func.word_similarity(
        text, storefront_products.c.title
    )
    return condition, score


class SqlStorefrontReader:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def products(self, store_id: UUID, text: str | None, limit: int) -> Sequence[ShopProduct]:
        statement = select(*SHOP_COLUMNS, price_from().label("price_from")).where(
            storefront_products.c.store_id == store_id
        )
        if text:
            await allow_misspellings(self.connection)
            condition, score = matches(text)
            statement = statement.where(condition).order_by(score.desc())
        statement = statement.order_by(
            storefront_products.c.created_at.desc(), storefront_products.c.product_id
        ).limit(limit)
        return [shop_product_from_row(row) for row in await self.connection.execute(statement)]

    async def product(self, store_id: UUID, product_id: UUID) -> ShopProduct | None:
        statement = select(*SHOP_COLUMNS, price_from().label("price_from")).where(
            storefront_products.c.store_id == store_id,
            storefront_products.c.product_id == product_id,
        )
        row = (await self.connection.execute(statement)).first()
        return None if row is None else shop_product_from_row(row)

    async def variants(self, product_id: UUID) -> Sequence[ShopVariant]:
        statement = (
            select(
                storefront_variants.c.variant_id,
                storefront_variants.c.label,
                storefront_variants.c.price_paise,
            )
            .where(storefront_variants.c.product_id == product_id)
            .order_by(storefront_variants.c.created_at, storefront_variants.c.variant_id)
        )
        return [ShopVariant(*row) for row in await self.connection.execute(statement)]
