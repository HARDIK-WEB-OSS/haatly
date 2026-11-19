from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"
RUNTIME_ROLES = "haatly_app, haatly_ops"
SEARCH_DOCUMENT = (
    "setweight(to_tsvector('simple'::regconfig, title), 'A') || "
    "setweight(to_tsvector('simple'::regconfig, description), 'B')"
)
SYSTEM = "platform.actor_role() = 'system'"
SELLER = "store_id = platform.actor_store_id() AND platform.actor_role() = 'seller'"

PRODUCT_READABLE = """
products.status = 'active'
AND EXISTS (SELECT 1 FROM stores.directory AS live WHERE live.id = products.store_id)
AND (
    products.visibility = 'public'
    OR EXISTS (
        SELECT 1 FROM membership.my_active_memberships AS mine
        WHERE mine.store_id = products.store_id
    )
)
"""

VARIANT_READABLE = """
variants.status = 'active'
AND EXISTS (
    SELECT 1 FROM catalog.products AS shown
    WHERE shown.store_id = variants.store_id AND shown.id = variants.product_id
)
"""

PRICE_READABLE = """
EXISTS (
    SELECT 1 FROM catalog.variants AS shown
    WHERE shown.store_id = variant_prices.store_id AND shown.id = variant_prices.variant_id
)
AND (
    EXISTS (
        SELECT 1 FROM stores.directory AS open_prices
        WHERE open_prices.id = variant_prices.store_id AND open_prices.price_visibility = 'public'
    )
    OR EXISTS (
        SELECT 1 FROM membership.my_active_memberships AS mine
        WHERE mine.store_id = variant_prices.store_id
    )
)
"""

UPDATABLE_COLUMNS = {
    "products": "title, description, visibility, status, updated_at",
    "variants": "label, status, updated_at",
    "variant_prices": "price_paise, updated_at",
}
READABLE = {
    "products": PRODUCT_READABLE,
    "variants": VARIANT_READABLE,
    "variant_prices": PRICE_READABLE,
}

STOREFRONT_PRODUCTS = """
CREATE VIEW catalog.storefront_products WITH (security_invoker = true) AS
SELECT id AS product_id, store_id, title, description, visibility, search, created_at
FROM catalog.products
WHERE status = 'active'
"""

STOREFRONT_VARIANTS = """
CREATE VIEW catalog.storefront_variants WITH (security_invoker = true) AS
SELECT v.id AS variant_id, v.product_id, v.store_id, v.label, p.price_paise, v.created_at
FROM catalog.variants AS v
LEFT JOIN catalog.variant_prices AS p ON p.variant_id = v.id AND p.store_id = v.store_id
WHERE v.status = 'active'
"""


def create_products() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "search",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_DOCUMENT, persisted=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')", name=op.f("ck_products_status_known")
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'members')", name=op.f("ck_products_visibility_known")
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.stores.id"], name=op.f("fk_products_store_id_stores")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("store_id", "id", name=op.f("uq_products_store_id_id")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_products_store_id_status", "products", ["store_id", "status"], schema=SCHEMA
    )
    op.create_index(
        "ix_products_search", "products", ["search"], schema=SCHEMA, postgresql_using="gin"
    )
    op.create_index(
        "ix_products_title_trgm",
        "products",
        ["title"],
        schema=SCHEMA,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def create_variants() -> None:
    op.create_table(
        "variants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name=op.f("ck_variants_status_known")
        ),
        sa.ForeignKeyConstraint(
            ["store_id", "product_id"],
            ["catalog.products.store_id", "catalog.products.id"],
            name=op.f("fk_variants_store_id_products"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_variants")),
        sa.UniqueConstraint("store_id", "id", name=op.f("uq_variants_store_id_id")),
        schema=SCHEMA,
    )
    op.create_index("ix_variants_product_id", "variants", ["product_id"], schema=SCHEMA)
    op.create_table(
        "variant_prices",
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("price_paise", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "price_paise > 0 AND price_paise <= 100000000000",
            name=op.f("ck_variant_prices_price_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["store_id", "variant_id"],
            ["catalog.variants.store_id", "catalog.variants.id"],
            name=op.f("fk_variant_prices_store_id_variants"),
        ),
        sa.PrimaryKeyConstraint("variant_id", name=op.f("pk_variant_prices")),
        schema=SCHEMA,
    )


def secure(table: str) -> None:
    op.execute(f"ALTER TABLE catalog.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE catalog.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_system ON catalog.{table} FOR ALL "
        f"USING ({SYSTEM}) WITH CHECK ({SYSTEM})"
    )
    op.execute(
        f"CREATE POLICY {table}_seller ON catalog.{table} FOR ALL "
        f"USING ({SELLER}) WITH CHECK ({SELLER})"
    )
    op.execute(
        f"CREATE POLICY {table}_storefront ON catalog.{table} FOR SELECT USING ({READABLE[table]})"
    )
    op.execute(f"REVOKE UPDATE ON catalog.{table} FROM haatly_app")
    op.execute(f"GRANT UPDATE ({UPDATABLE_COLUMNS[table]}) ON catalog.{table} TO haatly_app")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLES}")
    create_products()
    create_variants()
    for table in ("products", "variants", "variant_prices"):
        secure(table)
    op.execute(STOREFRONT_PRODUCTS)
    op.execute(STOREFRONT_VARIANTS)
    op.execute(f"GRANT SELECT ON catalog.storefront_products TO {RUNTIME_ROLES}")
    op.execute(f"GRANT SELECT ON catalog.storefront_variants TO {RUNTIME_ROLES}")


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
