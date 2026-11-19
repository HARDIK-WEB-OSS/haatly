from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Table,
    Text,
    UniqueConstraint,
    column,
    table,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID

from haatly.platform.public import module_metadata
from haatly.stores.public import stores_metadata

SCHEMA = "catalog"
metadata = module_metadata(SCHEMA)
stores = stores_metadata.tables["stores.stores"]
SEARCH_DOCUMENT = (
    "setweight(to_tsvector('simple'::regconfig, title), 'A') || "
    "setweight(to_tsvector('simple'::regconfig, description), 'B')"
)


def timestamp(name: str) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=False)


products = Table(
    "products",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("store_id", UUID(as_uuid=True), ForeignKey(stores.c.id), nullable=False),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("visibility", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("search", TSVECTOR, Computed(SEARCH_DOCUMENT, persisted=True), nullable=True),
    timestamp("created_at"),
    timestamp("updated_at"),
    UniqueConstraint("store_id", "id"),
    CheckConstraint("visibility IN ('public', 'members')", name="visibility_known"),
    CheckConstraint("status IN ('draft', 'active', 'archived')", name="status_known"),
    Index("ix_products_store_id_status", "store_id", "status"),
    Index("ix_products_search", "search", postgresql_using="gin"),
    Index(
        "ix_products_title_trgm",
        "title",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    ),
)

product_search = Table(
    "product_search",
    metadata,
    Column("product_id", UUID(as_uuid=True), primary_key=True),
    Column("store_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("visibility", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("search", TSVECTOR, nullable=False),
    ForeignKeyConstraint(
        ["store_id", "product_id"], [products.c.store_id, products.c.id], ondelete="CASCADE"
    ),
    Index(
        "ix_product_search_search",
        "search",
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    ),
    Index(
        "ix_product_search_title_trgm",
        "title",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
        postgresql_with={"fastupdate": "off"},
    ),
)

variants = Table(
    "variants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("store_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("label", Text, nullable=False),
    Column("status", Text, nullable=False),
    timestamp("created_at"),
    timestamp("updated_at"),
    UniqueConstraint("store_id", "id"),
    ForeignKeyConstraint(["store_id", "product_id"], [products.c.store_id, products.c.id]),
    CheckConstraint("status IN ('active', 'archived')", name="status_known"),
    Index("ix_variants_product_id", "product_id"),
)

variant_prices = Table(
    "variant_prices",
    metadata,
    Column("variant_id", UUID(as_uuid=True), primary_key=True),
    Column("store_id", UUID(as_uuid=True), nullable=False),
    Column("price_paise", BigInteger, nullable=False),
    timestamp("updated_at"),
    ForeignKeyConstraint(["store_id", "variant_id"], [variants.c.store_id, variants.c.id]),
    CheckConstraint("price_paise > 0 AND price_paise <= 100000000000", name="price_in_range"),
)

storefront_products = table(
    "storefront_products",
    column("product_id"),
    column("store_id"),
    column("title"),
    column("description"),
    column("visibility"),
    column("search"),
    column("created_at"),
    schema=SCHEMA,
)

storefront_variants = table(
    "storefront_variants",
    column("variant_id"),
    column("product_id"),
    column("store_id"),
    column("label"),
    column("price_paise"),
    column("created_at"),
    schema=SCHEMA,
)
