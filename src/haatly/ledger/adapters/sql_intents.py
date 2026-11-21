from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Row, bindparam, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.ledger.adapters.tables import (
    accounts,
    cursors,
    drift,
    intent_accounts,
    intents,
    transfers,
)
from haatly.ledger.app.intents import IntentConflictError
from haatly.ledger.app.ports import AccountConflictError, DriftEntry, IntentState
from haatly.ledger.domain.ids import id_from_uuid, uuid_from_id
from haatly.ledger.domain.model import (
    AccountFlag,
    AccountSpec,
    Intent,
    IntentStatus,
    Ledger,
    TransferKind,
    TransferSpec,
)
from haatly.ledger.domain.outcomes import Settlement
from haatly.platform.public import is_unique_violation

TIGERBEETLE_CURSOR = "tigerbeetle"

type AnyRow = Row[tuple[object, ...]]


def account_values(account: AccountSpec, now: datetime) -> dict[str, object]:
    return {
        "id": uuid_from_id(account.id),
        "ledger": int(account.ledger),
        "code": account.code,
        "flags": sorted(flag.value for flag in account.flags),
        "created_at": now,
    }


def account_from_row(row: AnyRow) -> AccountSpec:
    return AccountSpec(
        id_from_uuid(row.id),
        Ledger(row.ledger),
        row.code,
        frozenset(AccountFlag(flag) for flag in row.flags),
    )


def transfer_values(intent_id: UUID, position: int, transfer: TransferSpec) -> dict[str, object]:
    return {
        "id": uuid_from_id(transfer.id),
        "intent_id": intent_id,
        "position": position,
        "debit_account_id": uuid_from_id(transfer.debit_account_id),
        "credit_account_id": uuid_from_id(transfer.credit_account_id),
        "amount": transfer.amount,
        "ledger": int(transfer.ledger),
        "code": transfer.code,
        "kind": transfer.kind.value,
        "pending_id": uuid_from_id(transfer.pending_id) if transfer.pending_id else None,
        "timeout_seconds": transfer.timeout_seconds,
    }


def transfer_from_row(row: AnyRow) -> TransferSpec:
    return TransferSpec(
        id=id_from_uuid(row.id),
        debit_account_id=id_from_uuid(row.debit_account_id),
        credit_account_id=id_from_uuid(row.credit_account_id),
        amount=row.amount,
        ledger=Ledger(row.ledger),
        code=row.code,
        kind=TransferKind(row.kind),
        pending_id=0 if row.pending_id is None else id_from_uuid(row.pending_id),
        timeout_seconds=row.timeout_seconds,
    )


def group_by_intent(rows: Sequence[AnyRow]) -> defaultdict[UUID, list[AnyRow]]:
    grouped: defaultdict[UUID, list[AnyRow]] = defaultdict(list)
    for row in rows:
        grouped[row.intent_id].append(row)
    return grouped


def intent_from_rows(
    row: AnyRow, account_rows: Sequence[AnyRow], transfer_rows: Sequence[AnyRow]
) -> Intent:
    settled = transfer_rows[0].outcome is not None
    return Intent(
        id=row.id,
        kind=row.kind,
        reference_id=row.reference_id,
        status=IntentStatus(row.status),
        transfers=tuple(map(transfer_from_row, transfer_rows)),
        accounts=tuple(map(account_from_row, account_rows)),
        created_at=row.created_at,
        result_code=row.result_code,
        outcomes=tuple(transfer.outcome for transfer in transfer_rows) if settled else (),
    )


class SqlIntentRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def add(self, intent: Intent) -> None:
        try:
            async with self.connection.begin_nested():
                await self.insert(intent)
        except IntegrityError as error:
            if is_unique_violation(error):
                raise IntentConflictError from error
            raise

    async def insert(self, intent: Intent) -> None:
        await self.ensure_accounts(intent.accounts, intent.created_at)
        await self.connection.execute(
            insert(intents).values(
                id=intent.id,
                kind=intent.kind,
                reference_id=intent.reference_id,
                status=intent.status.value,
                created_at=intent.created_at,
                actor_role=func.platform.actor_role(),
                user_id=func.platform.actor_user_id(),
                store_id=func.platform.actor_store_id(),
            )
        )
        if intent.accounts:
            await self.connection.execute(
                insert(intent_accounts),
                [
                    {"intent_id": intent.id, "position": position, "account_id": uuid_from_id(a.id)}
                    for position, a in enumerate(intent.accounts)
                ],
            )
        await self.connection.execute(
            insert(transfers),
            [
                transfer_values(intent.id, position, transfer)
                for position, transfer in enumerate(intent.transfers)
            ],
        )

    async def ensure_accounts(self, specs: Sequence[AccountSpec], now: datetime) -> None:
        if not specs:
            return
        await self.connection.execute(
            insert(accounts)
            .values([account_values(spec, now) for spec in specs])
            .on_conflict_do_nothing(index_elements=[accounts.c.id])
        )
        statement = select(accounts).where(
            accounts.c.id.in_([uuid_from_id(spec.id) for spec in specs])
        )
        stored = {
            account.id: account
            for account in map(account_from_row, (await self.connection.execute(statement)).all())
        }
        for spec in specs:
            if stored[spec.id] != spec:
                raise AccountConflictError(str(uuid_from_id(spec.id)))

    async def load_many(self, intent_ids: Sequence[UUID]) -> list[Intent]:
        if not intent_ids:
            return []
        rows = (
            await self.connection.execute(
                select(intents).where(intents.c.id.in_(intent_ids)).order_by(intents.c.id)
            )
        ).all()
        account_rows = (
            await self.connection.execute(
                select(intent_accounts.c.intent_id, accounts)
                .join(accounts, accounts.c.id == intent_accounts.c.account_id)
                .where(intent_accounts.c.intent_id.in_(intent_ids))
                .order_by(intent_accounts.c.intent_id, intent_accounts.c.position)
            )
        ).all()
        transfer_rows = (
            await self.connection.execute(
                select(transfers)
                .where(transfers.c.intent_id.in_(intent_ids))
                .order_by(transfers.c.intent_id, transfers.c.position)
            )
        ).all()
        accounts_by_intent = group_by_intent(account_rows)
        transfers_by_intent = group_by_intent(transfer_rows)
        return [
            intent_from_rows(row, accounts_by_intent[row.id], transfers_by_intent[row.id])
            for row in rows
        ]

    async def get(self, intent_id: UUID) -> Intent | None:
        found = await self.load_many([intent_id])
        return found[0] if found else None

    async def find_by_reference(self, kind: str, reference_id: UUID) -> Intent | None:
        statement = select(intents.c.id).where(
            intents.c.kind == kind, intents.c.reference_id == reference_id
        )
        intent_id = (await self.connection.execute(statement)).scalar_one_or_none()
        return None if intent_id is None else await self.get(intent_id)

    async def settle(self, intent_id: UUID, settlement: Settlement, now: datetime) -> bool:
        statement = (
            update(intents)
            .where(intents.c.id == intent_id, intents.c.status == IntentStatus.PENDING.value)
            .values(status=settlement.status.value, result_code=settlement.code, settled_at=now)
            .returning(intents.c.id)
        )
        if (await self.connection.execute(statement)).first() is None:
            return False
        if settlement.outcomes:
            await self.connection.execute(
                update(transfers)
                .where(
                    transfers.c.intent_id == bindparam("target_intent"),
                    transfers.c.position == bindparam("target_position"),
                )
                .values(outcome=bindparam("target_outcome")),
                [
                    {
                        "target_intent": intent_id,
                        "target_position": position,
                        "target_outcome": code,
                    }
                    for position, code in enumerate(settlement.outcomes)
                ],
            )
        return True

    async def stale_pending(self, before: datetime, limit: int) -> list[UUID]:
        statement = (
            select(intents.c.id)
            .where(intents.c.status == IntentStatus.PENDING.value, intents.c.created_at <= before)
            .order_by(intents.c.created_at, intents.c.id)
            .limit(limit)
        )
        return list((await self.connection.execute(statement)).scalars())

    async def unverified(self, limit: int) -> list[Intent]:
        statement = (
            select(intents.c.id)
            .where(intents.c.status == IntentStatus.APPLIED.value, intents.c.verified_at.is_(None))
            .order_by(intents.c.settled_at, intents.c.id)
            .limit(limit)
        )
        return await self.load_many(list((await self.connection.execute(statement)).scalars()))

    async def mark_verified(self, intent_ids: Sequence[UUID], now: datetime) -> None:
        if intent_ids:
            await self.connection.execute(
                update(intents).where(intents.c.id.in_(intent_ids)).values(verified_at=now)
            )

    async def states_of_transfers(self, transfer_ids: Sequence[int]) -> Mapping[int, IntentState]:
        statement = (
            select(transfers.c.id, intents.c.id.label("intent_id"), intents.c.status)
            .join(intents, intents.c.id == transfers.c.intent_id)
            .where(transfers.c.id.in_([uuid_from_id(value) for value in transfer_ids]))
        )
        return {
            id_from_uuid(row.id): IntentState(row.intent_id, IntentStatus(row.status))
            for row in (await self.connection.execute(statement)).all()
        }

    async def cursor(self) -> int:
        statement = select(cursors.c.position).where(cursors.c.name == TIGERBEETLE_CURSOR)
        position = (await self.connection.execute(statement)).scalar_one_or_none()
        return 0 if position is None else int(position)

    async def advance_cursor(self, timestamp: int) -> None:
        statement = insert(cursors).values(name=TIGERBEETLE_CURSOR, position=timestamp)
        await self.connection.execute(
            statement.on_conflict_do_update(
                index_elements=[cursors.c.name],
                set_={"position": func.greatest(cursors.c.position, statement.excluded.position)},
            )
        )

    async def record_drift(
        self, transfer_id: int, intent_id: UUID | None, reason: str, now: datetime
    ) -> bool:
        statement = (
            insert(drift)
            .values(
                transfer_id=uuid_from_id(transfer_id),
                intent_id=intent_id,
                reason=reason,
                detected_at=now,
            )
            .on_conflict_do_nothing(index_elements=[drift.c.transfer_id])
            .returning(drift.c.transfer_id)
        )
        return (await self.connection.execute(statement)).first() is not None

    async def open_drift(self, limit: int) -> list[DriftEntry]:
        statement = (
            select(drift)
            .where(drift.c.resolved_at.is_(None))
            .order_by(drift.c.detected_at, drift.c.transfer_id)
            .limit(limit)
        )
        return [
            DriftEntry(id_from_uuid(row.transfer_id), row.intent_id, row.reason, row.detected_at)
            for row in (await self.connection.execute(statement)).all()
        ]

    async def count_open_drift(self) -> int:
        statement = select(func.count()).select_from(drift).where(drift.c.resolved_at.is_(None))
        return int((await self.connection.execute(statement)).scalar_one())
