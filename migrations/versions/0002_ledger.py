from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "ledger"
RUNTIME_ROLES = "haatly_app, haatly_ops"


def timestamp(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def create_accounts() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ledger", sa.Integer(), nullable=False),
        sa.Column("code", sa.Integer(), nullable=False),
        sa.Column("flags", postgresql.ARRAY(sa.Text()), nullable=False),
        timestamp("created_at"),
        sa.CheckConstraint("ledger > 0", name=op.f("ck_accounts_ledger_positive")),
        sa.CheckConstraint("code BETWEEN 1 AND 65535", name=op.f("ck_accounts_code_in_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        schema=SCHEMA,
    )


def create_intents() -> None:
    op.create_table(
        "intents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reference_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_code", sa.Text(), nullable=True),
        timestamp("created_at"),
        timestamp("settled_at", nullable=True),
        timestamp("verified_at", nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'rejected', 'flagged')",
            name=op.f("ck_intents_status_known"),
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (settled_at IS NULL)",
            name=op.f("ck_intents_settled_unless_pending"),
        ),
        sa.CheckConstraint(
            "verified_at IS NULL OR status = 'applied'",
            name=op.f("ck_intents_verified_only_when_applied"),
        ),
        sa.CheckConstraint(
            "(status IN ('rejected', 'flagged')) = (result_code IS NOT NULL)",
            name=op.f("ck_intents_code_only_when_refused"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_intents")),
        sa.UniqueConstraint("kind", "reference_id", name=op.f("uq_intents_kind_reference_id")),
        schema=SCHEMA,
    )
    create_intent_indexes()


def create_intent_indexes() -> None:
    op.create_index(
        "ix_intents_pending",
        "intents",
        ["created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_intents_unverified",
        "intents",
        ["settled_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'applied' AND verified_at IS NULL"),
    )


def create_intent_accounts() -> None:
    op.create_table(
        "intent_accounts",
        sa.Column("intent_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["ledger.accounts.id"],
            name=op.f("fk_intent_accounts_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["ledger.intents.id"],
            name=op.f("fk_intent_accounts_intent_id_intents"),
        ),
        sa.PrimaryKeyConstraint("intent_id", "position", name=op.f("pk_intent_accounts")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_intent_accounts_account_id", "intent_accounts", ["account_id"], schema=SCHEMA
    )


def create_transfers() -> None:
    op.create_table(
        "transfers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("intent_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("debit_account_id", sa.UUID(), nullable=False),
        sa.Column("credit_account_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("ledger", sa.Integer(), nullable=False),
        sa.Column("code", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("pending_id", sa.UUID(), nullable=True),
        sa.Column("timeout_seconds", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_transfers_amount_not_negative")),
        sa.CheckConstraint(
            "kind IN ('single', 'pending', 'post_pending', 'void_pending')",
            name=op.f("ck_transfers_kind_known"),
        ),
        sa.CheckConstraint(
            "(kind IN ('post_pending', 'void_pending')) = (pending_id IS NOT NULL)",
            name=op.f("ck_transfers_pending_id_only_when_resolving"),
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 0 AND 4294967295",
            name=op.f("ck_transfers_timeout_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["ledger.intents.id"], name=op.f("fk_transfers_intent_id_intents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfers")),
        sa.UniqueConstraint("intent_id", "position", name=op.f("uq_transfers_intent_id_position")),
        schema=SCHEMA,
    )


def create_reconciliation_tables() -> None:
    op.create_table(
        "drift",
        sa.Column("transfer_id", sa.UUID(), nullable=False),
        sa.Column("intent_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        timestamp("detected_at"),
        timestamp("resolved_at", nullable=True),
        sa.PrimaryKeyConstraint("transfer_id", name=op.f("pk_drift")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_drift_open",
        "drift",
        ["detected_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_table(
        "cursors",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("position >= 0", name=op.f("ck_cursors_position_not_negative")),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_cursors")),
        schema=SCHEMA,
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLES}")
    create_accounts()
    create_intents()
    create_intent_accounts()
    create_transfers()
    create_reconciliation_tables()


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
