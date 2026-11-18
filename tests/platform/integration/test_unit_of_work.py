from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.platform.adapters.unit_of_work import SqlUnitOfWork, UnitOfWorkNotStartedError
from haatly.platform.public import (
    PUBLIC,
    SYSTEM,
    ActorRole,
    FrozenClock,
    NewJob,
    SqlUnitOfWorkFactory,
    TenantContext,
    Uuid7Source,
    create_engine,
    enqueue_job,
)

pytestmark = pytest.mark.anyio

USER = UUID("018f0000-0000-7000-8000-0000000000a1")
STORE = UUID("018f0000-0000-7000-8000-0000000000b1")
SETTING_NAMES = ("app.role", "app.user_id", "app.store_id")


async def read_settings(uow: SqlUnitOfWork) -> dict[str, str | None]:
    values = select(*(func.current_setting(name, True) for name in SETTING_NAMES))
    row = (await uow.connection.execute(values)).one()
    return dict(zip(SETTING_NAMES, row, strict=True))


async def test_tenant_context_is_set_locally_for_the_transaction(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    async with uow_factory(TenantContext(ActorRole.SELLER, USER, STORE)) as uow:
        assert await read_settings(uow) == {
            "app.role": "seller",
            "app.user_id": str(USER),
            "app.store_id": str(STORE),
        }


async def test_tenant_context_does_not_leak_to_the_next_transaction(
    app_engine: AsyncEngine,
) -> None:
    single_connection = create_engine(app_engine.url, pool_size=1)
    factory = SqlUnitOfWorkFactory(single_connection)
    try:
        async with factory(TenantContext(ActorRole.SELLER, USER, STORE)) as uow:
            first_pid = (await uow.connection.execute(select(func.pg_backend_pid()))).scalar()
        async with single_connection.connect() as connection:
            same_pid = (await connection.execute(select(func.pg_backend_pid()))).scalar()
            leaked = (
                await connection.execute(text("SELECT current_setting('app.store_id', true)"))
            ).scalar()
    finally:
        await single_connection.dispose()
    assert same_pid == first_pid
    assert leaked in (None, "")


async def test_public_context_sets_role_without_identity(uow_factory: SqlUnitOfWorkFactory) -> None:
    async with uow_factory(PUBLIC) as uow:
        assert await read_settings(uow) == {
            "app.role": "public",
            "app.user_id": "",
            "app.store_id": "",
        }


async def test_an_exception_rolls_the_transaction_back(uow_factory: SqlUnitOfWorkFactory) -> None:
    clock = FrozenClock()
    ids = Uuid7Source(clock)

    async def enqueue_then_fail() -> None:
        async with uow_factory(SYSTEM) as uow:
            await enqueue_job(uow, ids, clock, NewJob("test.kind", dedupe_key="rolled-back"))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await enqueue_then_fail()
    async with uow_factory(SYSTEM) as uow:
        assert await enqueue_job(uow, ids, clock, NewJob("test.kind", dedupe_key="rolled-back"))


async def test_connection_is_unavailable_outside_the_block(
    uow_factory: SqlUnitOfWorkFactory,
) -> None:
    uow = uow_factory(SYSTEM)
    with pytest.raises(UnitOfWorkNotStartedError):
        _ = uow.connection
