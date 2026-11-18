from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"
RUNTIME_ROLES = "haatly_app, haatly_ops"

POLICIES = (
    "CREATE POLICY users_system ON identity.users FOR ALL "
    "USING (platform.actor_role() = 'system') WITH CHECK (platform.actor_role() = 'system')",
    "CREATE POLICY users_self_read ON identity.users FOR SELECT "
    "USING (id = platform.actor_user_id())",
    "CREATE POLICY users_self_update ON identity.users FOR UPDATE "
    "USING (id = platform.actor_user_id()) WITH CHECK (id = platform.actor_user_id())",
    "CREATE POLICY sessions_system ON identity.sessions FOR ALL "
    "USING (platform.actor_role() = 'system') WITH CHECK (platform.actor_role() = 'system')",
)


def create_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        schema=SCHEMA,
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity.users.id"], name=op.f("fk_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], schema=SCHEMA)


def secure_tables() -> None:
    for table in ("users", "sessions"):
        op.execute(f"ALTER TABLE identity.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE identity.{table} FORCE ROW LEVEL SECURITY")
    for policy in POLICIES:
        op.execute(policy)
    op.execute("REVOKE UPDATE ON identity.users FROM haatly_app")
    op.execute("GRANT UPDATE (password_hash, updated_at) ON identity.users TO haatly_app")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLES}")
    create_tables()
    secure_tables()


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
