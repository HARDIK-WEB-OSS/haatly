from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from haatly.identity.public import identity_metadata
from haatly.platform.public import module_metadata
from haatly.stores.public import stores_metadata

SCHEMA = "membership"
metadata = module_metadata(SCHEMA)
users = identity_metadata.tables["identity.users"]
stores = stores_metadata.tables["stores.stores"]


def timestamp(name: str, *, nullable: bool = False) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


memberships = Table(
    "memberships",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("store_id", UUID(as_uuid=True), ForeignKey(stores.c.id), nullable=False),
    Column("user_id", UUID(as_uuid=True), ForeignKey(users.c.id), nullable=False),
    Column("level", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("invited_by_user_id", UUID(as_uuid=True), ForeignKey(users.c.id), nullable=True),
    timestamp("created_at"),
    timestamp("updated_at"),
    UniqueConstraint("store_id", "user_id"),
    UniqueConstraint("store_id", "id"),
    CheckConstraint("level IN ('connected', 'seller_added')", name="level_known"),
    CheckConstraint("status IN ('pending', 'active', 'blocked')", name="status_known"),
    Index("ix_memberships_user_id", "user_id"),
)

invitations = Table(
    "invitations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("store_id", UUID(as_uuid=True), ForeignKey(stores.c.id), nullable=False),
    Column("created_by_user_id", UUID(as_uuid=True), ForeignKey(users.c.id), nullable=False),
    Column("token_hash", Text, nullable=False, unique=True),
    Column("label", Text, nullable=False),
    timestamp("expires_at"),
    Column("accepted_by_user_id", UUID(as_uuid=True), ForeignKey(users.c.id), nullable=True),
    timestamp("accepted_at", nullable=True),
    timestamp("revoked_at", nullable=True),
    timestamp("created_at"),
    UniqueConstraint("store_id", "id"),
    CheckConstraint("accepted_at IS NULL OR revoked_at IS NULL", name="used_or_revoked"),
    CheckConstraint(
        "(accepted_at IS NULL) = (accepted_by_user_id IS NULL)", name="acceptance_complete"
    ),
    Index("ix_invitations_store_id", "store_id"),
)
