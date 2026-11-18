import socket
import time

import pytest

from haatly.ledger.public import (
    Balance,
    LedgerGateway,
    LedgerSettings,
    LedgerUnavailableError,
    TransferKind,
    open_tigerbeetle,
    resolve_addresses,
)
from haatly.platform.public import Uuid7Source
from tests.ledger.scenario import Inventory

pytestmark = pytest.mark.anyio


async def test_accounts_are_created_once_and_then_exist(
    gateway: LedgerGateway, inventory: Inventory
) -> None:
    assert await gateway.create_accounts(inventory.accounts) == ["created"] * 3
    assert await gateway.create_accounts(inventory.accounts) == ["exists"] * 3
    assert await gateway.create_accounts(()) == []


async def test_transfers_carry_their_intent_and_come_back_intact(
    gateway: LedgerGateway, inventory: Inventory, ids: Uuid7Source
) -> None:
    await gateway.create_accounts(inventory.accounts)
    intent_id = ids.new_id()
    receipt = inventory.receive(5).transfers[0]
    hold = inventory.hold(3, timeout_seconds=600).transfers[0]
    assert await gateway.create_transfers(intent_id, (receipt, hold)) == ["created", "created"]
    assert await gateway.create_transfers(intent_id, (receipt, hold)) == [
        "exists",
        "linked_event_failed",
    ]
    assert await gateway.create_transfers(intent_id, (hold,)) == ["exists"]
    found = {record.id: record for record in await gateway.lookup_transfers((receipt.id, hold.id))}
    assert found[hold.id].intent_id == intent_id
    assert (found[hold.id].kind, found[hold.id].amount) == (TransferKind.PENDING, 3)
    assert (found[hold.id].debit_account_id, found[hold.id].credit_account_id) == (
        inventory.stock.id,
        inventory.sold.id,
    )
    assert found[receipt.id].kind is TransferKind.SINGLE
    assert await gateway.lookup_transfers(()) == []


async def test_a_failing_transfer_fails_its_whole_chain(
    gateway: LedgerGateway, inventory: Inventory, ids: Uuid7Source
) -> None:
    await gateway.create_accounts(inventory.accounts)
    receipt = inventory.receive(1).transfers[0]
    hold = inventory.hold(2).transfers[0]
    results = await gateway.create_transfers(ids.new_id(), (receipt, hold))
    assert results == ["linked_event_failed", "exceeds_credits"]
    assert await gateway.lookup_transfers((receipt.id,)) == []


async def test_post_and_void_kinds_round_trip(
    gateway: LedgerGateway, inventory: Inventory, ids: Uuid7Source
) -> None:
    await gateway.create_accounts(inventory.accounts)
    first, second = inventory.hold(1, 600), inventory.hold(1, 600)
    post = inventory.capture(first).transfers[0]
    void = inventory.release(second).transfers[0]
    transfers = (inventory.receive(2).transfers[0], first.transfers[0], second.transfers[0])
    await gateway.create_transfers(ids.new_id(), transfers)
    assert await gateway.create_transfers(ids.new_id(), (post, void)) == ["created", "created"]
    found = {record.id: record for record in await gateway.lookup_transfers((post.id, void.id))}
    assert (found[post.id].kind, found[post.id].pending_id) == (
        TransferKind.POST,
        first.transfers[0].id,
    )
    assert found[void.id].kind is TransferKind.VOID
    balances = await gateway.lookup_balances((inventory.stock.id, inventory.sold.id))
    assert balances == [
        Balance(inventory.stock.id, 0, 1, 0, 2),
        Balance(inventory.sold.id, 0, 0, 0, 1),
    ]


async def test_transfers_after_pages_in_commit_order(
    gateway: LedgerGateway, inventory: Inventory, ids: Uuid7Source
) -> None:
    await gateway.create_accounts(inventory.accounts)
    receipts = tuple(inventory.receive(1).transfers[0] for _ in range(3))
    await gateway.create_transfers(ids.new_id(), receipts[:1])
    start = (await gateway.lookup_transfers((receipts[0].id,)))[0].timestamp
    await gateway.create_transfers(ids.new_id(), receipts[1:])
    page = await gateway.transfers_after(start, 1)
    assert [record.id for record in page] == [receipts[1].id]
    rest = await gateway.transfers_after(page[0].timestamp, 10)
    assert [record.id for record in rest][:1] == [receipts[2].id]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


async def test_an_unreachable_cluster_times_out_instead_of_hanging() -> None:
    settings = LedgerSettings(
        tigerbeetle_addresses=f"127.0.0.1:{free_port()}", tigerbeetle_timeout_seconds=0.5
    )
    started = time.monotonic()
    async with open_tigerbeetle(settings) as unreachable:
        with pytest.raises(LedgerUnavailableError):
            await unreachable.lookup_transfers((1,))
    assert time.monotonic() - started < 5


async def test_hostnames_are_resolved_for_the_client() -> None:
    resolved = await resolve_addresses("localhost:3000,127.0.0.1:3001")
    assert resolved == "127.0.0.1:3000,127.0.0.1:3001"
