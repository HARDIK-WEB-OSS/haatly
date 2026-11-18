from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.membership.adapters.tables import invitations, memberships
from haatly.membership.domain.model import (
    Invitation,
    Membership,
    MembershipLevel,
    MembershipStatus,
)

INVITATION_CAPABILITY = "app.invitation_token_hash"
MEMBERSHIP_COLUMNS = (
    memberships.c.id,
    memberships.c.store_id,
    memberships.c.user_id,
    memberships.c.level,
    memberships.c.status,
    memberships.c.created_at,
)
INVITATION_COLUMNS = (
    invitations.c.id,
    invitations.c.store_id,
    invitations.c.created_by_user_id,
    invitations.c.label,
    invitations.c.expires_at,
    invitations.c.accepted_by_user_id,
    invitations.c.accepted_at,
    invitations.c.revoked_at,
    invitations.c.created_at,
)

type AnyRow = Row[tuple[Any, ...]]


def membership_from_row(row: AnyRow) -> Membership:
    return Membership(
        row.id,
        row.store_id,
        row.user_id,
        MembershipLevel(row.level),
        MembershipStatus(row.status),
        row.created_at,
    )


def membership_values(membership: Membership, now: datetime) -> dict[str, object]:
    return {
        "id": membership.id,
        "store_id": membership.store_id,
        "user_id": membership.user_id,
        "level": membership.level.value,
        "status": membership.status.value,
        "created_at": membership.created_at,
        "updated_at": now,
    }


def invitation_from_row(row: AnyRow) -> Invitation:
    return Invitation(*row)


class SqlMembershipRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def first(self, statement: Any) -> Membership | None:
        row = (await self.connection.execute(statement)).first()
        return None if row is None else membership_from_row(row)

    async def many(self, *conditions: Any) -> Sequence[Membership]:
        statement = (
            select(*MEMBERSHIP_COLUMNS)
            .where(*conditions)
            .order_by(memberships.c.created_at, memberships.c.id)
        )
        return [membership_from_row(row) for row in await self.connection.execute(statement)]

    async def of_user(self, store_id: UUID, user_id: UUID) -> Membership | None:
        return await self.first(
            select(*MEMBERSHIP_COLUMNS).where(
                memberships.c.store_id == store_id, memberships.c.user_id == user_id
            )
        )

    async def request(self, membership: Membership, now: datetime) -> Membership | None:
        statement = (
            insert(memberships)
            .values(**membership_values(membership, now))
            .on_conflict_do_nothing(index_elements=[memberships.c.store_id, memberships.c.user_id])
            .returning(*MEMBERSHIP_COLUMNS)
        )
        return await self.first(statement)

    async def join_by_invitation(
        self, membership: Membership, invited_by: UUID, now: datetime
    ) -> Membership | None:
        statement = insert(memberships).values(
            invited_by_user_id=invited_by, **membership_values(membership, now)
        )
        already_full = (memberships.c.level == MembershipLevel.SELLER_ADDED.value) & (
            memberships.c.status == MembershipStatus.ACTIVE.value
        )
        upgrade = statement.on_conflict_do_update(
            index_elements=[memberships.c.store_id, memberships.c.user_id],
            set_={
                "level": statement.excluded.level,
                "status": statement.excluded.status,
                "invited_by_user_id": statement.excluded.invited_by_user_id,
                "updated_at": statement.excluded.updated_at,
            },
            where=(memberships.c.status != MembershipStatus.BLOCKED.value) & ~already_full,
        )
        return await self.first(upgrade.returning(*MEMBERSHIP_COLUMNS))

    async def for_user(self, user_id: UUID) -> Sequence[Membership]:
        return await self.many(memberships.c.user_id == user_id)

    async def for_store(self, store_id: UUID) -> Sequence[Membership]:
        return await self.many(memberships.c.store_id == store_id)

    async def set_status(
        self,
        membership_id: UUID,
        allowed_from: Sequence[MembershipStatus],
        status: MembershipStatus,
        now: datetime,
    ) -> Membership | None:
        statement = (
            update(memberships)
            .where(
                memberships.c.id == membership_id,
                memberships.c.status.in_([value.value for value in allowed_from]),
            )
            .values(status=status.value, updated_at=now)
            .returning(*MEMBERSHIP_COLUMNS)
        )
        return await self.first(statement)


class SqlInvitationRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def add(self, invitation: Invitation, token_hash: str) -> None:
        await self.connection.execute(
            insert(invitations).values(
                id=invitation.id,
                store_id=invitation.store_id,
                created_by_user_id=invitation.created_by_user_id,
                token_hash=token_hash,
                label=invitation.label,
                expires_at=invitation.expires_at,
                created_at=invitation.created_at,
            )
        )

    async def first(self, statement: Any) -> Invitation | None:
        row = (await self.connection.execute(statement)).first()
        return None if row is None else invitation_from_row(row)

    async def present(self, token_hash: str) -> Invitation | None:
        capability = func.set_config(INVITATION_CAPABILITY, token_hash, True)
        await self.connection.execute(select(capability))
        return await self.first(
            select(*INVITATION_COLUMNS).where(invitations.c.token_hash == token_hash)
        )

    async def claim(self, invitation_id: UUID, user_id: UUID, now: datetime) -> bool:
        statement = (
            update(invitations)
            .where(
                invitations.c.id == invitation_id,
                invitations.c.accepted_at.is_(None),
                invitations.c.revoked_at.is_(None),
                invitations.c.expires_at > now,
            )
            .values(accepted_by_user_id=user_id, accepted_at=now)
            .returning(invitations.c.id)
        )
        return (await self.connection.execute(statement)).first() is not None

    async def for_store(self, store_id: UUID) -> Sequence[Invitation]:
        statement = (
            select(*INVITATION_COLUMNS)
            .where(invitations.c.store_id == store_id)
            .order_by(invitations.c.created_at, invitations.c.id)
        )
        return [invitation_from_row(row) for row in await self.connection.execute(statement)]

    async def revoke(self, invitation_id: UUID, now: datetime) -> Invitation | None:
        return await self.first(
            update(invitations)
            .where(
                invitations.c.id == invitation_id,
                invitations.c.accepted_at.is_(None),
                invitations.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(*INVITATION_COLUMNS)
        )


class SqlStoreGate:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def call(self, function: Any, store_id: UUID) -> object:
        return (await self.connection.execute(select(function(store_id)))).scalar()

    async def accepts_members(self, store_id: UUID) -> bool:
        return bool(await self.call(func.stores.accepts_members, store_id))

    async def is_live(self, store_id: UUID) -> bool:
        return bool(await self.call(func.stores.is_live, store_id))

    async def owner_of(self, store_id: UUID) -> UUID | None:
        owner = await self.call(func.stores.owner_of, store_id)
        return owner if isinstance(owner, UUID) else None
