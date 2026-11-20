from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.identity.public import hash_token
from haatly.membership.public import (
    Invitation,
    MembershipLevel,
    MembershipStatus,
    SqlMembershipUnitOfWorkFactory,
)
from haatly.platform.public import SYSTEM, ActorRole, TenantContext
from haatly.stores.public import StoreStatus
from tests.world import Actor, Api, Seeder

pytestmark = pytest.mark.anyio


async def live_store_with_owner(seeder: Seeder) -> tuple[Actor, str]:
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.ACTIVE, published=True)
    return owner, str(store.id)


async def test_a_buyer_requests_to_join_and_the_seller_decides(api: Api, seeder: Seeder) -> None:
    owner, store_id = await live_store_with_owner(seeder)
    buyer = await seeder.user("buyer")
    joined = await api.call("POST", f"/v1/stores/{store_id}/join", token=buyer.token)
    assert (joined.status_code, joined.json()["status"]) == (201, "pending")
    again = await api.call("POST", f"/v1/stores/{store_id}/join", token=buyer.token)
    assert again.json()["id"] == joined.json()["id"]
    members = await api.call("GET", "/v1/seller/members", token=owner.token)
    assert [(m["email"], m["status"]) for m in members.json()] == [(buyer.email, "pending")]
    membership_id = joined.json()["id"]
    approved = await api.call(
        "POST", f"/v1/seller/members/{membership_id}/approve", token=owner.token
    )
    assert approved.json()["status"] == "active"
    twice = await api.call("POST", f"/v1/seller/members/{membership_id}/approve", token=owner.token)
    assert twice.status_code == 404
    mine = await api.call("GET", "/v1/me/memberships", token=buyer.token)
    assert [(m["store_id"], m["status"]) for m in mine.json()] == [(store_id, "active")]


async def test_a_blocked_buyer_cannot_rejoin_or_use_an_invitation(api: Api, seeder: Seeder) -> None:
    owner, store_id = await live_store_with_owner(seeder)
    buyer = await seeder.user("buyer")
    joined = await api.call("POST", f"/v1/stores/{store_id}/join", token=buyer.token)
    blocked = await api.call(
        "POST", f"/v1/seller/members/{joined.json()['id']}/block", token=owner.token
    )
    assert blocked.json()["status"] == "blocked"
    assert (
        await api.call("POST", f"/v1/stores/{store_id}/join", token=buyer.token)
    ).status_code == 403
    invite = await api.call(
        "POST", "/v1/seller/invitations", token=owner.token, json={"label": "again"}
    )
    accepted = await api.call(
        "POST", "/v1/invitations/accept", token=buyer.token, json={"token": invite.json()["token"]}
    )
    assert accepted.status_code == 403
    listed = await api.call("GET", "/v1/seller/invitations", token=owner.token)
    assert [item["accepted"] for item in listed.json()] == [False]


async def test_join_refusals(api: Api, seeder: Seeder) -> None:
    owner, store_id = await live_store_with_owner(seeder)
    assert (
        await api.call("POST", f"/v1/stores/{store_id}/join", token=owner.token)
    ).status_code == 403
    hidden_owner = await seeder.user("hidden")
    hidden = await seeder.store(hidden_owner, StoreStatus.PENDING, published=False)
    buyer = await seeder.user("buyer")
    assert (
        await api.call("POST", f"/v1/stores/{hidden.id}/join", token=buyer.token)
    ).status_code == 404
    assert (await api.call("POST", f"/v1/stores/{store_id}/join")).status_code == 401
    assert (
        await api.call("POST", f"/v1/seller/members/{store_id}/block", token=owner.token)
    ).status_code == 404


async def test_an_invitation_upgrades_a_pending_member_and_is_single_use(
    api: Api, seeder: Seeder
) -> None:
    owner, store_id = await live_store_with_owner(seeder)
    buyer, latecomer = await seeder.user("buyer"), await seeder.user("late")
    await api.call("POST", f"/v1/stores/{store_id}/join", token=buyer.token)
    issued = await api.call(
        "POST",
        "/v1/seller/invitations",
        token=owner.token,
        json={"label": "Priya", "expires_in_days": 3},
    )
    assert issued.status_code == 201
    token = issued.json()["token"]
    accepted = await api.call(
        "POST", "/v1/invitations/accept", token=buyer.token, json={"token": token}
    )
    assert (accepted.json()["level"], accepted.json()["status"]) == ("seller_added", "active")
    reused = await api.call(
        "POST", "/v1/invitations/accept", token=latecomer.token, json={"token": token}
    )
    assert reused.status_code == 404
    second = await api.call(
        "POST", "/v1/seller/invitations", token=owner.token, json={"label": "dup"}
    )
    duplicate = await api.call(
        "POST", "/v1/invitations/accept", token=buyer.token, json={"token": second.json()["token"]}
    )
    assert duplicate.status_code == 409
    own = await api.call(
        "POST", "/v1/invitations/accept", token=owner.token, json={"token": second.json()["token"]}
    )
    assert own.status_code == 403


