import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.catalog.public import (
    Product,
    ProductStatus,
    ProductVisibility,
    SqlCatalogUnitOfWorkFactory,
    Variant,
    VariantStatus,
)
from haatly.discovery.public import PinCode, Point, SqlDiscoveryUnitOfWorkFactory, StoreLocation
from haatly.identity.public import (
    SESSION_COOKIE,
    SecretTokens,
    SqlIdentityUnitOfWorkFactory,
    User,
    hash_token,
)
from haatly.identity.public import Session as UserSession
from haatly.membership.public import (
    Invitation,
    Membership,
    MembershipLevel,
    MembershipStatus,
    SqlMembershipUnitOfWorkFactory,
)
from haatly.platform.public import SYSTEM, Clock, SystemClock, Uuid7Source
from haatly.stores.public import (
    PriceVisibility,
    SqlStoresUnitOfWorkFactory,
    Store,
    StoreChanges,
    StoreStatus,
)

PASSWORD = "correct-horse-battery-staple"


@dataclass
class Api:
    client: httpx.AsyncClient
    origin: str
    app: FastAPI

    async def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.client.cookies.clear()
        sent = {"Origin": self.origin} | (headers or {})
        if token is not None:
            sent["Cookie"] = f"{SESSION_COOKIE}={token}"
        return await self.client.request(method, path, json=json, headers=sent)


@dataclass(frozen=True)
class Actor:
    name: str
    user_id: UUID | None = None
    email: str | None = None
    token: str | None = None


ANONYMOUS = Actor("anonymous")


def unique(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


@dataclass(frozen=True)
class IssuedInvite:
    id: UUID
    token: str
    label: str


@dataclass
class Seeder:
    engine: AsyncEngine
    password_hash: str
    clock: Clock = field(default_factory=SystemClock)

    def __post_init__(self) -> None:
        self.ids = Uuid7Source(self.clock)
        self.identity = SqlIdentityUnitOfWorkFactory(self.engine)
        self.stores = SqlStoresUnitOfWorkFactory(self.engine)
        self.members = SqlMembershipUnitOfWorkFactory(self.engine)
        self.catalog = SqlCatalogUnitOfWorkFactory(self.engine)
        self.discovery = SqlDiscoveryUnitOfWorkFactory(self.engine)

    async def user(self, name: str, *, admin: bool = False) -> Actor:
        now = self.clock.now()
        email = f"{unique(name)}@example.test"
        user = User(self.ids.new_id(), email, self.password_hash, admin, now)
        token = SecretTokens().new_token()
        async with self.identity(SYSTEM) as uow:
            await uow.users.add(user)
            await uow.sessions.add(
                UserSession(self.ids.new_id(), user.id, hash_token(token), now, now)
            )
        return Actor(name, user.id, email, token)

    async def store(
        self,
        owner: Actor,
        status: StoreStatus,
        *,
        published: bool,
        prices: PriceVisibility = PriceVisibility.PUBLIC,
    ) -> Store:
        assert owner.user_id is not None
        now = self.clock.now()
        slug = unique("shop")
        store = Store(
            self.ids.new_id(),
            owner.user_id,
            unique("Store name"),
            slug,
            unique("about"),
            StoreStatus.PENDING,
            False,
            PriceVisibility.PUBLIC,
            now,
        )
        async with self.stores(SYSTEM) as uow:
            await uow.stores.add(store)
            changes = StoreChanges(is_published=published, price_visibility=prices)
            await uow.stores.update(store.id, changes, now)
            changed = await uow.stores.set_status(store.id, StoreStatus.PENDING, status, now)
        assert changed is not None
        return changed

    async def membership(
        self, store: Store, user: Actor, level: MembershipLevel, status: MembershipStatus
    ) -> Membership:
        assert user.user_id is not None
        now = self.clock.now()
        membership = Membership(self.ids.new_id(), store.id, user.user_id, level, status, now)
        async with self.members(SYSTEM) as uow:
            created = await uow.memberships.request(membership, now)
        assert created is not None
        return created

    async def invitation(self, store: Store, creator: Actor) -> IssuedInvite:
        assert creator.user_id is not None
        now = self.clock.now()
        token = SecretTokens().new_token()
        label = unique("label")
        invitation = Invitation(
            self.ids.new_id(),
            store.id,
            creator.user_id,
            label,
            now + timedelta(days=7),
            None,
            None,
            None,
            now,
        )
        async with self.members(SYSTEM) as uow:
            await uow.invitations.add(invitation, hash_token(token))
        return IssuedInvite(invitation.id, token, label)

    async def pin(self, pin_code: str, latitude: float, longitude: float) -> PinCode:
        pin = PinCode(pin_code, "Bengaluru Urban", "Karnataka", Point(latitude, longitude))
        async with self.discovery(SYSTEM) as uow:
            await uow.pins.upsert([pin], "test-fixture")
        return pin

    async def locate(self, store: Store, pin: PinCode, point: Point | None = None) -> None:
        location = StoreLocation(store.id, pin.pin_code, point or pin.centroid)
        async with self.discovery(SYSTEM) as uow:
            await uow.locations.put(location, self.clock.now())

    async def product(
        self,
        store: Store,
        *,
        title: str | None = None,
        visibility: ProductVisibility = ProductVisibility.PUBLIC,
        status: ProductStatus = ProductStatus.ACTIVE,
    ) -> Product:
        product = Product(
            self.ids.new_id(),
            store.id,
            title or unique("product"),
            unique("details"),
            visibility,
            status,
            self.clock.now(),
        )
        async with self.catalog(SYSTEM) as uow:
            await uow.products.add(product)
        return product

    async def variant(self, product: Product, price_paise: int) -> Variant:
        variant = Variant(
            self.ids.new_id(),
            product.store_id,
            product.id,
            unique("size"),
            VariantStatus.ACTIVE,
            price_paise,
            self.clock.now(),
        )
        async with self.catalog(SYSTEM) as uow:
            await uow.variants.add(variant)
        return variant
