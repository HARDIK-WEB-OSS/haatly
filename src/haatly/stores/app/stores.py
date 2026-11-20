from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from haatly.platform.kernel import (
    ActorRole,
    AuditAction,
    Clock,
    IdSource,
    TenantContext,
    record_audit,
)
from haatly.stores.app.ports import StoresUnitOfWorkFactory
from haatly.stores.domain.model import (
    PriceVisibility,
    Store,
    StoreChanges,
    StoreStatus,
    check_slug,
)

ADMIN_TRANSITIONS = {
    "approve": (StoreStatus.PENDING, StoreStatus.ACTIVE),
    "suspend": (StoreStatus.ACTIVE, StoreStatus.SUSPENDED),
    "reinstate": (StoreStatus.SUSPENDED, StoreStatus.ACTIVE),
}


class StoreNotFoundError(LookupError):
    pass


class NotAllowedError(PermissionError):
    pass


@dataclass
class Stores:
    uow_factory: StoresUnitOfWorkFactory
    ids: IdSource
    clock: Clock

    async def create(self, context: TenantContext, name: str, raw_slug: str) -> Store:
        if context.role is not ActorRole.BUYER or context.user_id is None:
            raise NotAllowedError
        store = Store(
            id=self.ids.new_id(),
            owner_user_id=context.user_id,
            name=name,
            slug=check_slug(raw_slug),
            description="",
            status=StoreStatus.PENDING,
            is_published=False,
            price_visibility=PriceVisibility.PUBLIC,
            created_at=self.clock.now(),
        )
        async with self.uow_factory(context) as uow:
            await uow.stores.add(store)
        return store

    async def visible(self, context: TenantContext, store_id: UUID) -> Store:
        async with self.uow_factory(context) as uow:
            store = await uow.stores.get(store_id)
        if store is None or not (
            store.is_live or store.id == context.store_id or context.role is ActorRole.ADMIN
        ):
            raise StoreNotFoundError
        return store

    async def own(self, context: TenantContext) -> Store:
        if context.role is not ActorRole.SELLER or context.store_id is None:
            raise StoreNotFoundError
        async with self.uow_factory(context) as uow:
            store = await uow.stores.get(context.store_id)
        if store is None:
            raise StoreNotFoundError
        return store

    async def update_own(self, context: TenantContext, changes: StoreChanges) -> Store:
        own = await self.own(context)
        async with self.uow_factory(context) as uow:
            updated = await uow.stores.update(own.id, changes, self.clock.now())
        if updated is None:
            raise StoreNotFoundError
        return updated

    async def by_status(self, context: TenantContext, status: StoreStatus) -> Sequence[Store]:
        if context.role is not ActorRole.ADMIN:
            raise NotAllowedError
        async with self.uow_factory(context) as uow:
            return await uow.stores.with_status(status, 200)

    async def moderate(self, context: TenantContext, store_id: UUID, action: str) -> Store:
        if context.role is not ActorRole.ADMIN:
            raise NotAllowedError
        expected, status = ADMIN_TRANSITIONS[action]
        async with self.uow_factory(context) as uow:
            store = await uow.stores.set_status(store_id, expected, status, self.clock.now())
            if store is None:
                raise StoreNotFoundError
            what = AuditAction(f"store.{action}", "store", store_id, {"status": status.value})
            await record_audit(uow, self.ids, self.clock, what)
        return store