async def test_revoked_and_expired_invitations_cannot_be_used(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner, _ = await live_store_with_owner(seeder)
    buyer = await seeder.user("buyer")
    issued = await api.call(
        "POST", "/v1/seller/invitations", token=owner.token, json={"label": "x"}
    )
    revoke_path = f"/v1/seller/invitations/{issued.json()['id']}/revoke"
    assert (await api.call("POST", revoke_path, token=owner.token)).json()["revoked"] is True
    assert (await api.call("POST", revoke_path, token=owner.token)).status_code == 404
    body = {"token": issued.json()["token"]}
    assert (
        await api.call("POST", "/v1/invitations/accept", token=buyer.token, json=body)
    ).status_code == 404
    store = await seeder.store(await seeder.user("other"), StoreStatus.ACTIVE, published=True)
    now = seeder.clock.now()
    expired = Invitation(
        seeder.ids.new_id(),
        store.id,
        store.owner_user_id,
        "old",
        now - timedelta(days=1),
        None,
        None,
        None,
        now - timedelta(days=8),
    )
    async with SqlMembershipUnitOfWorkFactory(app_engine)(SYSTEM) as uow:
        await uow.invitations.add(expired, hash_token("expired-token"))
    stale = await api.call(
        "POST", "/v1/invitations/accept", token=buyer.token, json={"token": "expired-token"}
    )
    assert stale.status_code == 404
    unknown = await api.call(
        "POST", "/v1/invitations/accept", token=buyer.token, json={"token": "nope"}
    )
    assert unknown.status_code == 404


async def test_an_approved_but_unpublished_store_accepts_invitations_only(
    api: Api, seeder: Seeder
) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.ACTIVE, published=False)
    pending_owner = await seeder.user("pending")
    pending = await seeder.store(pending_owner, StoreStatus.PENDING, published=False)
    buyer = await seeder.user("buyer")
    assert (
        await api.call("POST", f"/v1/stores/{store.id}/join", token=buyer.token)
    ).status_code == 404
    invite = await seeder.invitation(store, owner)
    accepted = await api.call(
        "POST", "/v1/invitations/accept", token=buyer.token, json={"token": invite.token}
    )
    assert accepted.status_code == 200
    early = await seeder.invitation(pending, pending_owner)
    refused = await api.call(
        "POST", "/v1/invitations/accept", token=buyer.token, json={"token": early.token}
    )
    assert refused.status_code == 404


async def test_rls_hides_other_stores_memberships_even_from_direct_queries(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner_a, owner_b = await seeder.user("a"), await seeder.user("b")
    store_a = await seeder.store(owner_a, StoreStatus.ACTIVE, published=True)
    store_b = await seeder.store(owner_b, StoreStatus.ACTIVE, published=True)
    buyer = await seeder.user("buyer")
    await seeder.membership(store_b, buyer, MembershipLevel.CONNECTED, MembershipStatus.PENDING)
    await seeder.invitation(store_b, owner_b)
    factory = SqlMembershipUnitOfWorkFactory(app_engine)
    seller_a = TenantContext(ActorRole.SELLER, owner_a.user_id, store_a.id)
    async with factory(seller_a) as uow:
        assert await uow.memberships.for_store(store_b.id) == []
        assert await uow.invitations.for_store(store_b.id) == []
    stranger = await seeder.user("stranger")
    async with factory(TenantContext(ActorRole.BUYER, stranger.user_id)) as uow:
        assert buyer.user_id is not None
        assert await uow.memberships.for_user(buyer.user_id) == []


async def test_join_requests_are_limited_to_twenty_an_hour_per_user(
    api: Api, seeder: Seeder
) -> None:
    _, store_id = await live_store_with_owner(seeder)
    buyer = await seeder.user("eager")
    path = f"/v1/stores/{store_id}/join"
    statuses = [(await api.call("POST", path, token=buyer.token)).status_code for _ in range(20)]
    assert statuses == [201] * 20
    limited = await api.call("POST", path, token=buyer.token)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    other = await seeder.user("patient")
    assert (await api.call("POST", path, token=other.token)).status_code == 201


async def test_invitations_are_limited_to_fifty_a_day_per_store(api: Api, seeder: Seeder) -> None:
    owner, _ = await live_store_with_owner(seeder)
    body = {"label": "batch"}
    for _ in range(50):
        issued = await api.call("POST", "/v1/seller/invitations", token=owner.token, json=body)
        assert issued.status_code == 201
    limited = await api.call("POST", "/v1/seller/invitations", token=owner.token, json=body)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) <= 86400
    listed = await api.call("GET", "/v1/seller/invitations", token=owner.token)
    assert len(listed.json()) == 50
    other_owner, _ = await live_store_with_owner(seeder)
    other = await api.call("POST", "/v1/seller/invitations", token=other_owner.token, json=body)
    assert other.status_code == 201
