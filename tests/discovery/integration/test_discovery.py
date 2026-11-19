from dataclasses import dataclass
from typing import Any

import pytest

from haatly.catalog.public import ProductStatus, ProductVisibility
from haatly.discovery.public import PinCode, Point
from haatly.membership.public import MembershipLevel, MembershipStatus
from haatly.stores.public import PriceVisibility, Store, StoreStatus
from tests.world import Actor, Api, Seeder

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class Town:
    gpo: PinCode
    near: Store
    koramangala: Store
    whitefield: Store
    hidden: tuple[Store, ...]
    near_owner: Actor
    koramangala_owner: Actor


async def town(seeder: Seeder) -> Town:
    gpo = await seeder.pin("560001", 12.975, 77.585)
    koramangala_pin = await seeder.pin("560034", 12.9352, 77.6245)
    whitefield_pin = await seeder.pin("560066", 12.9698, 77.75)
    near_owner = await seeder.user("near")
    koramangala_owner = await seeder.user("koramangala")
    near = await seeder.store(near_owner, StoreStatus.ACTIVE, published=True)
    koramangala = await seeder.store(
        koramangala_owner, StoreStatus.ACTIVE, published=True, prices=PriceVisibility.MEMBERS
    )
    whitefield = await seeder.store(
        await seeder.user("whitefield"), StoreStatus.ACTIVE, published=True
    )
    await seeder.locate(near, gpo, Point(12.98, 77.59))
    await seeder.locate(koramangala, koramangala_pin)
    await seeder.locate(whitefield, whitefield_pin)
    hidden = []
    for status, published in (
        (StoreStatus.PENDING, False),
        (StoreStatus.SUSPENDED, True),
        (StoreStatus.ACTIVE, False),
    ):
        store = await seeder.store(await seeder.user("hidden"), status, published=published)
        await seeder.locate(store, gpo)
        hidden.append(store)
    return Town(gpo, near, koramangala, whitefield, tuple(hidden), near_owner, koramangala_owner)


def names(response: Any) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()]


async def test_stores_are_found_by_pin_in_distance_order(api: Api, seeder: Seeder) -> None:
    world = await town(seeder)
    nearby = await api.call("GET", "/discover/stores?pin_code=560001&radius_km=10")
    assert names(nearby) == [world.near.name, world.koramangala.name]
    first = nearby.json()[0]
    assert set(first) == {"id", "name", "slug", "distance_km"}
    assert first["distance_km"] == 0.8
    wider = await api.call("GET", "/discover/stores?pin_code=560001&radius_km=25")
    assert names(wider) == [world.near.name, world.koramangala.name, world.whitefield.name]


async def test_stores_are_found_from_a_point(api: Api, seeder: Seeder) -> None:
    world = await town(seeder)
    response = await api.call(
        "GET", "/discover/stores?latitude=12.9698&longitude=77.75&radius_km=1"
    )
    assert names(response) == [world.whitefield.name]
    assert response.json()[0]["distance_km"] == 0.0


async def test_stores_that_are_not_live_are_never_discovered(api: Api, seeder: Seeder) -> None:
    world = await town(seeder)
    buyer = await seeder.user("buyer")
    for token in (None, buyer.token, world.near_owner.token):
        found = await api.call("GET", "/discover/stores?pin_code=560001&radius_km=50", token=token)
        hidden_names = {store.name for store in world.hidden}
        assert hidden_names.isdisjoint(names(found))


@pytest.mark.parametrize(
    "query",
    [
        "",
        "latitude=12.97",
        "longitude=77.5",
        "pin_code=999999",
        "pin_code=05600",
        "latitude=51.5&longitude=-0.12",
        "pin_code=560001&radius_km=0",
        "pin_code=560001&radius_km=50.5",
    ],
)
async def test_bad_origins_are_refused(api: Api, seeder: Seeder, query: str) -> None:
    await town(seeder)
    response = await api.call("GET", f"/discover/stores?{query}")
    assert response.status_code == 422, response.text


async def catalogue(seeder: Seeder, world: Town) -> None:
    saree = await seeder.product(world.near, title="Kanjeevaram silk saree")
    await seeder.variant(saree, 1_250_000)
    await seeder.variant(saree, 1_100_000)
    secret = await seeder.product(
        world.near, title="Banarasi wedding weave", visibility=ProductVisibility.MEMBERS
    )
    await seeder.variant(secret, 2_000_000)
    draft = await seeder.product(world.near, title="Kanjeevaram draft", status=ProductStatus.DRAFT)
    await seeder.variant(draft, 900_000)
    blend = await seeder.product(world.koramangala, title="Kanjeevaram cotton blend")
    await seeder.variant(blend, 450_000)


