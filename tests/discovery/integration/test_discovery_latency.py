import json
import math
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from haatly.platform.public import DbRole, Settings, create_engine, database_url
from tests.world import Actor, Api, Seeder

pytestmark = pytest.mark.anyio

STORES = 200
PRODUCTS_PER_STORE = 50
SEEDED_PRODUCTS = STORES * PRODUCTS_PER_STORE
REQUESTS_PER_ENDPOINT = 200
WARM_UP = 20
P95_BUDGET_MS = 500.0
REPORT = Path(__file__).resolve().parents[3] / "build" / "discovery-latency.json"

SEED_STATEMENTS = (
    """
    INSERT INTO discovery.pin_codes (pin_code, district, state, centroid, import_version)
    VALUES ('560001', 'Bengaluru Urban', 'Karnataka',
            ST_SetSRID(ST_MakePoint(77.585, 12.975), 4326)::geography, 'latency-seed')
    """,
    """
    INSERT INTO identity.users (id, email, password_hash, is_admin, created_at, updated_at)
    SELECT md5(:salt || 'owner' || g)::uuid, 'owner-' || g || '-' || :salt || '@example.test',
           :password_hash, false, :now, :now
    FROM generate_series(1, :stores) AS g
    """,
    """
    INSERT INTO stores.stores (id, owner_user_id, name, slug, description, status, is_published,
                               price_visibility, created_at, updated_at)
    SELECT md5(:salt || 'store' || g)::uuid, md5(:salt || 'owner' || g)::uuid,
           'Weaver ' || g, 'weaver-' || g || '-' || :salt, 'Looms of store ' || g,
           'active', true, CASE WHEN g % 3 = 0 THEN 'members' ELSE 'public' END, :now, :now
    FROM generate_series(1, :stores) AS g
    """,
    """
    INSERT INTO discovery.store_locations (store_id, pin_code, location, updated_at)
    SELECT md5(:salt || 'store' || g)::uuid, '560001',
           ST_SetSRID(ST_MakePoint(77.585 + ((g * 53) % 103 - 51) * 0.004,
                                   12.975 + ((g * 37) % 101 - 50) * 0.004), 4326)::geography,
           :now
    FROM generate_series(1, :stores) AS g
    """,
    """
    INSERT INTO catalog.products (id, store_id, title, description, visibility, status,
                                  created_at, updated_at)
    SELECT md5(:salt || 'product' || p)::uuid,
           md5(:salt || 'store' || (1 + (p - 1) / :per_store))::uuid,
           (ARRAY['Red', 'Indigo', 'Mustard', 'Emerald', 'Ivory', 'Maroon', 'Peach',
                  'Black'])[1 + p % 8] || ' ' ||
           (ARRAY['Silk', 'Cotton', 'Linen', 'Khadi', 'Chanderi', 'Banarasi', 'Kanjeevaram',
                  'Ikat', 'Pashmina', 'Chiffon', 'Georgette', 'Mulmul'])[1 + (p / 8) % 12] || ' ' ||
           (ARRAY['saree', 'kurta', 'dupatta', 'shawl', 'lehenga', 'stole', 'dhoti', 'blouse',
                  'fabric', 'salwar'])[1 + (p / 96) % 10] || ' ' || p,
           'Handwoven piece number ' || p,
           CASE WHEN p % 5 = 0 THEN 'members' ELSE 'public' END, 'active', :now, :now
    FROM generate_series(1, :products) AS p
    """,
    """
    INSERT INTO catalog.variants (id, store_id, product_id, label, status, created_at, updated_at)
    SELECT md5(:salt || 'variant' || p)::uuid,
           md5(:salt || 'store' || (1 + (p - 1) / :per_store))::uuid,
           md5(:salt || 'product' || p)::uuid, 'Standard', 'active', :now, :now
    FROM generate_series(1, :products) AS p
    """,
    """
    INSERT INTO catalog.variant_prices (variant_id, store_id, price_paise, updated_at)
    SELECT md5(:salt || 'variant' || p)::uuid,
           md5(:salt || 'store' || (1 + (p - 1) / :per_store))::uuid,
           (1000 + (p * 7919) % 500000) * 100, :now
    FROM generate_series(1, :products) AS p
    """,
    """
    INSERT INTO membership.memberships (id, store_id, user_id, level, status,
                                        invited_by_user_id, created_at, updated_at)
    SELECT md5(:salt || 'membership' || g)::uuid, md5(:salt || 'store' || g)::uuid,
           :member_id, 'connected', 'active', NULL, :now, :now
    FROM generate_series(1, :stores, 10) AS g
    """,
)
MIXED_SEARCHES = (
    "silk saree",
    "kanjeevaram",
    "kanjivaram",
    "pashmeena shawl",
    "indigo cotton kurta",
    "banarsi",
    "chanderi dupatta",
    "linen",
    "maroon lehenga",
    "khadi stole",
)
QUERY_SETS = {
    "every product": ("handwoven",),
    "a tenth of products": ("saree",),
    "under 1% of products": ("pashmeena shawl",),
    "mixed, with misspellings": MIXED_SEARCHES,
}
ORIGINS = (
    "pin_code=560001",
    "latitude=12.93&longitude=77.62",
    "latitude=13.02&longitude=77.55",
)
RADII = (5, 10, 25)


