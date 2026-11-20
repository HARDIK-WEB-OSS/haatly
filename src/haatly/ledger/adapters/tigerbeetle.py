import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import tigerbeetle as tb

from haatly.ledger.adapters.settings import LedgerSettings
from haatly.ledger.app.ports import Balance, LedgerUnavailableError, TransferRecord
from haatly.ledger.domain.model import AccountFlag, AccountSpec, TransferKind, TransferSpec

ACCOUNT_FLAGS = {
    AccountFlag.DEBITS_MUST_NOT_EXCEED_CREDITS: tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS,
    AccountFlag.CREDITS_MUST_NOT_EXCEED_DEBITS: tb.AccountFlags.CREDITS_MUST_NOT_EXCEED_DEBITS,
    AccountFlag.HISTORY: tb.AccountFlags.HISTORY,
}
KIND_FLAGS = {
    TransferKind.SINGLE: tb.TransferFlags.NONE,
    TransferKind.PENDING: tb.TransferFlags.PENDING,
    TransferKind.POST: tb.TransferFlags.POST_PENDING_TRANSFER,
    TransferKind.VOID: tb.TransferFlags.VOID_PENDING_TRANSFER,
}
SPECIAL_KINDS = (TransferKind.POST, TransferKind.VOID, TransferKind.PENDING)


async def resolve_addresses(addresses: str) -> str:
    loop = asyncio.get_running_loop()
    resolved = []
    for address in addresses.split(","):
        host, port = address.strip().rsplit(":", 1)
        infos = await loop.getaddrinfo(host, int(port), family=socket.AF_INET)
        resolved.append(f"{infos[0][4][0]}:{port}")
    return ",".join(resolved)


def to_account(spec: AccountSpec) -> tb.Account:
    flags = tb.AccountFlags.NONE
    for flag in spec.flags:
        flags |= ACCOUNT_FLAGS[flag]
    return tb.Account(id=spec.id, ledger=spec.ledger, code=spec.code, flags=flags)


def to_transfer(spec: TransferSpec, intent_id: UUID, *, linked: bool) -> tb.Transfer:
    flags = KIND_FLAGS[spec.kind]
    if linked:
        flags |= tb.TransferFlags.LINKED
    return tb.Transfer(
        id=spec.id,
        debit_account_id=spec.debit_account_id,
        credit_account_id=spec.credit_account_id,
        amount=spec.amount,
        pending_id=spec.pending_id,
        user_data_128=intent_id.int,
        timeout=spec.timeout_seconds,
        ledger=spec.ledger,
        code=spec.code,
        flags=flags,
    )


def kind_of(flags: tb.TransferFlags) -> TransferKind:
    for kind in SPECIAL_KINDS:
        if flags & KIND_FLAGS[kind]:
            return kind
    return TransferKind.SINGLE


def record_of(transfer: tb.Transfer) -> TransferRecord:
    return TransferRecord(
        id=transfer.id,
        debit_account_id=transfer.debit_account_id,
        credit_account_id=transfer.credit_account_id,
        amount=transfer.amount,
        ledger=transfer.ledger,
        code=transfer.code,
        kind=kind_of(transfer.flags),
        pending_id=transfer.pending_id,
        intent_id=UUID(int=transfer.user_data_128) if transfer.user_data_128 else None,
        timestamp=transfer.timestamp,
    )


def balance_of(account: tb.Account) -> Balance:
    return Balance(
        account.id,
        account.debits_pending,
        account.debits_posted,
        account.credits_pending,
        account.credits_posted,
    )


READINESS_PROBE_ACCOUNT = 1


class TigerBeetleGateway:
    def __init__(self, client: tb.ClientAsync, timeout_seconds: float) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.abandoned: set[asyncio.Future[Any]] = set()

    async def call[T](self, operation: Awaitable[T]) -> T:
        request = asyncio.ensure_future(operation)
        try:
            return await asyncio.wait_for(asyncio.shield(request), self.timeout_seconds)
        except TimeoutError as error:
            self.abandoned.add(request)
            request.add_done_callback(self.forget)
            raise LedgerUnavailableError from error

    def forget(self, request: asyncio.Future[Any]) -> None:
        self.abandoned.discard(request)
        request.cancelled() or request.exception()

    async def close(self) -> None:
        await self.client.close()
        await asyncio.gather(*self.abandoned, return_exceptions=True)

    async def create_accounts(self, accounts: Sequence[AccountSpec]) -> list[str]:
        if not accounts:
            return []
        results = await self.call(self.client.create_accounts([to_account(a) for a in accounts]))
        return [result.status.name.lower() for result in results]

    async def create_transfers(
        self, intent_id: UUID, transfers: Sequence[TransferSpec]
    ) -> list[str]:
        last = len(transfers) - 1
        batch = [
            to_transfer(transfer, intent_id, linked=position < last)
            for position, transfer in enumerate(transfers)
        ]
        results = await self.call(self.client.create_transfers(batch))
        return [result.status.name.lower() for result in results]

    async def lookup_transfers(self, ids: Sequence[int]) -> list[TransferRecord]:
        if not ids:
            return []
        return [
            record_of(found) for found in await self.call(self.client.lookup_transfers(list(ids)))
        ]

    async def transfers_after(self, timestamp: int, limit: int) -> list[TransferRecord]:
        query = tb.QueryFilter(0, 0, 0, 0, 0, timestamp + 1, 0, limit, tb.QueryFilterFlags.NONE)
        return [record_of(found) for found in await self.call(self.client.query_transfers(query))]

    async def lookup_balances(self, ids: Sequence[int]) -> list[Balance]:
        accounts = await self.call(self.client.lookup_accounts(list(ids)))
        return [balance_of(account) for account in accounts]

    async def is_reachable(self) -> bool:
        try:
            await self.lookup_balances((READINESS_PROBE_ACCOUNT,))
        except LedgerUnavailableError:
            return False
        return True


@asynccontextmanager
async def open_tigerbeetle(settings: LedgerSettings) -> AsyncIterator[TigerBeetleGateway]:
    addresses = await resolve_addresses(settings.tigerbeetle_addresses)
    client = tb.ClientAsync(cluster_id=settings.tigerbeetle_cluster_id, replica_addresses=addresses)
    gateway = TigerBeetleGateway(client, settings.tigerbeetle_timeout_seconds)
    try:
        yield gateway
    finally:
        await gateway.close()
