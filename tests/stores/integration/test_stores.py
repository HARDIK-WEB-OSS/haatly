import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.identity.public import SESSION_COOKIE
from haatly.platform.public import ActorRole, TenantContext
from haatly.stores.public import SqlStoresUnitOfWorkFactory, StoreStatus
from tests.world import Actor, Api, Seeder, unique

pytestmark = pytest.mark.anyio


def new_store_body() -> dict[str, str]:
    return {"name": unique("Weaves"), "slug": unique("weaves")}


async def open_store(api: Api, actor: Actor) -> tuple[httpx.Response, str]:
    response = await api.call("POST", "/v1/stores", token=actor.token, json=new_store_body())
    assert response.status_code == 201, response.text
    return response, response.cookies[SESSION_COOKIE]


async def system_rows(engine: AsyncEngine, sql: str, **params: object) -> list[tuple[object, ...]]:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        return [tuple(row) for row in await connection.execute(text(sql), params)]


async def test_opening_a_store_makes_a_pending_seller_with_a_rotated_session(
    api: Api, seeder: Seeder
) -> None:
    buyer = await seeder.user("buyer")
    response, token = await open_store(api, buyer)
    store = response.json()
    assert (store["status"], store["is_published"]) == ("pending", False)
    assert token != buyer.token
    assert (await api.call("GET", "/v1/me", token=buyer.token)).status_code == 401
    assert (await api.call("GET", "/v1/seller/store", token=token)).json()["id"] == store["id"]
    again = await api.call("POST", "/v1/stores", token=token, json=new_store_body())
    assert again.status_code == 409


async def test_slugs_are_unique_and_validated(api: Api, seeder: Seeder) -> None:
    first, second = await seeder.user("first"), await seeder.user("second")
    body = new_store_body()
    assert (await api.call("POST", "/v1/stores", token=first.token, json=body)).status_code == 201
    taken = await api.call("POST", "/v1/stores", token=second.token, json=body | {"name": "Other"})
    assert taken.status_code == 409
    bad = await api.call(
        "POST", "/v1/stores", token=second.token, json={"name": "x", "slug": "No Spaces!"}
    )
    assert bad.status_code == 422
    assert (await api.call("POST", "/v1/stores", json=new_store_body())).status_code == 401


async def test_a_store_is_public_only_once_approved_and_published(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner, admin = await seeder.user("owner"), await seeder.user("admin", admin=True)
    response, token = await open_store(api, owner)
    store_id = response.json()["id"]
    path = f"/v1/stores/{store_id}"
    assert (await api.call("GET", path)).status_code == 404
    assert (await api.call("GET", path, token=token)).status_code == 200
    published = await api.call(
        "PATCH", "/v1/seller/store", token=token, json={"is_published": True}
    )
    assert published.json()["is_published"] is True
    assert (await api.call("GET", path)).status_code == 404
    approved = await api.call("POST", f"/v1/admin/stores/{store_id}/approve", token=admin.token)
    assert approved.json()["status"] == "active"
    assert (await api.call("GET", path)).json() == {
        "id": store_id,
        "name": response.json()["name"],
        "slug": response.json()["slug"],
        "description": "",
    }
    audit = await system_rows(
        app_engine,
        "SELECT action, actor_role, actor_user_id FROM platform.audit_log WHERE subject_id = :id",
        id=store_id,
    )
    assert audit == [("store.approve", "admin", admin.user_id)]


async def test_only_admins_moderate_and_each_transition_needs_the_right_state(
    api: Api, seeder: Seeder
) -> None:
    owner, admin = await seeder.user("owner"), await seeder.user("admin", admin=True)
    store = await seeder.store(owner, StoreStatus.PENDING, published=True)
    base = f"/v1/admin/stores/{store.id}"
    assert (await api.call("POST", f"{base}/approve", token=owner.token)).status_code == 403
    assert (await api.call("GET", "/v1/admin/stores", token=owner.token)).status_code == 403
    buyer = await seeder.user("buyer")
    assert (await api.call("POST", f"{base}/approve", token=buyer.token)).status_code == 403
    pending = await api.call("GET", "/v1/admin/stores", token=admin.token)
    assert str(store.id) in {item["id"] for item in pending.json()}
    assert (await api.call("POST", f"{base}/suspend", token=admin.token)).status_code == 404
    steps = ("approve", "suspend", "reinstate")
    statuses = [
        (await api.call("POST", f"{base}/{step}", token=admin.token)).json()["status"]
        for step in steps
    ]
    assert statuses == ["active", "suspended", "active"]
    listed = await api.call("GET", "/v1/admin/stores?status=active", token=admin.token)
    assert str(store.id) in {item["id"] for item in listed.json()}


async def test_a_seller_cannot_change_their_own_store_status(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.PENDING, published=False)
    seller = TenantContext(ActorRole.SELLER, owner.user_id, store.id)
    with pytest.raises(DBAPIError, match="only an admin can change a store status"):
        async with SqlStoresUnitOfWorkFactory(app_engine)(seller) as uow:
            await uow.stores.set_status(
                store.id, StoreStatus.PENDING, StoreStatus.ACTIVE, seeder.clock.now()
            )


async def test_the_audit_log_is_append_only(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner, admin = await seeder.user("owner"), await seeder.user("admin", admin=True)
    store = await seeder.store(owner, StoreStatus.PENDING, published=False)
    await api.call("POST", f"/v1/admin/stores/{store.id}/approve", token=admin.token)
    for statement in (
        "UPDATE platform.audit_log SET action = 'x'",
        "DELETE FROM platform.audit_log",
    ):
        with pytest.raises(DBAPIError):
            await system_rows(app_engine, statement + " RETURNING id")


async def test_store_text_is_cleaned_and_bounded(api: Api, seeder: Seeder) -> None:
    owner = await seeder.user("owner")
    _, token = await open_store(api, owner)
    patch = await api.call(
        "PATCH",
        "/v1/seller/store",
        token=token,
        json={"description": " hand\x00loom ", "name": "Looms"},
    )
    assert (patch.json()["description"], patch.json()["name"]) == ("handloom", "Looms")
    too_long = await api.call("PATCH", "/v1/seller/store", token=token, json={"name": "x" * 121})
    assert too_long.status_code == 422
    blank = await api.call("PATCH", "/v1/seller/store", token=token, json={"name": "\x00 "})
    assert blank.status_code == 422
