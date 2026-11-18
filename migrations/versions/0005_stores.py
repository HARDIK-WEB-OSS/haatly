from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "stores"
RUNTIME_ROLES = "haatly_app, haatly_ops"
OWNER = "owner_user_id = platform.actor_user_id()"
OWN_STORE = f"{OWNER} AND id = platform.actor_store_id() AND platform.actor_role() = 'seller'"

POLICIES = (
    "CREATE POLICY stores_system ON stores.stores FOR ALL "
    "USING (platform.actor_role() = 'system') WITH CHECK (platform.actor_role() = 'system')",
    f"CREATE POLICY stores_owner_read ON stores.stores FOR SELECT USING ({OWNER})",
    f"CREATE POLICY stores_owner_create ON stores.stores FOR INSERT WITH CHECK ("
    f"{OWNER} AND platform.actor_role() = 'buyer' AND status = 'pending' AND NOT is_published)",
    f"CREATE POLICY stores_owner_update ON stores.stores FOR UPDATE "
    f"USING ({OWN_STORE}) WITH CHECK ({OWN_STORE})",
    "CREATE POLICY stores_admin ON stores.stores FOR SELECT "
    "USING (platform.actor_role() = 'admin')",
    "CREATE POLICY stores_admin_update ON stores.stores FOR UPDATE "
    "USING (platform.actor_role() = 'admin') WITH CHECK (platform.actor_role() = 'admin')",
    "CREATE POLICY stores_live ON stores.stores FOR SELECT "
    "USING (status = 'active' AND is_published)",
)

STATUS_GUARD = """
CREATE FUNCTION stores.guard_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status
       AND platform.actor_role() NOT IN ('admin', 'system') THEN
        RAISE EXCEPTION 'only an admin can change a store status'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$
"""

SYSTEM_READS = (
    (
        "accepts_members",
        "boolean",
        "SELECT EXISTS (SELECT 1 FROM stores.stores WHERE id = target AND status = 'active')",
    ),
    (
        "is_live",
        "boolean",
        "SELECT EXISTS (SELECT 1 FROM stores.stores "
        "WHERE id = target AND status = 'active' AND is_published)",
    ),
    ("owner_of", "uuid", "SELECT owner_user_id FROM stores.stores WHERE id = target"),
)


def system_read(name: str, returns: str, query: str) -> str:
    return f"""
    CREATE FUNCTION stores.{name}(target uuid) RETURNS {returns}
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    DECLARE
        caller text := current_setting('app.role', true);
        answer {returns};
    BEGIN
        PERFORM set_config('app.role', 'system', true);
        {query} INTO answer;
        PERFORM set_config('app.role', coalesce(caller, ''), true);
        RETURN answer;
    END
    $$
    """


def create_stores() -> None:
    op.create_table(
        "stores",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("price_visibility", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "price_visibility IN ('public', 'members')",
            name=op.f("ck_stores_price_visibility_known"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended')", name=op.f("ck_stores_status_known")
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["identity.users.id"], name=op.f("fk_stores_owner_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stores")),
        sa.UniqueConstraint("owner_user_id", name=op.f("uq_stores_owner_user_id")),
        sa.UniqueConstraint("slug", name=op.f("uq_stores_slug")),
        schema=SCHEMA,
    )


def secure_stores() -> None:
    op.execute("ALTER TABLE stores.stores ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE stores.stores FORCE ROW LEVEL SECURITY")
    for policy in POLICIES:
        op.execute(policy)
    op.execute("REVOKE UPDATE ON stores.stores FROM haatly_app")
    op.execute(
        "GRANT UPDATE (name, description, status, is_published, price_visibility, updated_at) "
        "ON stores.stores TO haatly_app"
    )
    op.execute(STATUS_GUARD)
    op.execute(
        "CREATE TRIGGER stores_status_guard BEFORE UPDATE ON stores.stores "
        "FOR EACH ROW EXECUTE FUNCTION stores.guard_status()"
    )
    for name, returns, query in SYSTEM_READS:
        op.execute(system_read(name, returns, query))


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLES}")
    create_stores()
    secure_stores()


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
