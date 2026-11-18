from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Table, Text
from sqlalchemy.dialects.postgresql import UUID

from haatly.identity.public import identity_metadata
from haatly.platform.public import module_metadata

SCHEMA = "stores"
metadata = module_metadata(SCHEMA)
users = identity_metadata.tables["identity.users"]


def timestamp(name: str) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=False)


stores = Table(
    "stores",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "owner_user_id", UUID(as_uuid=True), ForeignKey(users.c.id), nullable=False, unique=True
    ),
    Column("name", Text, nullable=False),
    Column("slug", Text, nullable=False, unique=True),
    Column("description", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("is_published", Boolean, nullable=False),
    Column("price_visibility", Text, nullable=False),
    timestamp("created_at"),
    timestamp("updated_at"),
    CheckConstraint("status IN ('pending', 'active', 'suspended')", name="status_known"),
    CheckConstraint("price_visibility IN ('public', 'members')", name="price_visibility_known"),
)
