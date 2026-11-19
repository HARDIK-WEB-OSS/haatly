from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.platform.public import (
    SYSTEM,
    ActorRole,
    FrozenClock,
    IdempotencyGate,
    NewEvent,
    NewJob,
    Proceed,
    Replay,
    SqlUnitOfWorkFactory,
    StoredResponse,
    TenantContext,
    Uuid7Source,
    enqueue_job,
    record_event,
)

pytestmark = pytest.mark.anyio

ALICE_ID = UUID("018f0000-0000-7000-8000-0000000000a1")
ALICE = TenantContext(ActorRole.BUYER, ALICE_ID)
BOB = TenantContext(ActorRole.BUYER, UUID("018f0000-0000-7000-8000-0000000000b0"))
SELLER = TenantContext(ActorRole.SELLER, ALICE_ID, UUID("018f0000-0000-7000-8000-0000000000c5"))
SCOPE = "checkout:shared"
BODY = b'{"variant": "v1"}'
SECRET = StoredResponse(201, '{"order": "alice-only", "amount_paise": 129900}')
PLATFORM_TABLES = (
    "platform.idempotency_keys",
    "platform.jobs",
    "platform.outbox_events",
    "platform.processed_events",
)


async def count_as(engine: AsyncEngine, context: TenantContext | None, table: str) -> int:
    async with engine.begin() as connection:
        if context is not None:
            for name, value in context.settings().items():
                await connection.execute(
                    text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value}
                )
        counted = text(f"SELECT count(*) FROM {table}")  # noqa: S608
        return int((await connection.execute(counted)).scalar_one())


async def test_a_stored_response_is_replayed_only_to_the_caller_that_owns_it(
    uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine
) -> None:
    gate = IdempotencyGate(uow_factory, FrozenClock(), lease=timedelta(minutes=2))
    assert await gate.begin(ALICE, SCOPE, "key-1", BODY) == Proceed()
    await gate.complete(ALICE, SCOPE, "key-1", SECRET)
    assert await gate.begin(ALICE, SCOPE, "key-1", BODY) == Replay(SECRET)
    for intruder in (BOB, SELLER):
        with pytest.raises(DBAPIError, match="row-level security"):
            await gate.begin(intruder, SCOPE, "key-1", BODY)
        assert await count_as(app_engine, intruder, "platform.idempotency_keys") == 0
    assert await count_as(app_engine, ALICE, "platform.idempotency_keys") == 1
    assert await count_as(app_engine, SYSTEM, "platform.idempotency_keys") == 1


async def test_the_key_records_the_full_owner(
    uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine
) -> None:
    gate = IdempotencyGate(uow_factory, FrozenClock())
    await gate.begin(SELLER, "payout:store", "key-2", BODY)
    async with app_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        row = (
            await connection.execute(
                text("SELECT actor_role, user_id, store_id FROM platform.idempotency_keys")
            )
        ).one()
    assert tuple(row) == ("seller", SELLER.user_id, SELLER.store_id)


async def test_tenants_append_events_and_jobs_but_only_system_reads_them(
    uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine
) -> None:
    clock = FrozenClock()
    ids = Uuid7Source(clock)
    job = NewJob("demo.job", {"order": "o-1"}, dedupe_key="demo:o-1")
    async with uow_factory(ALICE) as uow:
        await record_event(uow, ids, clock, NewEvent("demo.happened", ALICE_ID, {"n": 1}))
        assert await enqueue_job(uow, ids, clock, job)
        assert not await enqueue_job(uow, ids, clock, job)
        rows = await uow.connection.execute(text("SELECT count(*) FROM platform.jobs"))
        assert rows.scalar_one() == 0
        changed = await uow.connection.execute(
            text("UPDATE platform.outbox_events SET topic = 'x'")
        )
        assert changed.rowcount == 0
        removed = await uow.connection.execute(text("DELETE FROM platform.jobs"))
        assert removed.rowcount == 0
    for table in ("platform.jobs", "platform.outbox_events"):
        assert await count_as(app_engine, ALICE, table) == 0
        assert await count_as(app_engine, SYSTEM, table) == 1


async def test_only_system_records_processed_events(uow_factory: SqlUnitOfWorkFactory) -> None:
    statement = text(
        "INSERT INTO platform.processed_events (consumer, event_id, processed_at) "
        "VALUES ('demo', '018f0000-0000-7000-8000-00000000e001', '2026-11-19T00:00:00Z')"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with uow_factory(ALICE) as uow:
            await uow.connection.execute(statement)
    async with uow_factory(SYSTEM) as uow:
        await uow.connection.execute(statement)


@pytest.mark.parametrize("table", PLATFORM_TABLES)
async def test_a_transaction_without_a_context_sees_nothing(
    uow_factory: SqlUnitOfWorkFactory, app_engine: AsyncEngine, table: str
) -> None:
    clock = FrozenClock()
    ids = Uuid7Source(clock)
    async with uow_factory(SYSTEM) as uow:
        await enqueue_job(uow, ids, clock, NewJob("demo.job", {}))
        await record_event(uow, ids, clock, NewEvent("demo.happened", ALICE_ID, {}))
        await uow.processed.mark("demo", ids.new_id(), clock.now())
    await IdempotencyGate(uow_factory, clock).begin(ALICE, SCOPE, "key-3", BODY)
    assert await count_as(app_engine, SYSTEM, table) == 1
    assert await count_as(app_engine, None, table) == 0
