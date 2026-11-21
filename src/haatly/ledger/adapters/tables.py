from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from haatly.platform.public import module_metadata

SCHEMA = "ledger"
metadata = module_metadata(SCHEMA)


def timestamp(name: str, *, nullable: bool = False) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


accounts = Table(
    "accounts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("ledger", Integer, nullable=False),
    Column("code", Integer, nullable=False),
    Column("flags", ARRAY(Text), nullable=False),
    timestamp("created_at"),
    CheckConstraint("ledger > 0", name="ledger_positive"),
    CheckConstraint("code BETWEEN 1 AND 65535", name="code_in_range"),
)

intents = Table(
    "intents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("reference_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("result_code", Text, nullable=True),
    timestamp("created_at"),
    timestamp("settled_at", nullable=True),
    timestamp("verified_at", nullable=True),
    Column("actor_role", Text, nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=True),
    Column("store_id", UUID(as_uuid=True), nullable=True),
    UniqueConstraint("kind", "reference_id"),
    CheckConstraint(
        "actor_role IN ('public', 'buyer', 'seller', 'admin', 'system')",
        name="actor_role_known",
    ),
    CheckConstraint("status IN ('pending', 'applied', 'rejected', 'flagged')", name="status_known"),
    CheckConstraint("(status = 'pending') = (settled_at IS NULL)", name="settled_unless_pending"),
    CheckConstraint("verified_at IS NULL OR status = 'applied'", name="verified_only_when_applied"),
    CheckConstraint(
        "(status IN ('rejected', 'flagged')) = (result_code IS NOT NULL)",
        name="code_only_when_refused",
    ),
    Index("ix_intents_pending", "created_at", postgresql_where=text("status = 'pending'")),
    Index(
        "ix_intents_unverified",
        "settled_at",
        postgresql_where=text("status = 'applied' AND verified_at IS NULL"),
    ),
)

intent_accounts = Table(
    "intent_accounts",
    metadata,
    Column("intent_id", UUID(as_uuid=True), ForeignKey(intents.c.id), nullable=False),
    Column("position", Integer, nullable=False),
    Column("account_id", UUID(as_uuid=True), ForeignKey(accounts.c.id), nullable=False),
    PrimaryKeyConstraint("intent_id", "position"),
    Index("ix_intent_accounts_account_id", "account_id"),
)

transfers = Table(
    "transfers",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("intent_id", UUID(as_uuid=True), ForeignKey(intents.c.id), nullable=False),
    Column("position", Integer, nullable=False),
    Column("debit_account_id", UUID(as_uuid=True), nullable=False),
    Column("credit_account_id", UUID(as_uuid=True), nullable=False),
    Column("amount", BigInteger, nullable=False),
    Column("ledger", Integer, nullable=False),
    Column("code", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("pending_id", UUID(as_uuid=True), nullable=True),
    Column("timeout_seconds", BigInteger, nullable=False),
    Column("outcome", Text, nullable=True),
    UniqueConstraint("intent_id", "position"),
    CheckConstraint("amount >= 0", name="amount_not_negative"),
    CheckConstraint(
        "kind IN ('single', 'pending', 'post_pending', 'void_pending')", name="kind_known"
    ),
    CheckConstraint(
        "(kind IN ('post_pending', 'void_pending')) = (pending_id IS NOT NULL)",
        name="pending_id_only_when_resolving",
    ),
    CheckConstraint("timeout_seconds BETWEEN 0 AND 4294967295", name="timeout_in_range"),
)

drift = Table(
    "drift",
    metadata,
    Column("transfer_id", UUID(as_uuid=True), primary_key=True),
    Column("intent_id", UUID(as_uuid=True), nullable=True),
    Column("reason", Text, nullable=False),
    timestamp("detected_at"),
    timestamp("resolved_at", nullable=True),
    Index("ix_drift_open", "detected_at", postgresql_where=text("resolved_at IS NULL")),
)

cursors = Table(
    "cursors",
    metadata,
    Column("name", Text, primary_key=True),
    Column("position", BigInteger, nullable=False),
    CheckConstraint("position >= 0", name="position_not_negative"),
)
