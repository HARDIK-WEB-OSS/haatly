import re
import secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.routing import BaseRoute

from haatly.catalog.public import Product, ProductStatus, ProductVisibility, Variant
from haatly.discovery.public import PinCode
from haatly.membership.public import Membership, MembershipLevel, MembershipStatus
from haatly.stores.public import PriceVisibility, Store, StoreStatus
from tests.world import ANONYMOUS, PASSWORD, Actor, Api, IssuedInvite, Seeder, unique

FOREIGN_ACTORS = ("other_seller", "other_member", "plain_buyer", "anonymous")
CONTROL_ACTORS = (
    "victim_owner",
    "victim_member",
    "admin",
    "plain_buyer",
    "other_member",
    "other_seller",
    "anonymous",
)
PRICE_SCAN_ACTORS = ("victim_member", *CONTROL_ACTORS)


@dataclass(frozen=True)
class VictimSetup:
    status: StoreStatus
    published: bool
    prices: PriceVisibility = PriceVisibility.PUBLIC


VICTIM_STATES = {
    "live": VictimSetup(StoreStatus.ACTIVE, True),
    "pending": VictimSetup(StoreStatus.PENDING, False),
    "suspended": VictimSetup(StoreStatus.SUSPENDED, True),
}
PRICE_SCAN_STATES = {
    "live, members-only prices": VictimSetup(StoreStatus.ACTIVE, True, PriceVisibility.MEMBERS),
    "pending, members-only prices": VictimSetup(
        StoreStatus.PENDING, False, PriceVisibility.MEMBERS
    ),
    "suspended, members-only prices": VictimSetup(
        StoreStatus.SUSPENDED, True, PriceVisibility.MEMBERS
    ),
}
VICTIM_PIN = ("560001", 12.9716, 77.5946)
OTHER_PIN = ("560002", 12.9750, 77.6000)
PLACEHOLDER = re.compile(r"^\{(\w+)\}$")


class Mode(StrEnum):
    PROBE = "probe"
    CONTROL = "control"


class SuiteSetupError(AssertionError):
    pass


PATH_PARAM = re.compile(r"\{(\w+)(?::[^}]*)?\}")


