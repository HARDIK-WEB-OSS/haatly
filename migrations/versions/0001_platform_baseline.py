from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform"
RUNTIME_ROLES = "haatly_app, haatly_ops"

NOTIFY_FUNCTION = """
CREATE FUNCTION platform.notify_wakeup() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('haatly_wakeup', TG_TABLE_NAME);
    RETURN NULL;
END
$$
"""


def create_idempotency_keys() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(state = 'completed') = (response_status IS NOT NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_idempotency_keys_response_only_when_completed"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_idempotency_keys_request_hash_is_sha256"),
        ),
        sa.CheckConstraint(
            "state IN ('in_progress', 'completed')", name=op.f("ck_idempotency_keys_state_known")
        ),
        sa.CheckConstraint(
            "char_length(key) BETWEEN 1 AND 255", name=op.f("ck_idempotency_keys_key_length")
        ),
        sa.PrimaryKeyConstraint("scope", "key", name=op.f("pk_idempotency_keys")),
        schema=SCHEMA,
    )


def create_jobs() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'running') = (locked_until IS NOT NULL)",
            name=op.f("ck_jobs_lease_only_when_running"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'dead')",
            name=op.f("ck_jobs_status_known"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_jobs_attempts_not_negative")),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_jobs_max_attempts_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_jobs_dedupe_key")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_jobs_ready",
        "jobs",
        ["status", "run_after"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def create_outbox() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["occurred_at", "id"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "processed_events",
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("consumer", "event_id", name=op.f("pk_processed_events")),
        schema=SCHEMA,
    )


def create_wakeup_triggers() -> None:
    op.execute(NOTIFY_FUNCTION)
    for table in ("jobs", "outbox_events"):
        op.execute(
            f"CREATE TRIGGER {table}_wakeup AFTER INSERT ON platform.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION platform.notify_wakeup()"
        )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLES}")
    create_idempotency_keys()
    create_jobs()
    create_outbox()
    create_wakeup_triggers()


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
