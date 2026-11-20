from datetime import timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.platform.public import PUBLIC, ActorRole, FrozenClock, TenantContext
from haatly.stores.public import Callers, SqlCallerUnitOfWorkFactory, StoreStatus
from tests.world import Seeder

pytestmark = pytest.mark.anyio


def callers(engine: AsyncEngine, clock: FrozenClock | None = None) -> Callers:
    return Callers(SqlCallerUnitOfWorkFactory(engine), clock or FrozenClock())


async def last_seen(engine: AsyncEngine, user_id: object) -> object:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        statement = text("SELECT last_seen_at FROM identity.sessions WHERE user_id = :u")
        return (await connection.execute(statement, {"u": user_id})).scalar_one()


async def test_each_kind_of_caller_gets_its_context(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    buyer = await seeder.user("buyer")
    admin = await seeder.user("admin", admin=True)
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.PENDING, published=False)
    resolve = callers(app_engine).context_for
    assert await resolve(None) == PUBLIC
    assert await resolve("not-a-session-token") == PUBLIC
    assert await resolve(buyer.token) == TenantContext(ActorRole.BUYER, buyer.user_id)
    assert await resolve(admin.token) == TenantContext(ActorRole.ADMIN, admin.user_id)
    assert await resolve(owner.token) == TenantContext(ActorRole.SELLER, owner.user_id, store.id)


async def test_an_expired_session_is_public_and_a_live_one_is_touched(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    buyer = await seeder.user("buyer")
    clock = FrozenClock(seeder.clock.now())
    resolve = callers(app_engine, clock).context_for
    first_seen = await last_seen(app_engine, buyer.user_id)
    clock.advance(timedelta(seconds=30))
    assert (await resolve(buyer.token)).role is ActorRole.BUYER
    assert await last_seen(app_engine, buyer.user_id) == first_seen
    clock.advance(timedelta(minutes=1))
    assert (await resolve(buyer.token)).role is ActorRole.BUYER
    assert await last_seen(app_engine, buyer.user_id) == clock.now()
    clock.advance(timedelta(hours=24, seconds=1))
    assert await resolve(buyer.token) == PUBLIC


async def test_a_signed_in_caller_is_resolved_in_one_transaction(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner = await seeder.user("owner")
    await seeder.store(owner, StoreStatus.ACTIVE, published=True)
    resolve = callers(app_engine).context_for
    checkouts: list[object] = []
    statements: list[str] = []
    event.listen(app_engine.sync_engine.pool, "checkout", lambda *_: checkouts.append(1))
    event.listen(
        app_engine.sync_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, sql, *_: statements.append(sql),
    )
    context = await resolve(owner.token)
    assert context.role is ActorRole.SELLER
    assert len(checkouts) == 1
    assert len(statements) == 3, statements