@dataclass(frozen=True)
class RouteCase:
    method: str
    path: str
    route: BaseRoute

    @property
    def name(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def path_params(self) -> list[str]:
        return PATH_PARAM.findall(self.path)


def route_cases(app: FastAPI) -> list[RouteCase]:
    return [
        RouteCase(method, context.path or "", context.original_route)
        for context in iter_route_contexts(app.routes)
        for method in sorted(context.methods or ())
    ]


@dataclass(frozen=True)
class Listing:
    product: Product
    variant: Variant

    @property
    def secrets(self) -> set[str]:
        product, variant = self.product, self.variant
        return {
            str(product.id),
            product.title,
            product.description,
            str(variant.id),
            variant.label,
            str(variant.price_paise),
        }


@dataclass(frozen=True)
class Shop:
    public: Listing
    members_only: Listing
    draft: Listing
    pin: PinCode

    @property
    def prices(self) -> set[str]:
        listings = (self.public, self.members_only, self.draft)
        return {str(item.variant.price_paise) for item in listings}


@dataclass
class Tenant:
    store: Store
    owner: Actor
    member: Actor
    requester: Actor
    member_membership: Membership
    pending_membership: Membership
    invite: IssuedInvite
    shop: Shop


@dataclass
class World:
    state: str
    victim: Tenant
    foreign: Tenant
    plain_buyer: Actor
    admin: Actor

    @property
    def actors(self) -> dict[str, Actor]:
        return {
            "victim_owner": self.victim.owner,
            "victim_member": self.victim.member,
            "admin": self.admin,
            "plain_buyer": self.plain_buyer,
            "other_member": self.foreign.member,
            "other_seller": self.foreign.owner,
            "anonymous": ANONYMOUS,
        }

    @property
    def path_values(self) -> dict[str, str]:
        shop = self.victim.shop
        return {
            "store_id": str(self.victim.store.id),
            "membership_id": str(self.victim.pending_membership.id),
            "invitation_id": str(self.victim.invite.id),
            "product_id": str(shop.members_only.product.id),
            "variant_id": str(shop.members_only.variant.id),
            "pin_code": shop.pin.pin_code,
        }

    @property
    def query_values(self) -> dict[str, str]:
        return {
            "q": self.victim.shop.members_only.product.title,
            "pin_code": self.victim.shop.pin.pin_code,
            "radius_km": "50",
        }

    @property
    def price_sentinels(self) -> set[str]:
        return self.victim.shop.prices

    @property
    def secrets(self) -> set[str]:
        victim = self.victim
        people = (victim.owner, victim.member, victim.requester)
        found = {
            str(value)
            for person in people
            for value in (person.user_id, person.email, person.token)
        }
        found |= {
            str(victim.member_membership.id),
            str(victim.pending_membership.id),
            str(victim.invite.id),
            victim.invite.token,
            victim.invite.label,
        }
        found |= victim.shop.members_only.secrets | victim.shop.draft.secrets
        if self.state != "live":
            store = victim.store
            found |= {str(store.id), store.name, store.slug, store.description}
            found |= victim.shop.public.secrets
        return found


def sentinel_price() -> int:
    return secrets.randbelow(8_999_999_999) + 1_000_000_000


async def listing(
    seeder: Seeder, store: Store, visibility: ProductVisibility, status: ProductStatus
) -> Listing:
    product = await seeder.product(
        store, title=unique("secret"), visibility=visibility, status=status
    )
    return Listing(product, await seeder.variant(product, sentinel_price()))


async def build_shop(seeder: Seeder, store: Store, pin: tuple[str, float, float]) -> Shop:
    located = await seeder.pin(*pin)
    await seeder.locate(store, located)
    return Shop(
        await listing(seeder, store, ProductVisibility.PUBLIC, ProductStatus.ACTIVE),
        await listing(seeder, store, ProductVisibility.MEMBERS, ProductStatus.ACTIVE),
        await listing(seeder, store, ProductVisibility.PUBLIC, ProductStatus.DRAFT),
        located,
    )


async def build_tenant(
    seeder: Seeder, setup: VictimSetup, prefix: str, pin: tuple[str, float, float]
) -> Tenant:
    owner = await seeder.user(f"{prefix}-owner")
    member = await seeder.user(f"{prefix}-member")
    requester = await seeder.user(f"{prefix}-requester")
    store = await seeder.store(owner, setup.status, published=setup.published, prices=setup.prices)
    full = await seeder.membership(
        store, member, MembershipLevel.SELLER_ADDED, MembershipStatus.ACTIVE
    )
    pending = await seeder.membership(
        store, requester, MembershipLevel.CONNECTED, MembershipStatus.PENDING
    )
    invite = await seeder.invitation(store, owner)
    shop = await build_shop(seeder, store, pin)
    return Tenant(store, owner, member, requester, full, pending, invite, shop)


async def build_world(seeder: Seeder, state: str, setup: VictimSetup) -> World:
    victim = await build_tenant(seeder, setup, "victim", VICTIM_PIN)
    other = VictimSetup(StoreStatus.ACTIVE, True)
    foreign = await build_tenant(seeder, other, "other", OTHER_PIN)
    return World(
        state, victim, foreign, await seeder.user("buyer"), await seeder.user("admin", admin=True)
    )


def placeholder_value(name: str, actor: Actor, world: World, mode: Mode) -> Any:
    invite = world.victim.invite if mode is Mode.CONTROL else world.foreign.invite
    values = {
        "email": actor.email or f"{unique('stranger')}@example.test",
        "password": PASSWORD,
        "new_email": f"{unique('newcomer')}@example.test",
        "new_password": unique("fresh-password"),
        "new_name": unique("name"),
        "new_slug": unique("slug"),
        "invitation_token": invite.token,
        "pin_code": world.victim.shop.pin.pin_code,
    }
    if name not in values:
        raise SuiteSetupError(f"unknown example placeholder {{{name}}}")
    return values[name]


def resolve(value: Any, actor: Actor, world: World, mode: Mode) -> Any:
    if isinstance(value, dict):
        return {key: resolve(item, actor, world, mode) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, actor, world, mode) for item in value]
    match = PLACEHOLDER.match(value) if isinstance(value, str) else None
    return placeholder_value(match.group(1), actor, world, mode) if match else value


def body_for(case: RouteCase, actor: Actor, world: World, mode: Mode) -> Any:
    body_field = case.route.body_field if isinstance(case.route, APIRoute) else None
    if body_field is None:
        return None
    model = body_field.field_info.annotation
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise SuiteSetupError(f"{case.name}: request body is not a pydantic model")
    extra = model.model_config.get("json_schema_extra")
    examples = extra.get("examples") if isinstance(extra, dict) else None
    if not isinstance(examples, list) or not examples:
        raise SuiteSetupError(f"{case.name}: {model.__name__} declares no example body")
    body = resolve(examples[0], actor, world, mode)
    model.model_validate(body)
    return body


def path_for(case: RouteCase, world: World) -> str:
    values = world.path_values
    path = case.path
    for param in case.path_params:
        if param not in values:
            raise SuiteSetupError(f"{case.name}: no victim value for path parameter {param}")
        path = PATH_PARAM.sub(lambda found: values[found.group(1)], path, count=1)
    return path


def query_params_of(case: RouteCase) -> list[tuple[str, bool]]:
    if not isinstance(case.route, APIRoute):
        return []
    found: dict[str, bool] = {}
    pending = [case.route.dependant]
    while pending:
        current: Dependant = pending.pop()
        for param in current.query_params:
            found[param.name] = found.get(param.name, False) or param.field_info.is_required()
        pending.extend(current.dependencies)
    return sorted(found.items())


