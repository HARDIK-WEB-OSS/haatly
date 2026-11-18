from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.platform.public import SYSTEM
from haatly.stores.public import SqlStoresUnitOfWorkFactory
from tests.security.leak_suite import (
    FOREIGN_ACTORS,
    VICTIM_STATES,
    LeakSuite,
    RouteCase,
    SuiteSetupError,
    route_cases,
)
from tests.world import Api, Seeder

pytestmark = pytest.mark.anyio


def app_of(api: Api) -> FastAPI:
    return api.app


async def test_no_route_leaks_to_a_foreign_tenant_actor(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    cases = route_cases(app_of(api))
    assert len(cases) >= 20
    result = await LeakSuite(api, seeder, app_engine).run(cases)
    print(f"\nRLS leak suite: {result.summary()}")
    assert result.probes == len(cases) * len(FOREIGN_ACTORS) * len(VICTIM_STATES)
    assert result.leaks == [], result.summary()
    assert result.uncovered == [], "routes no actor could use, so their probes prove nothing"


async def test_every_documented_operation_is_enumerated(api: Api) -> None:
    app = app_of(api)
    documented = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    found = {(case.method, case.path) for case in route_cases(app)}
    assert documented <= found
    assert {("GET", "/health"), ("GET", "/ready")} <= found
    assert len({case.path for case in route_cases(app)}) >= 20


def plant_leaky_route(app: FastAPI, engine: AsyncEngine) -> RouteCase:
    stores = SqlStoresUnitOfWorkFactory(engine)

    async def leaky(store_id: UUID) -> dict[str, str]:
        async with stores(SYSTEM) as uow:
            store = await uow.stores.get(store_id)
        assert store is not None
        return {"owner": str(store.owner_user_id), "slug": store.slug}

    app.add_api_route("/planted/{store_id}", leaky, methods=["GET"])
    return next(case for case in route_cases(app) if case.path == "/planted/{store_id}")


async def test_the_suite_catches_a_planted_leaky_route(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    case = plant_leaky_route(app_of(api), app_engine)
    result = await LeakSuite(api, seeder, app_engine).run([case])
    assert case in route_cases(app_of(api))
    leaking_probes = {leak.split(": exposes")[0] for leak in result.leaks}
    assert len(leaking_probes) == len(FOREIGN_ACTORS) * len(VICTIM_STATES)
    assert all("exposes" in leak for leak in result.leaks)


async def test_a_route_with_an_unknown_path_parameter_fails_the_suite(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    async def unknown(order_id: UUID) -> None:
        return None

    app_of(api).add_api_route("/planted/orders/{order_id}", unknown, methods=["GET"])
    case = next(c for c in route_cases(app_of(api)) if c.path == "/planted/orders/{order_id}")
    with pytest.raises(SuiteSetupError, match="order_id"):
        await LeakSuite(api, seeder, app_engine).run([case])
