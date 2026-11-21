from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ledger"
IS_SYSTEM = "platform.actor_role() = 'system'"
HAS_CONTEXT = "platform.actor_role() IN ('public', 'buyer', 'seller', 'admin', 'system')"
OWNS_INTENT = (
    "actor_role = platform.actor_role() "
    "AND user_id IS NOT DISTINCT FROM platform.actor_user_id() "
    "AND store_id IS NOT DISTINCT FROM platform.actor_store_id()"
)
INTENT_VISIBLE = "EXISTS (SELECT 1 FROM ledger.intents AS parent WHERE parent.id = intent_id)"
SECURED_TABLES = ("accounts", "intents", "intent_accounts", "transfers", "drift", "cursors")
POLICIES = (
    ("accounts_read", "accounts", "SELECT", HAS_CONTEXT, None),
    ("accounts_append", "accounts", "INSERT", None, HAS_CONTEXT),
    ("accounts_system", "accounts", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("intents_owner", "intents", "ALL", OWNS_INTENT, OWNS_INTENT),
    ("intents_system", "intents", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("intent_accounts_owner", "intent_accounts", "ALL", INTENT_VISIBLE, INTENT_VISIBLE),
    ("intent_accounts_system", "intent_accounts", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("transfers_owner", "transfers", "ALL", INTENT_VISIBLE, INTENT_VISIBLE),
    ("transfers_system", "transfers", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("drift_system", "drift", "ALL", IS_SYSTEM, IS_SYSTEM),
    ("cursors_system", "cursors", "ALL", IS_SYSTEM, IS_SYSTEM),
)


def policy_sql(name: str, table: str, command: str, using: str | None, check: str | None) -> str:
    using_clause = "" if using is None else f" USING ({using})"
    check_clause = "" if check is None else f" WITH CHECK ({check})"
    return f"CREATE POLICY {name} ON {SCHEMA}.{table} FOR {command}{using_clause}{check_clause}"


def add_intent_owner() -> None:
    op.add_column(
        "intents",
        sa.Column("actor_role", sa.Text(), nullable=False, server_default="system"),
        schema=SCHEMA,
    )
    op.alter_column("intents", "actor_role", server_default=None, schema=SCHEMA)
    op.add_column("intents", sa.Column("user_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.add_column("intents", sa.Column("store_id", sa.UUID(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_intents_actor_role_known"),
        "intents",
        "actor_role IN ('public', 'buyer', 'seller', 'admin', 'system')",
        schema=SCHEMA,
    )


def upgrade() -> None:
    add_intent_owner()
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
    op.drop_constraint(op.f("ck_intents_actor_role_known"), "intents", schema=SCHEMA)
    for column in ("store_id", "user_id", "actor_role"):
        op.drop_column("intents", column, schema=SCHEMA)
