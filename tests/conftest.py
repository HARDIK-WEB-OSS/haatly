from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import ExecWaitStrategy

from haatly.platform.public import (
    DbRole,
    Environment,
    Settings,
    SqlUnitOfWorkFactory,
    create_engine,
    database_url,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = "postgis/postgis:16-3.5"
ROLES_SCRIPT = REPO_ROOT / "docker" / "postgres" / "initdb" / "01-roles.sql"
TEST_DATABASE = "haatly_test"
TEST_PASSWORDS = {
    "POSTGRES_PASSWORD": "test-superuser",
    "HAATLY_DB_OWNER_PASSWORD": "test-owner",
    "HAATLY_DB_APP_PASSWORD": "test-app",
    "HAATLY_DB_OPS_PASSWORD": "test-ops",
}
PLATFORM_TABLES = (
    "platform.jobs",
    "platform.outbox_events",
    "platform.processed_events",
    "platform.idempotency_keys",
)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[DockerContainer]:
    container = (
        DockerContainer(POSTGRES_IMAGE)
        .with_env("POSTGRES_DB", TEST_DATABASE)
        .with_envs(**TEST_PASSWORDS)
        .with_exposed_ports(5432)
        .with_volume_mapping(str(ROLES_SCRIPT), "/docker-entrypoint-initdb.d/20-haatly-roles.sql")
        .waiting_for(ExecWaitStrategy(["pg_isready", "-h", "127.0.0.1", "-U", "postgres"]))
    )
    with container:
        yield container


def loopback(host: str) -> str:
    return "127.0.0.1" if host == "localhost" else host


@pytest.fixture(scope="session")
def settings(postgres_container: DockerContainer) -> Settings:
    return Settings(
        environment=Environment.TEST,
        db_host=loopback(postgres_container.get_container_host_ip()),
        db_port=int(postgres_container.get_exposed_port(5432)),
        db_name=TEST_DATABASE,
        db_owner_password=SecretStr(TEST_PASSWORDS["HAATLY_DB_OWNER_PASSWORD"]),
        db_app_password=SecretStr(TEST_PASSWORDS["HAATLY_DB_APP_PASSWORD"]),
    )


def alembic_config(settings: Settings) -> Config:
    config = Config(toml_file=REPO_ROOT / "pyproject.toml")
    config.attributes["url"] = database_url(settings, DbRole.OWNER)
    return config


@pytest.fixture(scope="session")
def migrated(settings: Settings) -> Settings:
    command.upgrade(alembic_config(settings), "head")
    return settings


@pytest.fixture
async def owner_engine(migrated: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url(migrated, DbRole.OWNER), pool_size=2)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(migrated: Settings, owner_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url(migrated, DbRole.APP), pool_size=20)
    yield engine
    await engine.dispose()
    async with owner_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {', '.join(PLATFORM_TABLES)}"))


@pytest.fixture
def uow_factory(app_engine: AsyncEngine) -> SqlUnitOfWorkFactory:
    return SqlUnitOfWorkFactory(app_engine)
