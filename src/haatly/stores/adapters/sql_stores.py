from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Row, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.platform.public import is_unique_violation
from haatly.stores.adapters.tables import stores
from haatly.stores.app.ports import AlreadyOwnsStoreError, SlugTakenError
from haatly.stores.domain.model import PriceVisibility, Store, StoreChanges, StoreStatus

COLUMNS = (
    stores.c.id,
    stores.c.owner_user_id,
    stores.c.name,
    stores.c.slug,
    stores.c.description,
    stores.c.status,
    stores.c.is_published,
    stores.c.price_visibility,
    stores.c.created_at,
)


def store_from_row(row: Row[tuple[object, ...]]) -> Store:
    return Store(
        id=row.id,
        owner_user_id=row.owner_user_id,
        name=row.name,
        slug=row.slug,
        description=row.description,
        status=StoreStatus(row.status),
        is_published=row.is_published,
        price_visibility=PriceVisibility(row.price_visibility),
        created_at=row.created_at,
    )


class SqlStoreRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def add(self, store: Store) -> None:
        statement = insert(stores).values(
            id=store.id,
            owner_user_id=store.owner_user_id,
            name=store.name,
            slug=store.slug,
            description=store.description,
            status=store.status.value,
            is_published=store.is_published,
            price_visibility=store.price_visibility.value,
            created_at=store.created_at,
            updated_at=store.created_at,
        )
        try:
            async with self.connection.begin_nested():
                await self.connection.execute(statement)
        except IntegrityError as error:
            if is_unique_violation(error, "uq_stores_slug"):
                raise SlugTakenError from error
            if is_unique_violation(error, "uq_stores_owner_user_id"):
                raise AlreadyOwnsStoreError from error
            raise

    async def one(self, *conditions: ColumnElement[bool]) -> Store | None:
        row = (await self.connection.execute(select(*COLUMNS).where(*conditions))).first()
        return None if row is None else store_from_row(row)

    async def get(self, store_id: UUID) -> Store | None:
        return await self.one(stores.c.id == store_id)

    async def owned_by(self, user_id: UUID) -> Store | None:
        return await self.one(stores.c.owner_user_id == user_id)

    async def change(self, *conditions: ColumnElement[bool], **values: object) -> Store | None:
        statement = update(stores).where(*conditions).values(**values).returning(*COLUMNS)
        row = (await self.connection.execute(statement)).first()
        return None if row is None else store_from_row(row)

    async def update(self, store_id: UUID, changes: StoreChanges, now: datetime) -> Store | None:
        return await self.change(stores.c.id == store_id, updated_at=now, **changes.values())

    async def set_status(
        self, store_id: UUID, expected: StoreStatus, status: StoreStatus, now: datetime
    ) -> Store | None:
        return await self.change(
            stores.c.id == store_id,
            stores.c.status == expected.value,
            status=status.value,
            updated_at=now,
        )

    async def with_status(self, status: StoreStatus, limit: int) -> Sequence[Store]:
        statement = (
            select(*COLUMNS)
            .where(stores.c.status == status.value)
            .order_by(stores.c.created_at, stores.c.id)
            .limit(limit)
        )
        return [store_from_row(row) for row in await self.connection.execute(statement)]
