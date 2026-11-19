from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from haatly.catalog.public import ProductStatus, ProductVisibility
from haatly.discovery.adapters.sql_discovery import ALLOW_MISSPELLINGS, PRODUCTS_NEAR
from haatly.discovery.public import Point
from haatly.membership.public import MembershipLevel, MembershipStatus
from haatly.platform.public import (
    PUBLIC,
    SYSTEM,
    ActorRole,
    DbRole,
    Settings,
    TenantContext,
    create_engine,
    database_url,
)
from haatly.stores.public import Store, StoreStatus
from tests.world import Actor, Seeder

pytestmark = pytest.mark.anyio

SEARCH = "ikat saree"
CANDIDATES = text(
    "SELECT candidate FROM catalog.search_candidates(:q, :stores, :meters, :max) AS candidate"
)
READABLE = text("""
SELECT product_id FROM catalog.storefront_products
WHERE store_id = ANY(:stores)
AND (search @@ websearch_to_tsquery('simple', :q) OR :q <% title)
""")
REFERENCE_PRODUCTS_NEAR = text("""
WITH origin AS (
    SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography AS point
),
nearby AS (
    SELECT d.id AS store_id, d.name AS store_name, ST_Distance(l.location, origin.point) AS meters
    FROM discovery.store_locations AS l
    JOIN stores.directory AS d ON d.id = l.store_id
    CROSS JOIN origin
    WHERE ST_DWithin(l.location, origin.point, :radius)
),
wanted AS (SELECT websearch_to_tsquery('simple', :text) AS query)
SELECT p.product_id, p.store_id, n.store_name, p.title, n.meters,
    (SELECT min(v.price_paise) FROM catalog.storefront_variants AS v
     WHERE v.product_id = p.product_id) AS price_from
FROM catalog.storefront_products AS p
JOIN nearby AS n ON n.store_id = p.store_id
CROSS JOIN wanted
WHERE p.search @@ wanted.query OR :text <% p.title
ORDER BY ts_rank(p.search, wanted.query) + word_similarity(:text, p.title) DESC, n.meters,
    p.product_id
LIMIT :limit
""")


@dataclass(frozen=True)
class Shelf:
    stores: tuple[Store, ...]
    products: dict[str, UUID]
    contexts: dict[str, TenantContext]


def as_buyer(actor: Actor) -> TenantContext:
    return TenantContext(ActorRole.BUYER, actor.user_id)


def as_seller(actor: Actor, store: Store) -> TenantContext:
    return TenantContext(ActorRole.SELLER, actor.user_id, store.id)


async def shelf(seeder: Seeder) -> Shelf:
    seller_a, seller_b = await seeder.user("seller-a"), await seeder.user("seller-b")
    live_a = await seeder.store(seller_a, StoreStatus.ACTIVE, published=True)
    live_b = await seeder.store(seller_b, StoreStatus.ACTIVE, published=True)
    products = {}
    for label, visibility, status in (
        ("a_public", ProductVisibility.PUBLIC, ProductStatus.ACTIVE),
        ("a_members", ProductVisibility.MEMBERS, ProductStatus.ACTIVE),
        ("a_draft", ProductVisibility.PUBLIC, ProductStatus.DRAFT),
        ("a_archived", ProductVisibility.PUBLIC, ProductStatus.ARCHIVED),
    ):
        product = await seeder.product(
            live_a, title=f"Ikat saree {label}", visibility=visibility, status=status
        )
        products[label] = product.id
    products["b_public"] = (await seeder.product(live_b, title="Ikat saree b_public")).id
    hidden = []
    for label, store_status, published in (
        ("pending", StoreStatus.PENDING, False),
        ("suspended", StoreStatus.SUSPENDED, True),
        ("unpublished", StoreStatus.ACTIVE, False),
    ):
        store = await seeder.store(await seeder.user(label), store_status, published=published)
        products[label] = (await seeder.product(store, title=f"Ikat saree {label}")).id
        hidden.append(store)
    member, requester = await seeder.user("member"), await seeder.user("requester")
    await seeder.membership(live_a, member, MembershipLevel.CONNECTED, MembershipStatus.ACTIVE)
    await seeder.membership(live_a, requester, MembershipLevel.CONNECTED, MembershipStatus.PENDING)
    admin = await seeder.user("admin", admin=True)
    contexts = {
        "anonymous": PUBLIC,
        "buyer": as_buyer(await seeder.user("buyer")),
        "member": as_buyer(member),
        "requester": as_buyer(requester),
        "admin": TenantContext(ActorRole.ADMIN, admin.user_id),
        "seller_a": as_seller(seller_a, live_a),
        "seller_b": as_seller(seller_b, live_b),
    }
    return Shelf((live_a, live_b, *hidden), products, contexts)


