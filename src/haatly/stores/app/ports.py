from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from haatly.identity.kernel import SessionRepository
from haatly.platform.kernel import TenantContext, UnitOfWork
from haatly.stores.domain.model import Store, StoreChanges, StoreStatus


class SlugTakenError(Exception):
    pass


class AlreadyOwnsStoreError(Exception):
    pass


class StoreRepository(Protocol):
    async def add(self, store: Store) -> None: ...

    async def get(self, store_id: UUID) -> Store | None: ...

    async def owned_by(self, user_id: UUID) -> Store | None: ...

    async def update(
        self, store_id: UUID, changes: StoreChanges, now: datetime
    ) -> Store | None: ...

    async def set_status(
        self, store_id: UUID, expected: StoreStatus, status: StoreStatus, now: datetime
    ) -> Store | None: ...

    async def with_status(self, status: StoreStatus, limit: int) -> Sequence[Store]: ...


class StoresUnitOfWork(UnitOfWork, Protocol):
    @property
    def stores(self) -> StoreRepository: ...

    async def __aenter__(self) -> Self: ...


class StoresUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> StoresUnitOfWork: ...


class CallerUnitOfWork(StoresUnitOfWork, Protocol):
    @property
    def sessions(self) -> SessionRepository: ...


class CallerUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> CallerUnitOfWork: ...
