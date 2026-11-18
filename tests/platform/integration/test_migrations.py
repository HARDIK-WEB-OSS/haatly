from alembic import command

from haatly.platform.public import Settings
from tests.conftest import alembic_config


def test_migrations_round_trip_without_drift(migrated: Settings) -> None:
    config = alembic_config(migrated)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
