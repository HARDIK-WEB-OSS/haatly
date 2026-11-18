import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.routing import BaseRoute

from haatly.membership.public import Membership, MembershipLevel, MembershipStatus
from haatly.stores.public import Store, StoreStatus
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
VICTIM_STATES = {
    "live": (StoreStatus.ACTIVE, True),
    "pending": (StoreStatus.PENDING, False),
    "suspended": (StoreStatus.SUSPENDED, True),
}
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


@dataclass
class Tenant:
    store: Store
    owner: Actor
    member: Actor
    requester: Actor
    member_membership: Membership
    pending_membership: Membership
    invite: IssuedInvite


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
        return {
            "store_id": str(self.victim.store.id),
            "membership_id": str(self.victim.pending_membership.id),
            "invitation_id": str(self.victim.invite.id),
        }

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
        if self.state != "live":
            store = victim.store
            found |= {str(store.id), store.name, store.slug, store.description}
        return found


async def build_tenant(seeder: Seeder, status: StoreStatus, published: bool, prefix: str) -> Tenant:
    owner = await seeder.user(f"{prefix}-owner")
    member = await seeder.user(f"{prefix}-member")
    requester = await seeder.user(f"{prefix}-requester")
    store = await seeder.store(owner, status, published=published)
    full = await seeder.membership(
        store, member, MembershipLevel.SELLER_ADDED, MembershipStatus.ACTIVE
    )
    pending = await seeder.membership(
        store, requester, MembershipLevel.CONNECTED, MembershipStatus.PENDING
    )
    invite = await seeder.invitation(store, owner)
    return Tenant(store, owner, member, requester, full, pending, invite)


async def build_world(seeder: Seeder, state: str) -> World:
    status, published = VICTIM_STATES[state]
    victim = await build_tenant(seeder, status, published, "victim")
    foreign = await build_tenant(seeder, StoreStatus.ACTIVE, True, "other")
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


TENANT_QUERIES = {
    "store": "SELECT * FROM stores.stores WHERE id = :store",
    "memberships": "SELECT * FROM membership.memberships WHERE store_id = :store",
    "invitations": "SELECT * FROM membership.invitations WHERE store_id = :store",
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
            rows[name] = {row.id: tuple(row) for row in result}
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
    probes: int = 0
    leaks: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{len(self.cases)} routes x {len(FOREIGN_ACTORS)} foreign actors x "
            f"{len(VICTIM_STATES)} victim store states = {self.probes} cases, "
            f"{len(self.leaks)} leaks"
        )


@dataclass
class LeakSuite:
    api: Api
    seeder: Seeder
    engine: AsyncEngine

    async def send(self, case: RouteCase, actor: Actor, world: World, mode: Mode) -> Any:
        body = body_for(case, actor, world, mode)
        return await self.api.call(case.method, path_for(case, world), token=actor.token, json=body)

    async def probe(self, case: RouteCase, name: str, world: World) -> list[str]:
        actor = world.actors[name]
        before = await snapshot(self.engine, world)
        response = await self.send(case, actor, world, Mode.PROBE)
        after = await snapshot(self.engine, world)
        where = f"{case.name} as {name} on {world.state} store"
        found = [
            f"{where}: exposes {secret!r}" for secret in world.secrets if secret in response.text
        ]
        found += [f"{where}: {problem}" for problem in changes(actor, before, after)]
        if response.status_code >= 500:
            found.append(f"{where}: server error {response.status_code}")
        return found

    async def control(self, case: RouteCase, world: World) -> bool:
        for name in CONTROL_ACTORS:
            response = await self.send(case, world.actors[name], world, Mode.CONTROL)
            if response.is_success:
                return True
        return False

    async def run(self, cases: Iterable[RouteCase]) -> SuiteResult:
        result = SuiteResult(list(cases))
        for case in result.cases:
            covered = False
            for state in VICTIM_STATES:
                world = await build_world(self.seeder, state)
                for name in FOREIGN_ACTORS:
                    result.leaks += await self.probe(case, name, world)
                    result.probes += 1
                covered = await self.control(case, world) or covered
            if not covered:
                result.uncovered.append(case.name)
        return result
