from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.ledger.public import (
    INTENT_SETTLED,
    Intent,
    IntentApplier,
    IntentConflictError,
    IntentStatus,
    NewIntent,
    SqlLedgerUnitOfWorkFactory,
    UnknownIntentError,
    record_intent,
)
from haatly.platform.public import (
    SYSTEM,
    ActorRole,
    FrozenClock,
    TenantContext,
    Uuid7Source,
)
from tests.ledger.scenario import Inventory

pytestmark = pytest.mark.anyio

BACKSTOP = timedelta(seconds=30)
LEDGER_TABLES = ("accounts", "intents", "intent_accounts", "transfers", "drift", "cursors")


def buyer(ids: Uuid7Source) -> TenantContext:
    return TenantContext(ActorRole.BUYER, ids.new_id())


async def record_as(
    factory: SqlLedgerUnitOfWorkFactory,
    context: TenantContext,
    ids: Uuid7Source,
    clock: FrozenClock,
    new: NewIntent,
) -> UUID:
    async with factory(context) as uow:
        return (await record_intent(uow, ids, clock, new, BACKSTOP)).id


async def visible_rows(engine: AsyncEngine, context: TenantContext, table: str) -> int:
    settings = context.settings()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "SELECT set_config('app.role', :role, true), "
                "set_config('app.user_id', :user, true), set_config('app.store_id', :store, true)"
            ),
            {
                "role": settings["app.role"],
                "user": settings["app.user_id"],
                "store": settings["app.store_id"],
            },
        )
        statement = text(f"SELECT count(*) FROM ledger.{table}")  # noqa: S608
        return int((await connection.execute(statement)).scalar_one())


async def test_a_tenant_sees_and_applies_only_its_own_intents(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    applier: IntentApplier,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    owner, stranger = buyer(ids), buyer(ids)
    intent_id = await record_as(ledger_uow_factory, owner, ids, clock, inventory.receive(3))
    for context in (stranger, TenantContext(ActorRole.SELLER, ids.new_id(), ids.new_id())):
        with pytest.raises(UnknownIntentError):
            await applier.load(intent_id, context)
        with pytest.raises(UnknownIntentError):
            await applier.apply(intent_id, context)
    assert (await applier.load(intent_id, SYSTEM)).status is IntentStatus.PENDING
    assert await applier.apply(intent_id, owner) is IntentStatus.APPLIED
    async with ledger_uow_factory(SYSTEM) as uow:
        events = await uow.outbox.claim_unpublished(1000)
    assert [event.aggregate_id for event in events if event.topic == INTENT_SETTLED] == [intent_id]


async def test_the_owner_comes_from_the_transaction_context(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    app_engine: AsyncEngine,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    seller = TenantContext(ActorRole.SELLER, ids.new_id(), ids.new_id())
    intent_id = await record_as(ledger_uow_factory, seller, ids, clock, inventory.receive(1))
    async with app_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        row = (
            await connection.execute(
                text("SELECT actor_role, user_id, store_id FROM ledger.intents WHERE id = :i"),
                {"i": intent_id},
            )
        ).one()
    assert tuple(row) == ("seller", seller.user_id, seller.store_id)


async def test_another_actors_reference_is_a_conflict_that_reveals_nothing(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    owner, stranger = buyer(ids), buyer(ids)
    taken = inventory.receive(4)
    await record_as(ledger_uow_factory, owner, ids, clock, taken)
    changed = NewIntent(
        taken.kind, taken.reference_id, inventory.receive(9).transfers, taken.accounts
    )
    with pytest.raises(IntentConflictError) as own_mismatch:
        await record_as(ledger_uow_factory, owner, ids, clock, changed)
    fresh = inventory.receive(2)
    async with ledger_uow_factory(stranger) as uow:
        with pytest.raises(IntentConflictError) as foreign:
            await record_intent(uow, ids, clock, taken, BACKSTOP)
        kept = await record_intent(uow, ids, clock, fresh, BACKSTOP)
    assert str(foreign.value) == str(own_mismatch.value) == "intent conflict"
    async with ledger_uow_factory(stranger) as uow:
        assert await uow.intents.get(kept.id) is not None


async def test_child_rows_follow_their_intent_and_accounts_are_shared(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    app_engine: AsyncEngine,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    owner, stranger = buyer(ids), buyer(ids)
    await record_as(ledger_uow_factory, owner, ids, clock, inventory.receive(1))
    for table, owner_rows, stranger_rows in (
        ("intents", 1, 0),
        ("transfers", 1, 0),
        ("intent_accounts", 3, 0),
    ):
        assert await visible_rows(app_engine, owner, table) == owner_rows
        assert await visible_rows(app_engine, stranger, table) == stranger_rows
    assert await visible_rows(app_engine, stranger, "accounts") == 3
    await record_as(ledger_uow_factory, stranger, ids, clock, inventory.hold(1, 600))
    assert await visible_rows(app_engine, stranger, "intents") == 1


async def test_drift_and_cursors_are_system_only(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    app_engine: AsyncEngine,
    ids: Uuid7Source,
) -> None:
    async with ledger_uow_factory(SYSTEM) as uow:
        await uow.intents.advance_cursor(42)
    seller = TenantContext(ActorRole.SELLER, ids.new_id(), ids.new_id())
    for context in (buyer(ids), seller, TenantContext(ActorRole.ADMIN, ids.new_id())):
        assert await visible_rows(app_engine, context, "cursors") == 0
        assert await visible_rows(app_engine, context, "drift") == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with ledger_uow_factory(buyer(ids)) as uow:
            await uow.intents.advance_cursor(43)


async def test_a_transaction_without_context_sees_nothing(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    app_engine: AsyncEngine,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    await record_as(ledger_uow_factory, SYSTEM, ids, clock, inventory.receive(1))
    async with ledger_uow_factory(SYSTEM) as uow:
        await uow.intents.advance_cursor(7)
        await uow.intents.record_drift(inventory.stock.id, None, "planted", clock.now())
    for table in LEDGER_TABLES:
        assert await visible_rows(app_engine, SYSTEM, table) > 0, table
    async with app_engine.connect() as connection:
        for table in LEDGER_TABLES:
            statement = text(f"SELECT count(*) FROM ledger.{table}")  # noqa: S608
            assert (await connection.execute(statement)).scalar_one() == 0


async def test_an_integrity_error_that_is_not_a_duplicate_is_not_a_conflict(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    ids: Uuid7Source,
    clock: FrozenClock,
    inventory: Inventory,
) -> None:
    receipt = inventory.receive(1)
    unsettled = Intent(
        id=ids.new_id(),
        kind=receipt.kind,
        reference_id=receipt.reference_id,
        status=IntentStatus.APPLIED,
        transfers=receipt.transfers,
        accounts=receipt.accounts,
        created_at=clock.now(),
    )
    with pytest.raises(IntegrityError, match="settled_unless_pending"):
        async with ledger_uow_factory(SYSTEM) as uow:
            await uow.intents.add(unsettled)
