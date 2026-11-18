from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "membership"
RUNTIME_ROLES = "haatly_app, haatly_ops"
SYSTEM = "platform.actor_role() = 'system'"
SELLER = "store_id = platform.actor_store_id() AND platform.actor_role() = 'seller'"
ME = "user_id = platform.actor_user_id()"
CAPABILITY = "token_hash = current_setting('app.invitation_token_hash', true)"
HOLDS_INVITATION = (
    "EXISTS (SELECT 1 FROM membership.invitations AS held "
    "WHERE held.store_id = memberships.store_id "
    "AND held.accepted_by_user_id = platform.actor_user_id() "
    "AND held.token_hash = current_setting('app.invitation_token_hash', true))"
)
INVITED = f"{ME} AND level = 'seller_added' AND status = 'active' AND {HOLDS_INVITATION}"
REQUESTED = (
    f"{ME} AND level = 'connected' AND status = 'pending' AND stores.is_live(store_id) "
    "AND stores.owner_of(store_id) IS DISTINCT FROM platform.actor_user_id()"
)

POLICIES = (
    f"CREATE POLICY memberships_system ON membership.memberships FOR ALL "
    f"USING ({SYSTEM}) WITH CHECK ({SYSTEM})",
    f"CREATE POLICY memberships_seller_read ON membership.memberships FOR SELECT USING ({SELLER})",
    f"CREATE POLICY memberships_seller_update ON membership.memberships FOR UPDATE "
    f"USING ({SELLER}) WITH CHECK ({SELLER})",
    f"CREATE POLICY memberships_self_read ON membership.memberships FOR SELECT USING ({ME})",
    f"CREATE POLICY memberships_self_request ON membership.memberships FOR INSERT "
    f"WITH CHECK ({REQUESTED})",
    f"CREATE POLICY memberships_self_invited ON membership.memberships FOR INSERT "
    f"WITH CHECK ({INVITED})",
    f"CREATE POLICY memberships_self_upgrade ON membership.memberships FOR UPDATE "
    f"USING ({ME}) WITH CHECK ({INVITED})",
    f"CREATE POLICY invitations_system ON membership.invitations FOR ALL "
    f"USING ({SYSTEM}) WITH CHECK ({SYSTEM})",
    f"CREATE POLICY invitations_seller_read ON membership.invitations FOR SELECT USING ({SELLER})",
    f"CREATE POLICY invitations_seller_create ON membership.invitations FOR INSERT "
    f"WITH CHECK ({SELLER} AND created_by_user_id = platform.actor_user_id())",
    f"CREATE POLICY invitations_seller_update ON membership.invitations FOR UPDATE "
    f"USING ({SELLER}) WITH CHECK ({SELLER})",
    f"CREATE POLICY invitations_holder_read ON membership.invitations FOR SELECT "
    f"USING ({CAPABILITY})",
    f"CREATE POLICY invitations_holder_claim ON membership.invitations FOR UPDATE "
    f"USING ({CAPABILITY}) "
    f"WITH CHECK ({CAPABILITY} AND accepted_by_user_id = platform.actor_user_id())",
)


def create_memberships() -> None:
    op.create_table(
        "memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "level IN ('connected', 'seller_added')", name=op.f("ck_memberships_level_known")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'blocked')", name=op.f("ck_memberships_status_known")
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["identity.users.id"],
            name=op.f("fk_memberships_invited_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.stores.id"], name=op.f("fk_memberships_store_id_stores")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity.users.id"], name=op.f("fk_memberships_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("store_id", "id", name=op.f("uq_memberships_store_id_id")),
        sa.UniqueConstraint("store_id", "user_id", name=op.f("uq_memberships_store_id_user_id")),
        schema=SCHEMA,
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"], schema=SCHEMA)


def create_invitations() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by_user_id IS NULL)",
            name=op.f("ck_invitations_acceptance_complete"),
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name=op.f("ck_invitations_used_or_revoked"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["identity.users.id"],
            name=op.f("fk_invitations_accepted_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["identity.users.id"],
            name=op.f("fk_invitations_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.stores.id"], name=op.f("fk_invitations_store_id_stores")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("store_id", "id", name=op.f("uq_invitations_store_id_id")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
        schema=SCHEMA,
    )
    op.create_index("ix_invitations_store_id", "invitations", ["store_id"], schema=SCHEMA)


def secure_tables() -> None:
    for table in ("memberships", "invitations"):
        op.execute(f"ALTER TABLE membership.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE membership.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE UPDATE ON membership.{table} FROM haatly_app")
    for policy in POLICIES:
        op.execute(policy)
    op.execute(
        "GRANT UPDATE (level, status, invited_by_user_id, updated_at) "
        "ON membership.memberships TO haatly_app"
    )
    op.execute(
        "GRANT UPDATE (accepted_by_user_id, accepted_at, revoked_at) "
        "ON membership.invitations TO haatly_app"
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLES}")
    create_memberships()
    create_invitations()
    secure_tables()


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
