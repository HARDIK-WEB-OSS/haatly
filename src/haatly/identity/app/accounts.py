from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from haatly.identity.app.ports import (
    EmailTakenError,
    IdentityUnitOfWork,
    IdentityUnitOfWorkFactory,
    PasswordHasher,
    TokenSource,
)
from haatly.identity.domain.model import (
    Principal,
    Session,
    SessionPolicy,
    User,
    check_password,
    hash_token,
    normalize_email,
)
from haatly.platform.kernel import (
    SYSTEM,
    ActorRole,
    Clock,
    IdSource,
    RateLimiter,
    RateLimitRule,
    TenantContext,
)

LOGIN_PER_IP = RateLimitRule("login.ip", 50, timedelta(minutes=15))
LOGIN_PER_EMAIL_AND_IP = RateLimitRule("login.email_ip", 10, timedelta(minutes=15))


class InvalidCredentialsError(Exception):
    pass


class RateLimitedError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(retry_after_seconds)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class SignedIn:
    user: User
    token: str


def context_of(principal: Principal) -> TenantContext:
    role = ActorRole.ADMIN if principal.is_admin else ActorRole.BUYER
    return TenantContext(role, principal.user_id)


@dataclass
class Accounts:
    uow_factory: IdentityUnitOfWorkFactory
    hasher: PasswordHasher
    tokens: TokenSource
    limiter: RateLimiter
    ids: IdSource
    clock: Clock
    policy: SessionPolicy = field(default_factory=SessionPolicy)

    async def open_session(self, uow: IdentityUnitOfWork, user_id: UUID) -> str:
        token = self.tokens.new_token()
        now = self.clock.now()
        await uow.sessions.add(Session(self.ids.new_id(), user_id, hash_token(token), now, now))
        return token

    async def register(self, raw_email: str, password: str) -> SignedIn:
        email = normalize_email(raw_email)
        password_hash = await self.hasher.hash(check_password(password))
        user = User(self.ids.new_id(), email, password_hash, False, self.clock.now())
        async with self.uow_factory(SYSTEM) as uow:
            if await uow.users.by_email(email) is not None:
                raise EmailTakenError
            await uow.users.add(user)
            token = await self.open_session(uow, user.id)
        return SignedIn(user, token)

    async def check_rate(self, email: str, client_ip: str) -> None:
        for rule, subject in (
            (LOGIN_PER_IP, client_ip),
            (LOGIN_PER_EMAIL_AND_IP, f"{email}|{client_ip}"),
        ):
            decision = await self.limiter.hit(rule, subject)
            if not decision.allowed:
                raise RateLimitedError(decision.retry_after_seconds)

    async def authenticate(self, email: str, password: str) -> User:
        async with self.uow_factory(SYSTEM) as uow:
            user = await uow.users.by_email(email)
        if user is None:
            await self.hasher.burn(password)
            raise InvalidCredentialsError
        if not await self.hasher.verify(user.password_hash, password):
            raise InvalidCredentialsError
        return user

    async def login(
        self, raw_email: str, password: str, client_ip: str, presented: str | None
    ) -> SignedIn:
        email = raw_email.strip().lower()
        await self.check_rate(email, client_ip)
        user = await self.authenticate(email, password)
        fresh_hash = None
        if self.hasher.needs_rehash(user.password_hash):
            fresh_hash = await self.hasher.hash(password)
        async with self.uow_factory(SYSTEM) as uow:
            if fresh_hash is not None:
                await uow.users.set_password_hash(user.id, fresh_hash, self.clock.now())
            if presented is not None:
                await uow.sessions.delete(hash_token(presented))
            token = await self.open_session(uow, user.id)
        return SignedIn(user, token)

    async def logout(self, presented: str) -> None:
        async with self.uow_factory(SYSTEM) as uow:
            await uow.sessions.delete(hash_token(presented))

    async def resolve(self, presented: str) -> Principal | None:
        now = self.clock.now()
        async with self.uow_factory(SYSTEM) as uow:
            session = await uow.sessions.by_token_hash(hash_token(presented))
            if session is None or not self.policy.is_live(session, now):
                return None
            user = await uow.users.get(session.user_id)
            if user is None:
                return None
            if self.policy.needs_touch(session, now):
                await uow.sessions.touch(session.id, now)
        return Principal(user.id, user.is_admin)

    async def rotate(self, principal: Principal, presented: str) -> str:
        async with self.uow_factory(SYSTEM) as uow:
            await uow.sessions.delete(hash_token(presented))
            return await self.open_session(uow, principal.user_id)

    async def me(self, principal: Principal) -> User:
        async with self.uow_factory(context_of(principal)) as uow:
            user = await uow.users.get(principal.user_id)
        if user is None:
            raise InvalidCredentialsError
        return user

    async def change_password(self, principal: Principal, current: str, new: str) -> str:
        user = await self.me(principal)
        if not await self.hasher.verify(user.password_hash, current):
            raise InvalidCredentialsError
        password_hash = await self.hasher.hash(check_password(new))
        async with self.uow_factory(context_of(principal)) as uow:
            await uow.users.set_password_hash(user.id, password_hash, self.clock.now())
        async with self.uow_factory(SYSTEM) as uow:
            await uow.sessions.delete_for_user(user.id)
            return await self.open_session(uow, user.id)

    async def emails(self, user_ids: Sequence[UUID]) -> Mapping[UUID, str]:
        async with self.uow_factory(SYSTEM) as uow:
            return await uow.users.emails(user_ids)