def titles_and_prices(response: Any) -> list[tuple[str, int | None]]:
    assert response.status_code == 200, response.text
    return [(item["title"], item["price_from_paise"]) for item in response.json()]


async def test_products_are_searched_near_a_pin(api: Api, seeder: Seeder) -> None:
    world = await town(seeder)
    await catalogue(seeder, world)
    found = await api.call("GET", "/discover/products?q=kanjeevaram&pin_code=560001")
    assert titles_and_prices(found) == [
        ("Kanjeevaram silk saree", 1_100_000),
        ("Kanjeevaram cotton blend", None),
    ]
    assert set(found.json()[0]) == {
        "id",
        "store_id",
        "store_name",
        "title",
        "price_from_paise",
        "distance_km",
    }


async def test_a_misspelt_search_still_finds_the_product(api: Api, seeder: Seeder) -> None:
    world = await town(seeder)
    await catalogue(seeder, world)
    found = await api.call("GET", "/discover/products?q=kanjivaram&pin_code=560001")
    assert [title for title, _ in titles_and_prices(found)] == [
        "Kanjeevaram silk saree",
        "Kanjeevaram cotton blend",
    ]


async def test_members_see_members_only_products_and_hidden_prices(
    api: Api, seeder: Seeder
) -> None:
    world = await town(seeder)
    await catalogue(seeder, world)
    outsider = await seeder.user("outsider")
    hidden = await api.call(
        "GET", "/discover/products?q=banarasi&pin_code=560001", token=outsider.token
    )
    assert titles_and_prices(hidden) == []
    member = await seeder.user("member")
    for store in (world.near, world.koramangala):
        await seeder.membership(store, member, MembershipLevel.CONNECTED, MembershipStatus.ACTIVE)
    shown = await api.call(
        "GET", "/discover/products?q=banarasi&pin_code=560001", token=member.token
    )
    assert titles_and_prices(shown) == [("Banarasi wedding weave", 2_000_000)]
    priced = await api.call(
        "GET", "/discover/products?q=cotton&pin_code=560001", token=member.token
    )
    assert titles_and_prices(priced) == [("Kanjeevaram cotton blend", 450_000)]


async def test_a_pending_member_is_still_an_outsider(api: Api, seeder: Seeder) -> None:
    world = await town(seeder)
    await catalogue(seeder, world)
    pending = await seeder.user("pending")
    await seeder.membership(
        world.koramangala, pending, MembershipLevel.CONNECTED, MembershipStatus.PENDING
    )
    found = await api.call(
        "GET", "/discover/products?q=cotton&pin_code=560001", token=pending.token
    )
    assert titles_and_prices(found) == [("Kanjeevaram cotton blend", None)]


async def test_a_seller_places_their_store(api: Api, seeder: Seeder) -> None:
    world = await town(seeder)
    unplaced = await seeder.user("unplaced")
    await seeder.store(unplaced, StoreStatus.ACTIVE, published=True)
    missing = await api.call("GET", "/seller/store/location", token=unplaced.token)
    assert missing.status_code == 404
    owner = world.near_owner
    at_pin = await api.call(
        "PUT", "/seller/store/location", token=owner.token, json={"pin_code": "560034"}
    )
    assert at_pin.json() == {"pin_code": "560034", "latitude": 12.9352, "longitude": 77.6245}
    exact = await api.call(
        "PUT",
        "/seller/store/location",
        token=owner.token,
        json={"pin_code": "560034", "latitude": 12.94, "longitude": 77.62},
    )
    assert (exact.json()["latitude"], exact.json()["longitude"]) == (12.94, 77.62)
    stored = await api.call("GET", "/seller/store/location", token=owner.token)
    assert stored.json() == exact.json()
    for body in (
        {"pin_code": "999999"},
        {"pin_code": "560034", "latitude": 12.94},
        {"pin_code": "560034", "latitude": 51.5, "longitude": -0.12},
    ):
        refused = await api.call("PUT", "/seller/store/location", token=owner.token, json=body)
        assert refused.status_code == 422, body
    buyer = await seeder.user("buyer")
    denied = await api.call(
        "PUT", "/seller/store/location", token=buyer.token, json={"pin_code": "560034"}
    )
    assert denied.status_code in {403, 404}