async def as_system(connection: AsyncConnection) -> None:
    await connection.execute(text("SELECT set_config('app.role', 'system', true)"))


async def analyze(settings: Settings) -> None:
    owner = create_engine(database_url(settings, DbRole.OWNER), pool_size=1)
    try:
        async with owner.connect() as connection:
            await connection.execute(text("ANALYZE"))
    finally:
        await owner.dispose()


@pytest.fixture
def analyzer(migrated: Settings) -> Callable[[], Awaitable[None]]:
    return partial(analyze, migrated)


async def seed(engine: AsyncEngine, password_hash: str, member: Actor) -> int:
    parameters = {
        "salt": secrets.token_hex(4),
        "password_hash": password_hash,
        "now": datetime.now(UTC),
        "stores": STORES,
        "products": SEEDED_PRODUCTS,
        "per_store": PRODUCTS_PER_STORE,
        "member_id": member.user_id,
    }
    async with engine.begin() as connection:
        await as_system(connection)
        for statement in SEED_STATEMENTS:
            await connection.execute(text(statement), parameters)
    async with engine.begin() as connection:
        await as_system(connection)
        counted = text("SELECT count(*) FROM catalog.products")
        return int((await connection.execute(counted)).scalar_one())


async def matching(engine: AsyncEngine) -> dict[str, int]:
    counted = text(
        "SELECT count(*) FROM catalog.products "
        "WHERE search @@ websearch_to_tsquery('simple', :q) OR :q <% title"
    )
    found = {}
    async with engine.begin() as connection:
        await as_system(connection)
        await connection.execute(
            text("SELECT set_config('pg_trgm.word_similarity_threshold', '0.5', true)")
        )
        for searches in QUERY_SETS.values():
            for wanted in searches:
                found[wanted] = int((await connection.execute(counted, {"q": wanted})).scalar_one())
    return found


@dataclass(frozen=True)
class Timing:
    name: str
    millis: tuple[float, ...]
    results: int

    def percentile(self, fraction: float) -> float:
        ordered = sorted(self.millis)
        return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]

    def summary(self) -> dict[str, float | int | str]:
        return {
            "endpoint": self.name,
            "requests": len(self.millis),
            "results": self.results,
            "p50_ms": round(self.percentile(0.50), 1),
            "p95_ms": round(self.percentile(0.95), 1),
            "p99_ms": round(self.percentile(0.99), 1),
            "max_ms": round(max(self.millis), 1),
        }


def paths(endpoint: str, searches: tuple[str, ...]) -> list[str]:
    built = []
    for index in range(WARM_UP + REQUESTS_PER_ENDPOINT):
        origin = ORIGINS[index % len(ORIGINS)]
        radius = RADII[(index // len(ORIGINS)) % len(RADII)]
        query = f"{origin}&radius_km={radius}"
        if searches:
            query = f"q={searches[index % len(searches)]}&{query}"
        built.append(f"/discover/{endpoint}?{query}")
    return built


async def measure(
    api: Api, name: str, endpoint: str, token: str | None, searches: tuple[str, ...] = ()
) -> Timing:
    millis = []
    results = 0
    for index, path in enumerate(paths(endpoint, searches)):
        started = time.perf_counter()
        response = await api.call("GET", path, token=token)
        elapsed = (time.perf_counter() - started) * 1000
        assert response.status_code == 200, response.text
        if index >= WARM_UP:
            millis.append(elapsed)
            results += len(response.json())
    return Timing(name, tuple(millis), results)


async def test_discovery_p95_over_ten_thousand_products(
    api: Api,
    app_engine: AsyncEngine,
    seeder: Seeder,
    password_hash: str,
    analyzer: Callable[[], Awaitable[None]],
) -> None:
    member = await seeder.user("regular")
    assert member.token is not None
    assert await seed(app_engine, password_hash, member) == SEEDED_PRODUCTS
    await analyzer()
    timings = [await measure(api, "stores, anonymous", "stores", None)]
    for label, searches in QUERY_SETS.items():
        for viewer, token in (("anonymous", None), ("member of 20 stores", member.token)):
            name = f"products, {label}, {viewer}"
            timings.append(await measure(api, name, "products", token, searches))
    report = {
        "seeded_products": SEEDED_PRODUCTS,
        "seeded_stores": STORES,
        "matching_products": await matching(app_engine),
        "budget_p95_ms": P95_BUDGET_MS,
        "timings": [timing.summary() for timing in timings],
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\ndiscovery latency: {json.dumps(report, indent=2)}")
    for timing in timings:
        assert timing.results > 0, f"{timing.name} found nothing, so it measured nothing"
        assert timing.percentile(0.95) <= P95_BUDGET_MS, timing.summary()
