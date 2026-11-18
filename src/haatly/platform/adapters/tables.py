from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from haatly.platform.adapters.database import module_metadata

SCHEMA = "platform"
metadata = module_metadata(SCHEMA)


def timestamp(name: str, *, nullable: bool = False) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


jobs = Table(
    "jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("dedupe_key", Text, nullable=True, unique=True),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    timestamp("run_after"),
    timestamp("locked_until", nullable=True),
    Column("last_error", Text, nullable=True),
    timestamp("created_at"),
    timestamp("updated_at"),
    CheckConstraint("status IN ('queued', 'running', 'succeeded', 'dead')", name="status_known"),
    CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
    CheckConstraint(
        "(status = 'running') = (locked_until IS NOT NULL)", name="lease_only_when_running"
    ),
    Index(
        "ix_jobs_ready",
        "status",
        "run_after",
        postgresql_where=text("status IN ('queued', 'running')"),
    ),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("topic", Text, nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    timestamp("occurred_at"),
    timestamp("published_at", nullable=True),
    Index(
        "ix_outbox_events_unpublished",
        "occurred_at",
        "id",
        postgresql_where=text("published_at IS NULL"),
    ),
)

processed_events = Table(
    "processed_events",
    metadata,
    Column("consumer", Text, nullable=False),
    Column("event_id", UUID(as_uuid=True), nullable=False),
    timestamp("processed_at"),
    PrimaryKeyConstraint("consumer", "event_id"),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("scope", Text, nullable=False),
    Column("key", Text, nullable=False),
    Column("request_hash", Text, nullable=False),
    Column("state", Text, nullable=False),
    timestamp("lease_expires_at"),
    Column("response_status", Integer, nullable=True),
    Column("response_body", Text, nullable=True),
    timestamp("created_at"),
    timestamp("completed_at", nullable=True),
    PrimaryKeyConstraint("scope", "key"),
    CheckConstraint("state IN ('in_progress', 'completed')", name="state_known"),
    CheckConstraint("char_length(key) BETWEEN 1 AND 255", name="key_length"),
    CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_is_sha256"),
    CheckConstraint(
        "(state = 'completed') = (response_status IS NOT NULL AND completed_at IS NOT NULL)",
        name="response_only_when_completed",
    ),
)
