import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.anyio


async def scalar(engine: AsyncEngine, sql: str) -> object:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql))).scalar()


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE platform.intruder (id int)",
        "CREATE TABLE public.intruder (id int)",
        "ALTER TABLE platform.jobs DISABLE ROW LEVEL SECURITY",
        "DROP TABLE platform.jobs",
        "CREATE SCHEMA intruder",
    ],
)
async def test_app_role_cannot_change_the_schema(app_engine: AsyncEngine, statement: str) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(ProgrammingError):
            await connection.execute(text(statement))


async def test_app_role_cannot_bypass_row_level_security(app_engine: AsyncEngine) -> None:
    sql = "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"
    assert await scalar(app_engine, sql) is False


async def test_owner_role_is_not_a_superuser(app_engine: AsyncEngine) -> None:
    sql = "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = 'haatly_owner'"
    assert await scalar(app_engine, sql) is False


async def test_ops_role_logs_every_statement(app_engine: AsyncEngine) -> None:
    sql = (
        "SELECT array_to_string(setconfig, ',') FROM pg_db_role_setting "
        "JOIN pg_roles ON pg_roles.oid = setrole WHERE rolname = 'haatly_ops'"
    )
    assert "log_statement=all" in str(await scalar(app_engine, sql))


async def test_app_role_can_use_platform_tables(app_engine: AsyncEngine) -> None:
    assert await scalar(app_engine, "SELECT count(*) FROM platform.jobs") == 0
