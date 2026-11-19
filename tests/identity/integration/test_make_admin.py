import asyncio
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.identity.public import (
    AdminGrant,
    AdminGrants,
    SqlIdentityUnitOfWorkFactory,
    make_admin,
    make_admin_main,
)
from haatly.platform.public import (
    DbRole,
    FrozenClock,
    Settings,
    UnsupportedRuntimeRoleError,
    Uuid7Source,
    create_engine,
    database_url,
)
from tests.conftest import truncate_everything
from tests.world import Seeder

AUDIT_ROWS = text(
    "SELECT action, actor_role, actor_user_id, details FROM platform.audit_log "
    "WHERE subject_id = :id"
)


async def is_admin(engine: AsyncEngine, user_id: UUID) -> bool:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        sql = text("SELECT is_admin FROM identity.users WHERE id = :id")
        return bool((await connection.execute(sql, {"id": user_id})).scalar_one())


async def audit_rows(engine: AsyncEngine, user_id: UUID) -> list[tuple[object, ...]]:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        return [tuple(row) for row in await connection.execute(AUDIT_ROWS, {"id": user_id})]


@pytest.mark.anyio
async def test_make_admin_grants_once_and_audits_the_grant(
    migrated: Settings, app_engine: AsyncEngine, seeder: Seeder
) -> None:
    user = await seeder.user("operator-target")
    assert user.email is not None
    assert user.user_id is not None
    assert await make_admin(migrated, user.email.upper()) is AdminGrant.GRANTED
    assert await is_admin(app_engine, user.user_id)
    assert await make_admin(migrated, user.email) is AdminGrant.ALREADY_ADMIN
    expected = {"via": "make-admin", "database_role": "haatly_ops"}
    assert await audit_rows(app_engine, user.user_id) == [
        ("identity.admin_granted", "system", None, expected)
    ]


@pytest.mark.anyio
async def test_the_app_role_cannot_grant_admin(app_engine: AsyncEngine, seeder: Seeder) -> None:
    user = await seeder.user("not-via-the-app")
    assert user.email is not None
    assert user.user_id is not None
    clock = FrozenClock()
    grants = AdminGrants(SqlIdentityUnitOfWorkFactory(app_engine), Uuid7Source(clock), clock)
    with pytest.raises(DBAPIError, match="permission denied"):
        await grants.grant(user.email, "haatly_app")
    assert not await is_admin(app_engine, user.user_id)
    assert await audit_rows(app_engine, user.user_id) == []


def test_the_ops_role_needs_its_own_password(migrated: Settings) -> None:
    without = migrated.model_copy(update={"db_ops_password": None})
    with pytest.raises(UnsupportedRuntimeRoleError):
        database_url(without, DbRole.OPS)


async def seed_user(settings: Settings, password_hash: str) -> tuple[str, UUID]:
    engine = create_engine(database_url(settings, DbRole.APP), pool_size=1)
    try:
        user = await Seeder(engine, password_hash).user("cli-target")
    finally:
        await engine.dispose()
    assert user.email is not None
    assert user.user_id is not None
    return user.email, user.user_id


def test_the_command_line_reports_each_outcome(
    migrated: Settings, password_hash: str, capsys: pytest.CaptureFixture[str]
) -> None:
    email, _ = asyncio.run(seed_user(migrated, password_hash))
    try:
        assert make_admin_main([], migrated) == 2
        assert make_admin_main(["nobody@example.test"], migrated) == 1
        assert make_admin_main(["not an email"], migrated) == 1
        assert make_admin_main([email], migrated) == 0
        assert make_admin_main([email], migrated) == 0
    finally:
        asyncio.run(truncate_everything(migrated))
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        f"granted admin to {email}",
        f"{email} is already an admin; nothing changed",
    ]
    assert "usage: make_admin <email>" in captured.err
    assert "no account is registered with nobody@example.test" in captured.err
