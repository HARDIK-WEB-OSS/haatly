from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"

ACTOR_FUNCTIONS = (
    """
    CREATE FUNCTION platform.actor_role() RETURNS text
    LANGUAGE sql STABLE AS $$
        SELECT coalesce(nullif(current_setting('app.role', true), ''), 'none')
    $$
    """,
    """
    CREATE FUNCTION platform.actor_user_id() RETURNS uuid
    LANGUAGE sql STABLE AS $$
        SELECT nullif(current_setting('app.user_id', true), '')::uuid
    $$
    """,
    """
    CREATE FUNCTION platform.actor_store_id() RETURNS uuid
    LANGUAGE sql STABLE AS $$
        SELECT nullif(current_setting('app.store_id', true), '')::uuid
    $$
    """,
    """
    CREATE FUNCTION platform.refuse_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
    END
    $$
    """,
)


def create_audit_log() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_log_subject", "audit_log", ["subject_type", "subject_id"], schema=SCHEMA
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON platform.audit_log FROM haatly_app, haatly_ops")
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON platform.audit_log "
        "FOR EACH ROW EXECUTE FUNCTION platform.refuse_change()"
    )
    op.execute("ALTER TABLE platform.audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.audit_log FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_log_append ON platform.audit_log FOR INSERT WITH CHECK ("
        "actor_role = platform.actor_role() "
        "AND actor_user_id IS NOT DISTINCT FROM platform.actor_user_id())"
    )
    op.execute(
        "CREATE POLICY audit_log_read ON platform.audit_log FOR SELECT "
        "USING (platform.actor_role() IN ('admin', 'system'))"
    )


def create_rate_limit_counters() -> None:
    op.create_table(
        "rate_limit_counters",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint("count > 0", name=op.f("ck_rate_limit_counters_count_positive")),
        sa.PrimaryKeyConstraint("key", "window_start", name=op.f("pk_rate_limit_counters")),
        schema=SCHEMA,
    )


def upgrade() -> None:
    for statement in ACTOR_FUNCTIONS:
        op.execute(statement)
    create_audit_log()
    create_rate_limit_counters()


def downgrade() -> None:
    op.drop_table("rate_limit_counters", schema=SCHEMA)
    op.drop_table("audit_log", schema=SCHEMA)
    for name in ("refuse_change", "actor_store_id", "actor_user_id", "actor_role"):
        op.execute(f"DROP FUNCTION platform.{name}()")
