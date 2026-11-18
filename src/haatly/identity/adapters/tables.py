from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Table, Text
from sqlalchemy.dialects.postgresql import UUID

from haatly.platform.public import module_metadata

SCHEMA = "identity"
metadata = module_metadata(SCHEMA)


def timestamp(name: str) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=False)


users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("is_admin", Boolean, nullable=False),
    timestamp("created_at"),
    timestamp("updated_at"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey(users.c.id), nullable=False, index=True),
    Column("token_hash", Text, nullable=False, unique=True),
    timestamp("created_at"),
    timestamp("last_seen_at"),
)
