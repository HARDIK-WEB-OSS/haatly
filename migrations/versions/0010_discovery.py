from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import UserDefinedType

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "discovery"
RUNTIME_ROLES = "haatly_app, haatly_ops"
SYSTEM = "platform.actor_role() = 'system'"
SELLER = "store_id = platform.actor_store_id() AND platform.actor_role() = 'seller'"
LIVE = "EXISTS (SELECT 1 FROM stores.directory AS live WHERE live.id = store_locations.store_id)"


class PointGeography(UserDefinedType[Any]):
    cache_ok = True

    def get_col_spec(self, **_: Any) -> str:
        return "geography(Point,4326)"


def create_pin_tables() -> None:
    op.create_table(
        "pin_codes",
        sa.Column("pin_code", sa.Text(), nullable=False),
        sa.Column("district", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("centroid", PointGeography(), nullable=False),
        sa.Column("import_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "pin_code ~ '^[1-9][0-9]{5}$'", name=op.f("ck_pin_codes_pin_code_shape")
        ),
        sa.PrimaryKeyConstraint("pin_code", name=op.f("pk_pin_codes")),
        schema=SCHEMA,
    )
    op.create_table(
        "pin_imports",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("pin_count", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_pin_imports")),
        schema=SCHEMA,
    )


def create_store_locations() -> None:
    op.create_table(
        "store_locations",
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("pin_code", sa.Text(), nullable=False),
        sa.Column("location", PointGeography(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["pin_code"],
            ["discovery.pin_codes.pin_code"],
            name=op.f("fk_store_locations_pin_code_pin_codes"),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.stores.id"], name=op.f("fk_store_locations_store_id_stores")
        ),
        sa.PrimaryKeyConstraint("store_id", name=op.f("pk_store_locations")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_store_locations_location",
        "store_locations",
        ["location"],
        schema=SCHEMA,
        postgresql_using="gist",
    )


def secure_store_locations() -> None:
    op.execute("ALTER TABLE discovery.store_locations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE discovery.store_locations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY store_locations_system ON discovery.store_locations FOR ALL "
        f"USING ({SYSTEM}) WITH CHECK ({SYSTEM})"
    )
    op.execute(
        "CREATE POLICY store_locations_seller ON discovery.store_locations FOR ALL "
        f"USING ({SELLER}) WITH CHECK ({SELLER})"
    )
    op.execute(
        f"CREATE POLICY store_locations_live ON discovery.store_locations FOR SELECT USING ({LIVE})"
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {RUNTIME_ROLES}")
    create_pin_tables()
    create_store_locations()
    secure_store_locations()


def downgrade() -> None:
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
