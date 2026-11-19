from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"

VISIBLE_SCOPES_FUNCTION = """
CREATE FUNCTION catalog.visible_product_scopes()
RETURNS TABLE (store_id uuid, visibility text, status text)
LANGUAGE sql STABLE AS $$
SELECT live.id, 'public'::text, 'active'::text
FROM stores.directory AS live
UNION ALL
SELECT mine.store_id, 'members'::text, 'active'::text
FROM membership.my_active_memberships AS mine
WHERE mine.store_id IN (SELECT live.id FROM stores.directory AS live)
UNION ALL
SELECT platform.actor_store_id(), own.visibility, 'active'::text
FROM (VALUES ('public'::text), ('members'::text)) AS own (visibility)
WHERE platform.actor_role() = 'seller'
$$
"""

STOREFRONT_POLICY = """
CREATE POLICY products_storefront ON catalog.products FOR SELECT USING (
    (store_id, visibility, status) IN (
        SELECT scope.store_id, scope.visibility, scope.status
        FROM catalog.visible_product_scopes() AS scope
    )
)
"""

PREVIOUS_STOREFRONT_POLICY = """
CREATE POLICY products_storefront ON catalog.products FOR SELECT USING (
    products.status = 'active'
    AND EXISTS (SELECT 1 FROM stores.directory AS live WHERE live.id = products.store_id)
    AND (
        products.visibility = 'public'
        OR EXISTS (
            SELECT 1 FROM membership.my_active_memberships AS mine
            WHERE mine.store_id = products.store_id
        )
    )
)
"""

SYNC_FUNCTION = """
CREATE FUNCTION catalog.sync_product_search() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO catalog.product_search (product_id, store_id, status, visibility, title, search)
    VALUES (NEW.id, NEW.store_id, NEW.status, NEW.visibility, NEW.title, NEW.search)
    ON CONFLICT (product_id) DO UPDATE SET
        status = EXCLUDED.status,
        visibility = EXCLUDED.visibility,
        title = EXCLUDED.title,
        search = EXCLUDED.search;
    RETURN NULL;
END
$$
"""

SYNC_TRIGGER = """
CREATE TRIGGER products_search_sync
AFTER INSERT OR UPDATE OF title, description, visibility, status ON catalog.products
FOR EACH ROW EXECUTE FUNCTION catalog.sync_product_search()
"""

CANDIDATES_FUNCTION = """
CREATE FUNCTION catalog.search_candidates(
    wanted text, near_stores uuid[], near_meters double precision[], max_results integer
) RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER ROWS 50 SET search_path = pg_catalog, public AS $$
BEGIN
    RETURN QUERY EXECUTE $candidates$
        SELECT found.product_id
        FROM catalog.product_search AS found
        CROSS JOIN websearch_to_tsquery('simple'::regconfig, $1) AS query
        WHERE found.store_id = ANY ($2)
        AND (found.search @@ query OR $1 <% found.title)
        AND (found.store_id, found.visibility, found.status) IN (
            SELECT scope.store_id, scope.visibility, scope.status
            FROM catalog.visible_product_scopes() AS scope
        )
        ORDER BY ts_rank(found.search, query) + word_similarity($1, found.title) DESC,
            $3[array_position($2, found.store_id)], found.product_id
        LIMIT $4
    $candidates$ USING wanted, near_stores, near_meters, max_results;
END
$$
"""

DATABASE_JIT = """
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I {}', current_database());
END
$$
"""

CANDIDATES_SIGNATURE = "catalog.search_candidates(text, uuid[], double precision[], integer)"

BACKFILL = """
INSERT INTO catalog.product_search (product_id, store_id, status, visibility, title, search)
SELECT id, store_id, status, visibility, title, search FROM catalog.products
"""


def create_search_table() -> None:
    op.create_table(
        "product_search",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("search", postgresql.TSVECTOR(), nullable=False),
        sa.ForeignKeyConstraint(
            ["store_id", "product_id"],
            ["catalog.products.store_id", "catalog.products.id"],
            name=op.f("fk_product_search_store_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("product_id", name=op.f("pk_product_search")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_product_search_search",
        "product_search",
        ["search"],
        schema=SCHEMA,
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    )
    op.create_index(
        "ix_product_search_title_trgm",
        "product_search",
        ["title"],
        schema=SCHEMA,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
        postgresql_with={"fastupdate": "off"},
    )


def secure_search_table() -> None:
    op.execute("REVOKE ALL ON catalog.product_search FROM haatly_app, haatly_ops")
    op.execute("ALTER TABLE catalog.product_search ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE catalog.product_search FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY product_search_owner ON catalog.product_search FOR ALL "
        "TO haatly_owner USING (true) WITH CHECK (true)"
    )


def share_visibility_rule() -> None:
    op.execute(VISIBLE_SCOPES_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION catalog.visible_product_scopes() FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION catalog.visible_product_scopes() TO haatly_app, haatly_ops"
    )
    op.execute("DROP POLICY products_storefront ON catalog.products")
    op.execute(STOREFRONT_POLICY)


def upgrade() -> None:
    create_search_table()
    secure_search_table()
    share_visibility_rule()
    op.execute(SYNC_FUNCTION)
    op.execute(SYNC_TRIGGER)
    op.execute(CANDIDATES_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION catalog.sync_product_search() FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {CANDIDATES_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {CANDIDATES_SIGNATURE} TO haatly_app")
    op.execute(DATABASE_JIT.format("SET jit = off"))
    op.execute("SELECT set_config('app.role', 'system', true)")
    op.execute(BACKFILL)
    op.execute("ANALYZE catalog.product_search")


def downgrade() -> None:
    op.execute(DATABASE_JIT.format("RESET jit"))
    op.execute(f"DROP FUNCTION {CANDIDATES_SIGNATURE}")
    op.execute("DROP TRIGGER products_search_sync ON catalog.products")
    op.execute("DROP FUNCTION catalog.sync_product_search()")
    op.execute("DROP POLICY products_storefront ON catalog.products")
    op.execute(PREVIOUS_STOREFRONT_POLICY)
    op.execute("DROP FUNCTION catalog.visible_product_scopes()")
    op.drop_table("product_search", schema=SCHEMA)
