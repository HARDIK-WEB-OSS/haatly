from dataclasses import dataclass
from types import TracebackType
from typing import Self

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from haatly.platform.adapters.sql_audit import SqlAuditRepository, SqlRateLimitRepository
from haatly.platform.adapters.sql_idempotency import SqlIdempotencyRepository
from haatly.platform.adapters.sql_jobs import SqlJobRepository
from haatly.platform.adapters.sql_outbox import SqlOutboxRepository, SqlProcessedEventRepository
from haatly.platform.domain.tenancy import TenantContext


class UnitOfWorkNotStartedError(RuntimeError):
    pass


async def apply_tenant_context(connection: AsyncConnection, context: TenantContext) -> None:
    settings = [func.set_config(name, value, True) for name, value in context.settings().items()]
    await connection.execute(select(*settings))


@dataclass(frozen=True)
class OpenTransaction:
    connection: AsyncConnection
    transaction: AsyncTransaction
    jobs: SqlJobRepository
    outbox: SqlOutboxRepository
    processed: SqlProcessedEventRepository
    idempotency: SqlIdempotencyRepository


async def open_transaction(engine: AsyncEngine, context: TenantContext) -> OpenTransaction:
    connection = await engine.connect()
    try:
        transaction = await connection.begin()
        await apply_tenant_context(connection, context)
    except BaseException:
        await connection.close()
        raise
    return OpenTransaction(
        connection,
        transaction,
        SqlJobRepository(connection),
        SqlOutboxRepository(connection),
        SqlProcessedEventRepository(connection),
        SqlIdempotencyRepository(connection),
    )


class SqlUnitOfWork:
    def __init__(self, engine: AsyncEngine, context: TenantContext) -> None:
        self.engine = engine
        self.context = context
        self._open: OpenTransaction | None = None

    @property
    def current(self) -> OpenTransaction:
        if self._open is None:
            raise UnitOfWorkNotStartedError
        return self._open

    @property
    def connection(self) -> AsyncConnection:
        return self.current.connection

    @property
    def jobs(self) -> SqlJobRepository:
        return self.current.jobs

    @property
    def outbox(self) -> SqlOutboxRepository:
        return self.current.outbox

    @property
    def processed(self) -> SqlProcessedEventRepository:
        return self.current.processed

    @property
    def idempotency(self) -> SqlIdempotencyRepository:
        return self.current.idempotency

    @property
    def audit(self) -> SqlAuditRepository:
        return SqlAuditRepository(self.connection)

    @property
    def rate_limits(self) -> SqlRateLimitRepository:
        return SqlRateLimitRepository(self.connection)

    async def __aenter__(self) -> Self:
        self._open = await open_transaction(self.engine, self.context)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        opened, self._open = self.current, None
        try:
            if exc_type is None:
                await opened.transaction.commit()
            else:
                await opened.transaction.rollback()
        finally:
            await opened.connection.close()


class SqlUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def __call__(self, context: TenantContext) -> SqlUnitOfWork:
        return SqlUnitOfWork(self.engine, context)
