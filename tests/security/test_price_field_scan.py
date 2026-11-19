from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.catalog.public import SqlCatalogUnitOfWorkFactory
from haatly.platform.public import SYSTEM
from tests.security.leak_suite import (
    FOREIGN_ACTORS,
    PRICE_SCAN_ACTORS,
    PRICE_SCAN_STATES,
    LeakSuite,
    price_sentinels,
    route_cases,
)
from tests.world import Api, Seeder

pytestmark = pytest.mark.anyio


async def test_no_response_shows_a_members_only_price_to_a_non_member(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    cases = route_cases(api.app)
    scan = LeakSuite(
        api,
        seeder,
        app_engine,
        states=PRICE_SCAN_STATES,
        secrets_of=price_sentinels,
        control_actors=PRICE_SCAN_ACTORS,
    )
    result = await scan.run(cases)
    print(f"\nprice-field scan: {result.summary()}")
    assert result.probes == len(cases) * len(FOREIGN_ACTORS) * len(PRICE_SCAN_STATES)
    assert result.leaks == [], result.summary()
    assert result.uncovered == []
    assert result.insider_saw_secret, "a member must see the prices the scan hides from others"


async def test_the_scan_catches_a_route_that_reads_prices_as_system(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    catalog = SqlCatalogUnitOfWorkFactory(app_engine)

    async def leaky(product_id: UUID) -> dict[str, list[int]]:
        async with catalog(SYSTEM) as uow:
            variants = await uow.variants.for_products([product_id])
        return {"prices": [variant.price_paise for variant in variants]}

    api.app.add_api_route("/planted/prices/{product_id}", leaky, methods=["GET"])
    case = next(c for c in route_cases(api.app) if c.path == "/planted/prices/{product_id}")
    scan = LeakSuite(api, seeder, app_engine, states=PRICE_SCAN_STATES, secrets_of=price_sentinels)
    result = await scan.run([case])
    leaking_probes = {leak.split(": exposes")[0] for leak in result.leaks}
    assert len(leaking_probes) == len(FOREIGN_ACTORS) * len(PRICE_SCAN_STATES)
