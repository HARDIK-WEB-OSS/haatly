from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.identity.adapters.tables import sessions, users
from haatly.identity.app.ports import EmailTakenError
from haatly.identity.domain.model import Session, User
from haatly.platform.public import is_unique_violation

USER_COLUMNS = (
    users.c.id,
    users.c.email,
    users.c.password_hash,
    users.c.is_admin,
    users.c.created_at,
)


class SqlUserRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def add(self, user: User) -> None:
        statement = insert(users).values(
            id=user.id,
            email=user.email,
            password_hash=user.password_hash,
            is_admin=user.is_admin,
            created_at=user.created_at,
            updated_at=user.created_at,
        )
        try:
            async with self.connection.begin_nested():
                await self.connection.execute(statement)
        except IntegrityError as error:
            if is_unique_violation(error):
                raise EmailTakenError from error
            raise

    async def find(self, *conditions: ColumnElement[bool]) -> User | None:
        row = (await self.connection.execute(select(*USER_COLUMNS).where(*conditions))).first()
        return None if row is None else User(*row)

    async def get(self, user_id: UUID) -> User | None:
        return await self.find(users.c.id == user_id)

    async def by_email(self, email: str) -> User | None:
        return await self.find(users.c.email == email)

    async def set_password_hash(self, user_id: UUID, password_hash: str, now: datetime) -> None:
        await self.connection.execute(
            update(users)
            .where(users.c.id == user_id)
            .values(password_hash=password_hash, updated_at=now)
        )

    async def emails(self, user_ids: Sequence[UUID]) -> Mapping[UUID, str]:
        statement = select(users.c.id, users.c.email).where(users.c.id.in_(user_ids))
        return {row.id: row.email for row in await self.connection.execute(statement)}


class SqlSessionRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def add(self, session: Session) -> None:
        await self.connection.execute(
            insert(sessions).values(
                id=session.id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                created_at=session.created_at,
                last_seen_at=session.last_seen_at,
            )
        )

    async def by_token_hash(self, token_hash: str) -> Session | None:
        statement = select(
            sessions.c.id,
            sessions.c.user_id,
            sessions.c.token_hash,
            sessions.c.created_at,
            sessions.c.last_seen_at,
        ).where(sessions.c.token_hash == token_hash)
        row = (await self.connection.execute(statement)).first()
        return None if row is None else Session(*row)

    async def touch(self, session_id: UUID, now: datetime) -> None:
        await self.connection.execute(
            update(sessions).where(sessions.c.id == session_id).values(last_seen_at=now)
        )

    async def delete(self, token_hash: str) -> None:
        await self.connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash))

    async def delete_for_user(self, user_id: UUID) -> None:
        await self.connection.execute(delete(sessions).where(sessions.c.user_id == user_id))
