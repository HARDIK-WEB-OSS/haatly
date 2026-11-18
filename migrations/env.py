import asyncio

from alembic import context
from sqlalchemy import URL, Connection, MetaData

from haatly.ledger.public import ledger_metadata
from haatly.platform.public import DbRole, Settings, create_engine, database_url, platform_metadata

MODULE_METADATA: tuple[MetaData, ...] = (platform_metadata, ledger_metadata)
MANAGED_SCHEMAS = frozenset(metadata.schema for metadata in MODULE_METADATA)
VERSION_TABLE_SCHEMA = "public"


class OfflineMigrationsUnsupportedError(RuntimeError):
    pass


def include_name(name: str | None, type_: str, _parents: object) -> bool:
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def configure_and_run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=list(MODULE_METADATA),
        include_schemas=True,
        include_name=include_name,
        version_table_schema=VERSION_TABLE_SCHEMA,
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migration_url() -> URL:
    configured = context.config.attributes.get("url")
    if isinstance(configured, URL):
        return configured
    return database_url(Settings(), DbRole.OWNER)


async def run_online() -> None:
    engine = create_engine(migration_url(), pool_size=1)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(configure_and_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise OfflineMigrationsUnsupportedError
asyncio.run(run_online())