async def enter(connection: AsyncConnection, context: TenantContext) -> None:
    settings = context.settings()
    await connection.execute(
        text(
            "SELECT set_config('app.role', :role, true), set_config('app.user_id', :user, true),"
            " set_config('app.store_id', :store, true)"
        ),
        {
            "role": settings["app.role"],
            "user": settings["app.user_id"],
            "store": settings["app.store_id"],
        },
    )
    await connection.execute(ALLOW_MISSPELLINGS)


async def candidates(
    engine: AsyncEngine, context: TenantContext, wanted: str, stores: tuple[Store, ...]
) -> set[UUID]:
    near = [store.id for store in stores]
    async with engine.begin() as connection:
        await enter(connection, context)
        rows = await connection.execute(
            CANDIDATES, {"q": wanted, "stores": near, "meters": [0.0] * len(near), "max": 1000}
        )
        return {row.candidate for row in rows}


async def readable(
    engine: AsyncEngine, context: TenantContext, wanted: str, stores: tuple[Store, ...]
) -> set[UUID]:
    async with engine.begin() as connection:
        await enter(connection, context)
        rows = await connection.execute(
            READABLE, {"q": wanted, "stores": [store.id for store in stores]}
        )
        return {row.product_id for row in rows}


EXPECTED = {
    "anonymous": {"a_public", "b_public"},
    "buyer": {"a_public", "b_public"},
    "member": {"a_public", "a_members", "b_public"},
    "requester": {"a_public", "b_public"},
    "admin": {"a_public", "b_public"},
    "seller_a": {"a_public", "a_members", "b_public"},
    "seller_b": {"a_public", "b_public"},
}


@pytest.mark.parametrize("actor", sorted(EXPECTED))
async def test_candidates_are_exactly_what_the_caller_can_read(
    seeder: Seeder, app_engine: AsyncEngine, actor: str
) -> None:
    world = await shelf(seeder)
    context = world.contexts[actor]
    found = await candidates(app_engine, context, SEARCH, world.stores)
    assert found == await readable(app_engine, context, SEARCH, world.stores)
    assert found == {world.products[label] for label in EXPECTED[actor]}


