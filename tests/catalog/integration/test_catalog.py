import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.catalog.public import ProductStatus, ProductVisibility
from haatly.membership.public import MembershipLevel, MembershipStatus
from haatly.stores.public import PriceVisibility, Store, StoreStatus
from tests.world import ANONYMOUS, Actor, Api, Seeder

pytestmark = pytest.mark.anyio


async def seller_with_store(
    seeder: Seeder, prices: PriceVisibility = PriceVisibility.PUBLIC
) -> tuple[Actor, Store]:
    owner = await seeder.user("owner")
    return owner, await seeder.store(owner, StoreStatus.ACTIVE, published=True, prices=prices)


async def test_a_seller_builds_a_product_with_variants(api: Api, seeder: Seeder) -> None:
    owner, store = await seller_with_store(seeder)
    store_id = str(store.id)
    created = await api.call(
        "POST",
        "/v1/seller/products",
        token=owner.token,
        json={"title": "Kanjeevaram silk", "description": "Handwoven in Kanchipuram"},
    )
    assert (created.status_code, created.json()["status"]) == (201, "draft")
    product_id = created.json()["id"]
    variant = await api.call(
        "POST",
        f"/v1/seller/products/{product_id}/variants",
        token=owner.token,
        json={"label": "6 yards", "price_paise": 1_250_000},
    )
    assert variant.json()["price_paise"] == 1_250_000
    repriced = await api.call(
        "PATCH",
        f"/v1/seller/variants/{variant.json()['id']}",
        token=owner.token,
        json={"price_paise": 1_300_000, "label": "6.2 yards"},
    )
    assert (repriced.json()["price_paise"], repriced.json()["label"]) == (1_300_000, "6.2 yards")
    assert (await api.call("GET", f"/v1/stores/{store_id}/products")).json() == []
    published = await api.call(
        "PATCH", f"/v1/seller/products/{product_id}", token=owner.token, json={"status": "active"}
    )
    assert published.json()["variants"][0]["price_paise"] == 1_300_000
    listing = (await api.call("GET", f"/v1/stores/{store_id}/products")).json()
    assert [(p["title"], p["price_from_paise"]) for p in listing] == [
        ("Kanjeevaram silk", 1_300_000)
    ]
    mine = await api.call("GET", "/v1/seller/products", token=owner.token)
    assert [p["id"] for p in mine.json()] == [product_id]


@pytest.mark.parametrize(
    "body",
    [
        {"label": "x", "price_paise": 0},
        {"label": "x", "price_paise": -5},
        {"label": "x", "price_paise": 100_000_000_001},
        {"label": "x", "price_paise": 12.5},
        {"label": "x", "price_paise": "100"},
        {"label": "x", "price_paise": True},
    ],
)
async def test_prices_are_positive_whole_paise(
    api: Api, seeder: Seeder, body: dict[str, object]
) -> None:
    owner, store = await seller_with_store(seeder)
    product = await seeder.product(store)
    response = await api.call(
        "POST", f"/v1/seller/products/{product.id}/variants", token=owner.token, json=body
    )
    assert response.status_code == 422


async def test_another_sellers_products_are_unreachable(api: Api, seeder: Seeder) -> None:
    owner, _ = await seller_with_store(seeder)
    other, _ = await seller_with_store(seeder)
    created = await api.call(
        "POST",
        "/v1/seller/products",
        token=owner.token,
        json={"title": "Ikat", "description": "Cotton"},
    )
    product_id = created.json()["id"]
    assert (
        await api.call("GET", f"/v1/seller/products/{product_id}", token=other.token)
    ).status_code == 404
    patched = await api.call(
        "PATCH", f"/v1/seller/products/{product_id}", token=other.token, json={"title": "Mine now"}
    )
    assert patched.status_code == 404
    variant = await api.call(
        "POST",
        f"/v1/seller/products/{product_id}/variants",
        token=other.token,
        json={"label": "x", "price_paise": 100},
    )
    assert variant.status_code == 404


VISIBILITY_MATRIX = [
    (PriceVisibility.PUBLIC, "stranger", {"public": 500}),
    (PriceVisibility.PUBLIC, "member", {"public": 500, "members": 900}),
    (PriceVisibility.MEMBERS, "stranger", {"public": None}),
    (PriceVisibility.MEMBERS, "member", {"public": 500, "members": 900}),
    (PriceVisibility.MEMBERS, "owner", {"public": 500, "members": 900}),
]


