import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.stores.public import StoreStatus
from tests.world import Api, Seeder

pytestmark = pytest.mark.anyio

ROLE_ATTRIBUTES = (
    "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
)
OWNED_TABLES = "SELECT count(*) FROM pg_tables WHERE tableowner = current_user"
VISIBLE_STORES = "SELECT count(*) FROM stores.stores"


async def role_of(engine: AsyncEngine) -> tuple[object, ...]:
    async with engine.connect() as connection:
        attributes = (await connection.execute(text(ROLE_ATTRIBUTES))).one()
        owned = (await connection.execute(text(OWNED_TABLES))).scalar_one()
    return (*attributes, owned)


async def test_every_test_engine_is_the_app_role_that_cannot_bypass_rls(
    app_engine: AsyncEngine, api: Api
) -> None:
    engines = {"fixture engine": app_engine, "HTTP app engine": api.app.state.engine}
    roles = {name: await role_of(engine) for name, engine in engines.items()}
    expected = ("haatly_app", False, False, 0)
    assert roles == dict.fromkeys(engines, expected), (
        "tests must run as haatly_app: not a superuser, no BYPASSRLS, owning no tables"
    )


async def test_rls_really_filters_rows_on_the_test_session(
    app_engine: AsyncEngine, seeder: Seeder
) -> None:
    owner = await seeder.user("owner")
    await seeder.store(owner, StoreStatus.PENDING, published=False)
    async with app_engine.begin() as connection:
        without_context = (await connection.execute(text(VISIBLE_STORES))).scalar_one()
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        as_system = (await connection.execute(text(VISIBLE_STORES))).scalar_one()
    assert (without_context, as_system) == (0, 1)
