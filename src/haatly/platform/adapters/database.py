from enum import StrEnum

from sqlalchemy import URL, MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from haatly.platform.adapters.settings import Settings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class DbRole(StrEnum):
    OWNER = "haatly_owner"
    APP = "haatly_app"
    OPS = "haatly_ops"


class UnsupportedRuntimeRoleError(ValueError):
    pass


def module_metadata(schema: str) -> MetaData:
    return MetaData(schema=schema, naming_convention=NAMING_CONVENTION)


def database_url(settings: Settings, role: DbRole) -> URL:
    passwords = {DbRole.OWNER: settings.db_owner_password, DbRole.APP: settings.db_app_password}
    password = passwords.get(role)
    if password is None:
        raise UnsupportedRuntimeRoleError(role)
    return URL.create(
        "postgresql+asyncpg",
        username=role.value,
        password=password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )


def plain_dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def create_engine(url: URL, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)