@pytest.mark.parametrize(("prices", "viewer", "expected"), VISIBILITY_MATRIX)
async def test_products_and_prices_follow_the_visibility_rules(
    api: Api,
    seeder: Seeder,
    prices: PriceVisibility,
    viewer: str,
    expected: dict[str, int | None],
) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.ACTIVE, published=True, prices=prices)
    public = await seeder.product(store, title="public")
    members = await seeder.product(store, title="members", visibility=ProductVisibility.MEMBERS)
    await seeder.product(store, title="draft", status=ProductStatus.DRAFT)
    await seeder.variant(public, 500)
    await seeder.variant(members, 900)
    member = await seeder.user("member")
    await seeder.membership(store, member, MembershipLevel.CONNECTED, MembershipStatus.ACTIVE)
    actor = {"stranger": ANONYMOUS, "member": member, "owner": owner}[viewer]
    listing = await api.call("GET", f"/v1/stores/{store.id}/products", token=actor.token)
    assert {p["title"]: p["price_from_paise"] for p in listing.json()} == expected
    detail = await api.call("GET", f"/v1/stores/{store.id}/products/{public.id}", token=actor.token)
    assert detail.json()["variants"][0]["price_paise"] == expected["public"]


async def test_a_pending_members_request_does_not_reveal_anything(api: Api, seeder: Seeder) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(
        owner, StoreStatus.ACTIVE, published=True, prices=PriceVisibility.MEMBERS
    )
    hidden = await seeder.product(store, title="hidden", visibility=ProductVisibility.MEMBERS)
    await seeder.variant(hidden, 900)
    hopeful = await seeder.user("hopeful")
    await seeder.membership(store, hopeful, MembershipLevel.CONNECTED, MembershipStatus.PENDING)
    listing = await api.call("GET", f"/v1/stores/{store.id}/products", token=hopeful.token)
    assert listing.json() == []
    detail = await api.call(
        "GET", f"/v1/stores/{store.id}/products/{hidden.id}", token=hopeful.token
    )
    assert detail.status_code == 404


async def test_an_unlisted_store_shows_no_products(api: Api, seeder: Seeder) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.PENDING, published=True)
    product = await seeder.product(store, title="early")
    await seeder.variant(product, 500)
    assert (await api.call("GET", f"/v1/stores/{store.id}/products")).json() == []
    own = await api.call("GET", f"/v1/stores/{store.id}/products", token=owner.token)
    assert [p["title"] for p in own.json()] == ["early"]


async def test_hidden_prices_are_absent_from_raw_queries_too(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(
        owner, StoreStatus.ACTIVE, published=True, prices=PriceVisibility.MEMBERS
    )
    product = await seeder.product(store, title="secret price")
    await seeder.variant(product, 777_777)
    stranger = await seeder.user("stranger")
    async with app_engine.begin() as connection:
        await connection.execute(
            text(
                "SELECT set_config('app.role', 'buyer', true), set_config('app.user_id', :u, true)"
            ),
            {"u": str(stranger.user_id)},
        )
        variants = (
            await connection.execute(text("SELECT count(*) FROM catalog.variants"))
        ).scalar_one()
        prices = (
            await connection.execute(text("SELECT count(*) FROM catalog.variant_prices"))
        ).scalar_one()
    assert (variants, prices) == (1, 0)


async def test_storefront_search_tolerates_typos(api: Api, seeder: Seeder) -> None:
    _, store = await seller_with_store(seeder)
    store_id = str(store.id)
    for title in ("Banarasi silk saree", "Cotton kurta", "Pashmina shawl"):
        await seeder.product(store, title=title)
    exact = await api.call("GET", f"/v1/stores/{store_id}/products?q=silk saree")
    assert [p["title"] for p in exact.json()] == ["Banarasi silk saree"]
    typo = await api.call("GET", f"/v1/stores/{store_id}/products?q=pashmeena shawl")
    assert [p["title"] for p in typo.json()] == ["Pashmina shawl"]


async def test_a_misspelt_word_finds_long_titles_too(api: Api, seeder: Seeder) -> None:
    _, store = await seller_with_store(seeder)
    titles = ("Kanjeevaram cotton blend with zari border", "Kanjeevaram silk", "Cotton kurta")
    for title in titles:
        await seeder.product(store, title=title)
    typo = await api.call("GET", f"/v1/stores/{store.id}/products?q=kanjivaram")
    assert sorted(p["title"] for p in typo.json()) == sorted(titles[:2])
