from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"
IS_SYSTEM = "platform.actor_role() = 'system'"
HAS_CONTEXT = "platform.actor_role() IN ('public', 'buyer', 'seller', 'admin', 'system')"
OWNS_KEY = (
    "actor_role = platform.actor_role() "
    "AND user_id IS NOT DISTINCT FROM platform.actor_user_id() "
    "AND store_id IS NOT DISTINCT FROM platform.actor_store_id()"
)
SECURED_TABLES = ("idempotency_keys", "jobs", "outbox_events", "processed_events")
POLICIES = (
    ("idempotency_keys_owner", "idempotency_keys", "ALL", OWNS_KEY, OWNS_KEY),
    ("idempotency_keys_system", "idempotency_keys", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("jobs_append", "jobs", "INSERT", None, HAS_CONTEXT),
    ("jobs_system", "jobs", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("outbox_events_append", "outbox_events", "INSERT", None, HAS_CONTEXT),
    ("outbox_events_system", "outbox_events", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("processed_events_system", "processed_events", "ALL", IS_SYSTEM, IS_SYSTEM),
)


def policy_sql(name: str, table: str, command: str, using: str | None, check: str) -> str:
    clauses = "" if using is None else f" USING ({using})"
    return f"CREATE POLICY {name} ON {SCHEMA}.{table} FOR {command}{clauses} WITH CHECK ({check})"


def add_key_owner() -> None:
    op.add_column(
        "idempotency_keys",
        sa.Column("actor_role", sa.Text(), nullable=False, server_default="system"),
        schema=SCHEMA,
    )
    op.alter_column("idempotency_keys", "actor_role", server_default=None, schema=SCHEMA)
    op.add_column("idempotency_keys", sa.Column("user_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.add_column(
        "idempotency_keys", sa.Column("store_id", sa.UUID(), nullable=True), schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_idempotency_keys_actor_role_known"),
        "idempotency_keys",
        "actor_role IN ('public', 'buyer', 'seller', 'admin', 'system')",
        schema=SCHEMA,
    )


def upgrade() -> None:
    add_key_owner()
    for table in SECURED_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    for policy in POLICIES:
        op.execute(policy_sql(*policy))


def downgrade() -> None:
    for name, table, *_ in POLICIES:
        op.execute(f"DROP POLICY {name} ON {SCHEMA}.{table}")
    for table in SECURED_TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
    op.drop_constraint(
        op.f("ck_idempotency_keys_actor_role_known"), "idempotency_keys", schema=SCHEMA
    )
    for column in ("store_id", "user_id", "actor_role"):
        op.drop_column("idempotency_keys", column, schema=SCHEMA)