async def test_system_gets_only_live_storefront_candidates(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    world = await shelf(seeder)
    found = await candidates(app_engine, SYSTEM, SEARCH, world.stores)
    assert found == {world.products["a_public"], world.products["b_public"]}
    assert found <= await readable(app_engine, SYSTEM, SEARCH, world.stores)


async def test_a_hidden_title_is_not_a_search_oracle(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    world = await shelf(seeder)
    store = world.stores[0]
    secret = await seeder.product(
        store, title="Zardozi bridal veil", visibility=ProductVisibility.MEMBERS
    )
    for wanted in ("zardozi", "zardosi bridal"):
        for actor in ("anonymous", "buyer", "requester", "admin", "seller_b"):
            assert await candidates(app_engine, world.contexts[actor], wanted, (store,)) == set()
        member = await candidates(app_engine, world.contexts["member"], wanted, (store,))
        assert member == {secret.id}


async def test_only_the_given_stores_are_searched(seeder: Seeder, app_engine: AsyncEngine) -> None:
    world = await shelf(seeder)
    only_b = await candidates(app_engine, world.contexts["member"], SEARCH, world.stores[1:2])
    assert only_b == {world.products["b_public"]}
    assert await candidates(app_engine, world.contexts["member"], SEARCH, ()) == set()


async def test_the_search_copy_follows_product_changes(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    world = await shelf(seeder)
    seller = world.contexts["seller_b"]
    product_id = world.products["b_public"]
    anonymous = world.contexts["anonymous"]
    change = text(
        "UPDATE catalog.products SET title = :title, status = :status, visibility = :v "
        "WHERE id = :id"
    )
    steps = (
        ("Mulmul dupatta", "active", "public", "mulmul", True),
        ("Mulmul dupatta", "active", "public", SEARCH, False),
        ("Mulmul dupatta", "active", "members", "mulmul", False),
        ("Mulmul dupatta", "draft", "public", "mulmul", False),
        ("Mulmul dupatta", "active", "public", "mulmul", True),
    )
    for title, status, visibility, wanted, shown in steps:
        async with app_engine.begin() as connection:
            await enter(connection, seller)
            await connection.execute(
                change, {"title": title, "status": status, "v": visibility, "id": product_id}
            )
        found = await candidates(app_engine, anonymous, wanted, world.stores[1:2])
        assert (product_id in found) is shown, (title, status, visibility, wanted)


async def test_the_app_role_cannot_read_the_search_copy(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    await shelf(seeder)
    async with app_engine.begin() as connection:
        await enter(connection, SYSTEM)
        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(text("SELECT count(*) FROM catalog.product_search"))


@pytest.fixture
async def granted_back(migrated: Settings, app_engine: AsyncEngine) -> AsyncIterator[None]:
    owner = create_engine(database_url(migrated, DbRole.OWNER), pool_size=1)
    statement = "{} SELECT, INSERT, UPDATE, DELETE ON catalog.product_search {} haatly_app"
    try:
        async with owner.begin() as connection:
            await connection.execute(text(statement.format("GRANT", "TO")))
        yield
    finally:
        async with owner.begin() as connection:
            await connection.execute(text(statement.format("REVOKE", "FROM")))
        await owner.dispose()


@pytest.mark.usefixtures("granted_back")
async def test_a_stray_grant_still_shows_the_app_role_nothing(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    world = await shelf(seeder)
    for context in (*world.contexts.values(), SYSTEM):
        async with app_engine.begin() as connection:
            await enter(connection, context)
            count = text("SELECT count(*) FROM catalog.product_search")
            assert (await connection.execute(count)).scalar_one() == 0
            emptied = await connection.execute(text("DELETE FROM catalog.product_search"))
            assert emptied.rowcount == 0
    assert len(await candidates(app_engine, SYSTEM, SEARCH, world.stores)) == 2
    async with app_engine.begin() as connection:
        await enter(connection, SYSTEM)
        with pytest.raises(DBAPIError, match="row-level security"):
            await connection.execute(
                text(
                    "INSERT INTO catalog.product_search "
                    "SELECT id, store_id, status, visibility, 'forged', search "
                    "FROM catalog.products LIMIT 1"
                )
            )


async def test_discovery_matches_the_unindexed_reference(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    pin = await seeder.pin("560001", 12.975, 77.585)
    stores = []
    for index, point in enumerate(((12.98, 77.59), (12.98, 77.59), (12.99, 77.60))):
        owner = await seeder.user(f"owner-{index}")
        store = await seeder.store(owner, StoreStatus.ACTIVE, published=True)
        await seeder.locate(store, pin, Point(*point))
        stores.append((owner, store))
        for number in range(25):
            colour = ("Indigo", "Ivory")[number % 2]
            fabric = ("Ikat", "Silk")[(number // 2) % 2]
            visibility = ProductVisibility.MEMBERS if number % 4 == 0 else ProductVisibility.PUBLIC
            await seeder.product(store, title=f"{colour} {fabric} saree", visibility=visibility)
    member = await seeder.user("member")
    await seeder.membership(
        stores[0][1], member, MembershipLevel.CONNECTED, MembershipStatus.ACTIVE
    )
    contexts = (PUBLIC, as_buyer(member), as_seller(*stores[1]))
    for context in contexts:
        for wanted in ("ikat saree", "ikkat", "ivory silk", "saree"):
            params = {
                "latitude": 12.975,
                "longitude": 77.585,
                "radius": 25_000.0,
                "limit": 10,
                "text": wanted,
            }
            async with app_engine.begin() as connection:
                await enter(connection, context)
                indexed = (await connection.execute(PRODUCTS_NEAR, params)).all()
                reference = (await connection.execute(REFERENCE_PRODUCTS_NEAR, params)).all()
            assert indexed, (context, wanted)
            assert [tuple(row) for row in indexed] == [tuple(row) for row in reference]
