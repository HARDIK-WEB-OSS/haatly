from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import tigerbeetle as tb
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.ledger.public import (
    IntentApplier,
    LedgerGateway,
    LedgerSettings,
    Reconciler,
    SqlLedgerUnitOfWorkFactory,
    open_tigerbeetle,
)
from haatly.platform.public import SYSTEM, FrozenClock, SystemClock, Uuid7Source
from tests.ledger.scenario import Inventory

TEST_HOLD_TIMEOUT_SECONDS = 2


@pytest.fixture
async def gateway(ledger_settings: LedgerSettings) -> AsyncIterator[LedgerGateway]:
    async with open_tigerbeetle(ledger_settings) as opened:
        yield opened


@pytest.fixture
async def raw_tigerbeetle(ledger_settings: LedgerSettings) -> AsyncIterator[tb.ClientAsync]:
    host, port = ledger_settings.tigerbeetle_addresses.rsplit(":", 1)
    address = f"{'127.0.0.1' if host == 'localhost' else host}:{port}"
    async with tb.ClientAsync(cluster_id=0, replica_addresses=address) as client:
        yield client


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def ids() -> Uuid7Source:
    return Uuid7Source(SystemClock())


@pytest.fixture
def ledger_uow_factory(app_engine: AsyncEngine) -> SqlLedgerUnitOfWorkFactory:
    return SqlLedgerUnitOfWorkFactory(app_engine)


@pytest.fixture
async def ledger_cursor_at_now(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory, raw_tigerbeetle: tb.ClientAsync
) -> int:
    newest = await raw_tigerbeetle.query_transfers(
        tb.QueryFilter(0, 0, 0, 0, 0, 0, 0, 1, tb.QueryFilterFlags.REVERSED)
    )
    timestamp = newest[0].timestamp if newest else 0
    async with ledger_uow_factory(SYSTEM) as uow:
        await uow.intents.advance_cursor(timestamp)
    return timestamp


@pytest.fixture
def applier(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    gateway: LedgerGateway,
    ids: Uuid7Source,
    clock: FrozenClock,
) -> IntentApplier:
    return IntentApplier(ledger_uow_factory, gateway, ids, clock)


@pytest.fixture
def reconciler(
    ledger_uow_factory: SqlLedgerUnitOfWorkFactory,
    gateway: LedgerGateway,
    applier: IntentApplier,
    clock: FrozenClock,
    ledger_cursor_at_now: int,
) -> Reconciler:
    return Reconciler(
        ledger_uow_factory, gateway, applier, clock, grace=timedelta(seconds=30), batch_size=50
    )


@pytest.fixture
def inventory(ids: Uuid7Source) -> Inventory:
    return Inventory.fresh(ids, TEST_HOLD_TIMEOUT_SECONDS)
