from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType

from haatly.platform.public import module_metadata
from haatly.stores.public import stores_metadata

SCHEMA = "discovery"
metadata = module_metadata(SCHEMA)
stores = stores_metadata.tables["stores.stores"]


class Geography(UserDefinedType[Any]):
    cache_ok = True

    def __init__(self, *_: Any) -> None:
        super().__init__()

    def get_col_spec(self, **_: Any) -> str:
        return "geography(Point,4326)"


ischema_names["geography"] = Geography


def timestamp(name: str) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=False)


pin_codes = Table(
    "pin_codes",
    metadata,
    Column("pin_code", Text, primary_key=True),
    Column("district", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("centroid", Geography(), nullable=False),
    Column("import_version", Text, nullable=False),
    CheckConstraint("pin_code ~ '^[1-9][0-9]{5}$'", name="pin_code_shape"),
)

pin_imports = Table(
    "pin_imports",
    metadata,
    Column("version", Text, primary_key=True),
    Column("row_count", Integer, nullable=False),
    Column("pin_count", Integer, nullable=False),
    timestamp("imported_at"),
)

store_locations = Table(
    "store_locations",
    metadata,
    Column("store_id", UUID(as_uuid=True), ForeignKey(stores.c.id), primary_key=True),
    Column("pin_code", Text, ForeignKey(pin_codes.c.pin_code), nullable=False),
    Column("location", Geography(), nullable=False),
    timestamp("updated_at"),
    Index("ix_store_locations_location", "location", postgresql_using="gist"),
)
