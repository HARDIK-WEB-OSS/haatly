from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.platform.adapters.tables import idempotency_keys
from haatly.platform.domain.idempotency import (
    IdempotencyState,
    NewRequest,
    StoredRequest,
    StoredResponse,
)
from haatly.platform.domain.tenancy import TenantContext

LOCK_SEPARATOR = "\x1f"


class SqlIdempotencyRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def lock(self, scope: str, key: str) -> None:
        name = LOCK_SEPARATOR.join(("idempotency", scope, key))
        lock_key = func.hashtextextended(name, 0)
        await self.connection.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def find(self, scope: str, key: str) -> StoredRequest | None:
        table = idempotency_keys
        statement = select(
            table.c.request_hash,
            table.c.state,
            table.c.lease_expires_at,
            table.c.response_status,
            table.c.response_body,
        ).where(table.c.scope == scope, table.c.key == key)
        row = (await self.connection.execute(statement)).first()
        if row is None:
            return None
        response = None
        if row.response_status is not None:
            response = StoredResponse(row.response_status, row.response_body or "")
        return StoredRequest(
            row.request_hash, IdempotencyState(row.state), row.lease_expires_at, response
        )

    async def start(self, owner: TenantContext, request: NewRequest) -> None:
        values = {
            "request_hash": request.request_hash,
            "state": IdempotencyState.IN_PROGRESS,
            "lease_expires_at": request.lease_expires_at,
            "response_status": None,
            "response_body": None,
            "completed_at": None,
        }
        statement = insert(idempotency_keys).values(
            scope=request.scope,
            key=request.key,
            created_at=request.now,
            actor_role=owner.role.value,
            user_id=owner.user_id,
            store_id=owner.store_id,
            **values,
        )
        await self.connection.execute(
            statement.on_conflict_do_update(
                index_elements=[idempotency_keys.c.scope, idempotency_keys.c.key], set_=values
            )
        )

    async def complete(self, scope: str, key: str, response: StoredResponse, now: datetime) -> None:
        table = idempotency_keys
        await self.connection.execute(
            update(table)
            .where(table.c.scope == scope, table.c.key == key)
            .values(
                state=IdempotencyState.COMPLETED,
                response_status=response.status,
                response_body=response.body,
                completed_at=now,
            )
        )

    async def abandon(self, scope: str, key: str) -> None:
        table = idempotency_keys
        await self.connection.execute(
            delete(table).where(
                table.c.scope == scope,
                table.c.key == key,
                table.c.state == IdempotencyState.IN_PROGRESS,
            )
        )
