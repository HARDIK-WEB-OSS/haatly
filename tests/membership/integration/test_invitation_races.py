import asyncio
from collections import Counter
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.identity.public import hash_token
from haatly.membership.public import (
    Membership,
    MembershipLevel,
    MembershipStatus,
    SqlMembershipUnitOfWorkFactory,
)
from haatly.platform.public import ActorRole, TenantContext
from haatly.stores.public import StoreStatus
from tests.world import Actor, Api, Seeder

pytestmark = pytest.mark.anyio

ROUNDS = 25


async def accept(api: Api, actor: Actor, token: str) -> int:
    response = await api.call(
        "POST", "/invitations/accept", token=actor.token, json={"token": token}
    )
    return response.status_code


async def system_count(engine: AsyncEngine, sql: str, **params: object) -> int:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        return int((await connection.execute(text(sql), params)).scalar_one())


async def test_two_users_racing_for_one_invitation_get_exactly_one_acceptance(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    outcomes: Counter[int] = Counter()
    for _ in range(ROUNDS):
        owner = await seeder.user("owner")
        store = await seeder.store(owner, StoreStatus.ACTIVE, published=True)
        invite = await seeder.invitation(store, owner)
        first, second = await seeder.user("first"), await seeder.user("second")
        statuses = await asyncio.gather(
            accept(api, first, invite.token), accept(api, second, invite.token)
        )
        outcomes.update(statuses)
        assert sorted(statuses) == [200, 404]
        members = await system_count(
            app_engine,
            "SELECT count(*) FROM membership.memberships WHERE store_id = :store",
            store=store.id,
        )
        assert members == 1
    print(f"\nrace (a) same invite, two users x {ROUNDS}: {dict(outcomes)}")
    assert outcomes == Counter({200: ROUNDS, 404: ROUNDS})


async def test_one_user_accepting_two_invitations_at_once_gets_one_membership(
    api: Api, seeder: Seeder, app_engine: AsyncEngine
) -> None:
    outcomes: Counter[int] = Counter()
    for _ in range(ROUNDS):
        owner = await seeder.user("owner")
        store = await seeder.store(owner, StoreStatus.ACTIVE, published=True)
        first, second = await seeder.invitation(store, owner), await seeder.invitation(store, owner)
        buyer = await seeder.user("buyer")
        statuses = await asyncio.gather(
            accept(api, buyer, first.token), accept(api, buyer, second.token)
        )
        outcomes.update(statuses)
        assert sorted(statuses) == [200, 409]
        members = await system_count(
            app_engine,
            "SELECT count(*) FROM membership.memberships "
            "WHERE store_id = :store AND user_id = :user",
            store=store.id,
            user=buyer.user_id,
        )
        used = await system_count(
            app_engine,
            "SELECT count(*) FROM membership.invitations "
            "WHERE store_id = :store AND accepted_at IS NOT NULL",
            store=store.id,
        )
        assert (members, used) == (1, 1)
    print(f"\nrace (b) two invites, one user x {ROUNDS}: {dict(outcomes)}")
    assert outcomes == Counter({200: ROUNDS, 409: ROUNDS})


def buyer_context(actor: Actor) -> TenantContext:
    return TenantContext(ActorRole.BUYER, actor.user_id)


async def test_a_second_claim_waits_for_the_first_and_then_finds_nothing(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.ACTIVE, published=True)
    invite = await seeder.invitation(store, owner)
    first, second = await seeder.user("first"), await seeder.user("second")
    factory = SqlMembershipUnitOfWorkFactory(app_engine)
    now = seeder.clock.now()

    async def claim(actor: Actor) -> bool:
        async with factory(buyer_context(actor)) as uow:
            found = await uow.invitations.present(hash_token(invite.token))
            assert found is not None
            assert actor.user_id is not None
            return await uow.invitations.claim(found.id, actor.user_id, now)

    async with factory(buyer_context(first)) as holder:
        found = await holder.invitations.present(hash_token(invite.token))
        assert found is not None
        assert first.user_id is not None
        assert await holder.invitations.claim(found.id, first.user_id, now)
        waiting = asyncio.create_task(claim(second))
        await asyncio.sleep(0.3)
        assert not waiting.done()
    assert await waiting is False


def invited_member(seeder: Seeder, store_id: UUID, buyer: Actor) -> Membership:
    assert buyer.user_id is not None
    return Membership(
        seeder.ids.new_id(),
        store_id,
        buyer.user_id,
        MembershipLevel.SELLER_ADDED,
        MembershipStatus.ACTIVE,
        seeder.clock.now(),
    )


async def test_a_second_invited_join_waits_and_then_refuses_to_duplicate(
    seeder: Seeder, app_engine: AsyncEngine
) -> None:
    owner = await seeder.user("owner")
    store = await seeder.store(owner, StoreStatus.ACTIVE, published=True)
    invites = (await seeder.invitation(store, owner), await seeder.invitation(store, owner))
    buyer = await seeder.user("buyer")
    assert buyer.user_id is not None
    assert owner.user_id is not None
    factory = SqlMembershipUnitOfWorkFactory(app_engine)
    now = seeder.clock.now()

    def joining() -> Membership:
        return invited_member(seeder, store.id, buyer)

    async def join(token: str) -> Membership | None:
        async with factory(buyer_context(buyer)) as uow:
            found = await uow.invitations.present(hash_token(token))
            assert found is not None
            assert buyer.user_id is not None
            assert await uow.invitations.claim(found.id, buyer.user_id, now)
            assert owner.user_id is not None
            return await uow.memberships.join_by_invitation(joining(), owner.user_id, now)

    async with factory(buyer_context(buyer)) as holder:
        found = await holder.invitations.present(hash_token(invites[0].token))
        assert found is not None
        assert await holder.invitations.claim(found.id, buyer.user_id, now)
        assert await holder.memberships.join_by_invitation(joining(), owner.user_id, now)
        waiting = asyncio.create_task(join(invites[1].token))
        await asyncio.sleep(0.3)
        assert not waiting.done()
    assert await waiting is None