def query_for(case: RouteCase, world: World) -> dict[str, str]:
    values = world.query_values
    query = {}
    for name, required in query_params_of(case):
        if name in values:
            query[name] = values[name]
        elif required:
            raise SuiteSetupError(f"{case.name}: no victim value for query parameter {name}")
    return query


TENANT_QUERIES = {
    "store": "SELECT * FROM stores.stores WHERE id = :store",
    "memberships": "SELECT * FROM membership.memberships WHERE store_id = :store",
    "invitations": "SELECT * FROM membership.invitations WHERE store_id = :store",
    "products": "SELECT * FROM catalog.products WHERE store_id = :store",
    "variants": "SELECT * FROM catalog.variants WHERE store_id = :store",
    "variant_prices": "SELECT * FROM catalog.variant_prices WHERE store_id = :store",
    "store_locations": "SELECT * FROM discovery.store_locations WHERE store_id = :store",
    "users": "SELECT * FROM identity.users WHERE id = ANY(:people)",
    "sessions": "SELECT * FROM identity.sessions WHERE user_id = ANY(:people)",
}


@dataclass(frozen=True)
class Snapshot:
    rows: Mapping[str, Mapping[object, tuple[Any, ...]]]


async def snapshot(engine: AsyncEngine, world: World) -> Snapshot:
    victim = world.victim
    params = {
        "store": victim.store.id,
        "people": [victim.owner.user_id, victim.member.user_id, victim.requester.user_id],
    }
    rows: dict[str, dict[object, tuple[Any, ...]]] = {}
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        for name, sql in TENANT_QUERIES.items():
            result = await connection.execute(text(sql), params)
            rows[name] = {row[0]: tuple(row) for row in result}
    return Snapshot(rows)


def own_new_rows_only(actor: Actor, before: Snapshot, after: Snapshot) -> list[str]:
    problems = []
    for table, rows in after.rows.items():
        for key, row in rows.items():
            if key in before.rows[table]:
                continue
            owned = table == "memberships" and actor.user_id is not None and actor.user_id in row
            if not owned:
                problems.append(f"new {table} row {key}")
    return problems


def changes(actor: Actor, before: Snapshot, after: Snapshot) -> list[str]:
    problems = own_new_rows_only(actor, before, after)
    for table, rows in before.rows.items():
        for key, row in rows.items():
            if after.rows[table].get(key) != row:
                problems.append(f"changed {table} row {key}")
    return problems


@dataclass
class SuiteResult:
    cases: list[RouteCase]
    states: int
    probes: int = 0
    leaks: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    insider_saw_secret: bool = False

    def summary(self) -> str:
        return (
            f"{len(self.cases)} routes x {len(FOREIGN_ACTORS)} foreign actors x "
            f"{self.states} victim store states = {self.probes} cases, "
            f"{len(self.leaks)} leaks"
        )


def all_secrets(world: World) -> set[str]:
    return world.secrets


def price_sentinels(world: World) -> set[str]:
    return world.price_sentinels


@dataclass
class LeakSuite:
    api: Api
    seeder: Seeder
    engine: AsyncEngine
    states: Mapping[str, VictimSetup] = field(default_factory=lambda: VICTIM_STATES)
    secrets_of: Callable[[World], set[str]] = all_secrets
    control_actors: tuple[str, ...] = CONTROL_ACTORS

    async def send(self, case: RouteCase, actor: Actor, world: World, mode: Mode) -> Any:
        body = body_for(case, actor, world, mode)
        path = path_for(case, world)
        query = query_for(case, world)
        url = f"{path}?{urlencode(query)}" if query else path
        return await self.api.call(case.method, url, token=actor.token, json=body)

    async def probe(self, case: RouteCase, name: str, world: World) -> list[str]:
        actor = world.actors[name]
        before = await snapshot(self.engine, world)
        response = await self.send(case, actor, world, Mode.PROBE)
        after = await snapshot(self.engine, world)
        where = f"{case.name} as {name} on {world.state} store"
        found = [
            f"{where}: exposes {secret!r}"
            for secret in self.secrets_of(world)
            if secret in response.text
        ]
        found += [f"{where}: {problem}" for problem in changes(actor, before, after)]
        if response.status_code >= 500:
            found.append(f"{where}: server error {response.status_code}")
        return found

    async def control(self, case: RouteCase, world: World, result: SuiteResult) -> bool:
        for name in self.control_actors:
            response = await self.send(case, world.actors[name], world, Mode.CONTROL)
            if name.startswith("victim") and any(
                secret in response.text for secret in self.secrets_of(world)
            ):
                result.insider_saw_secret = True
            if response.is_success:
                return True
        return False

    async def run(self, cases: Iterable[RouteCase]) -> SuiteResult:
        result = SuiteResult(list(cases), len(self.states))
        for case in result.cases:
            covered = False
            for state, setup in self.states.items():
                world = await build_world(self.seeder, state, setup)
                for name in FOREIGN_ACTORS:
                    result.leaks += await self.probe(case, name, world)
                    result.probes += 1
                covered = await self.control(case, world, result) or covered
            if not covered:
                result.uncovered.append(case.name)
        return result
