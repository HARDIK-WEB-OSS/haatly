import asyncio
from datetime import timedelta

import httpx
import pytest
from argon2 import PasswordHasher, Type
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.identity.public import (
    SESSION_COOKIE,
    Accounts,
    Argon2idHasher,
    InvalidCredentialsError,
    SecretTokens,
    SqlIdentityUnitOfWorkFactory,
)
from haatly.platform.public import (
    FrozenClock,
    RateLimitedError,
    RateLimiter,
    SqlUnitOfWorkFactory,
    Uuid7Source,
)
from tests.world import PASSWORD, Api, unique

pytestmark = pytest.mark.anyio


def new_email() -> str:
    return f"{unique('person')}@example.test"


async def stored_hash(engine: AsyncEngine, email: str) -> str:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.role', 'system', true)"))
        sql = text("SELECT password_hash FROM identity.users WHERE email = :email")
        return str((await connection.execute(sql, {"email": email})).scalar_one())


def session_of(response: httpx.Response) -> str:
    return response.cookies[SESSION_COOKIE]


async def register(api: Api, email: str, password: str = PASSWORD) -> str:
    response = await api.call(
        "POST", "/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    return session_of(response)


def accounts(
    engine: AsyncEngine, clock: FrozenClock, hasher: Argon2idHasher | None = None
) -> Accounts:
    limiter = RateLimiter(SqlUnitOfWorkFactory(engine), clock)
    return Accounts(
        SqlIdentityUnitOfWorkFactory(engine),
        hasher or Argon2idHasher(),
        SecretTokens(),
        limiter,
        Uuid7Source(clock),
        clock,
    )


async def test_registration_stores_an_argon2id_hash_and_sets_a_strict_cookie(
    api: Api, app_engine: AsyncEngine
) -> None:
    email = new_email()
    response = await api.call(
        "POST", "/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201
    assert response.json()["email"] == email
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=lax", "path=/"))
    assert (await stored_hash(app_engine, email)).startswith("$argon2id$")
    assert response.headers["x-request-id"]


async def test_the_session_cookie_identifies_the_user(api: Api) -> None:
    email = new_email()
    token = await register(api, email)
    assert (await api.call("GET", "/v1/me", token=token)).json()["email"] == email
    assert (await api.call("GET", "/v1/me")).status_code == 401
    assert (await api.call("GET", "/v1/me", token="forged")).status_code == 401


@pytest.mark.parametrize(
    ("email", "password"),
    [("not-an-email", PASSWORD), ("short@example.test", "tiny"), ("", PASSWORD)],
)
async def test_unacceptable_registrations_are_refused(api: Api, email: str, password: str) -> None:
    response = await api.call(
        "POST", "/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_nul_bytes_are_stripped_and_emails_are_normalised(api: Api) -> None:
    email = new_email()
    token = await register(api, f"  {email.upper()}\x00 ")
    assert (await api.call("GET", "/v1/me", token=token)).json()["email"] == email


async def test_an_email_can_register_only_once_even_concurrently(api: Api) -> None:
    email = new_email()
    body = {"email": email, "password": PASSWORD}
    statuses = sorted(
        response.status_code
        for response in await asyncio.gather(
            *(api.call("POST", "/v1/auth/register", json=body) for _ in range(5))
        )
    )
    assert statuses == [201, 409, 409, 409, 409]


async def test_login_rotates_the_session_and_logout_ends_it(api: Api) -> None:
    email = new_email()
    first = await register(api, email)
    wrong = await api.call(
        "POST", "/v1/auth/login", json={"email": email, "password": "wrong-password"}
    )
    assert wrong.status_code == 401
    unknown = await api.call(
        "POST", "/v1/auth/login", json={"email": new_email(), "password": PASSWORD}
    )
    assert unknown.status_code == 401
    login = await api.call(
        "POST", "/v1/auth/login", token=first, json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200
    second = session_of(login)
    assert second != first
    assert (await api.call("GET", "/v1/me", token=first)).status_code == 401
    assert (await api.call("POST", "/v1/auth/logout", token=second)).status_code == 204
    assert (await api.call("GET", "/v1/me", token=second)).status_code == 401
    assert (await api.call("POST", "/v1/auth/logout")).status_code == 204


async def test_a_password_change_revokes_every_session(api: Api) -> None:
    email = new_email()
    first = await register(api, email)
    login = await api.call("POST", "/v1/auth/login", json={"email": email, "password": PASSWORD})
    second = session_of(login)
    body = {"current_password": PASSWORD, "new_password": "a-much-better-passphrase"}
    bad = await api.call(
        "POST", "/v1/me/password", token=first, json=body | {"current_password": "nope-nope-nope"}
    )
    assert bad.status_code == 401
    weak = await api.call(
        "POST", "/v1/me/password", token=first, json=body | {"new_password": "short"}
    )
    assert weak.status_code == 422
    changed = await api.call("POST", "/v1/me/password", token=first, json=body)
    assert changed.status_code == 204
    fresh = session_of(changed)
    statuses = [
        (await api.call("GET", "/v1/me", token=t)).status_code for t in (first, second, fresh)
    ]
    assert statuses == [401, 401, 200]


async def test_login_rehashes_a_hash_made_with_weaker_parameters(
    app_engine: AsyncEngine,
) -> None:
    clock = FrozenClock()
    weak = Argon2idHasher(PasswordHasher(time_cost=1, memory_cost=8192, type=Type.ID))
    email = new_email()
    await accounts(app_engine, clock, weak).register(email, PASSWORD, "10.0.0.9")
    old_hash = await stored_hash(app_engine, email)
    strong = Argon2idHasher()
    assert strong.needs_rehash(old_hash)
    await accounts(app_engine, clock, strong).login(email, PASSWORD, "10.0.0.1", None)
    new_hash = await stored_hash(app_engine, email)
    assert new_hash != old_hash
    assert new_hash.startswith("$argon2id$")
    assert not strong.needs_rehash(new_hash)


async def test_login_limits_apply_per_email_and_ip_without_locking_the_account(
    app_engine: AsyncEngine,
) -> None:
    clock = FrozenClock()
    service = accounts(app_engine, clock)
    email = new_email()
    await service.register(email, PASSWORD, "10.0.0.9")
    for _ in range(10):
        with pytest.raises(InvalidCredentialsError):
            await service.login(email, "wrong-password", "10.0.0.1", None)
    with pytest.raises(RateLimitedError) as limited:
        await service.login(email, PASSWORD, "10.0.0.1", None)
    assert limited.value.retry_after_seconds > 0
    assert (await service.login(email, PASSWORD, "10.0.0.2", None)).user.email == email
    clock.advance(timedelta(minutes=15))
    assert (await service.login(email, PASSWORD, "10.0.0.1", None)).user.email == email


async def test_the_per_ip_limit_spans_emails(api: Api) -> None:
    for attempt in range(50):
        response = await api.call(
            "POST",
            "/v1/auth/login",
            json={"email": f"nobody{attempt}@example.test", "password": PASSWORD},
        )
        assert response.status_code == 401
    limited = await api.call(
        "POST", "/v1/auth/login", json={"email": new_email(), "password": PASSWORD}
    )
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


async def test_sessions_expire_after_idling_and_at_their_absolute_limit(
    app_engine: AsyncEngine,
) -> None:
    clock = FrozenClock()
    service = accounts(app_engine, clock)
    signed_in = await service.register(new_email(), PASSWORD, "10.0.0.9")
    clock.advance(timedelta(hours=23))
    assert await service.resolve(signed_in.token) is not None
    clock.advance(timedelta(hours=23))
    assert await service.resolve(signed_in.token) is not None
    clock.advance(timedelta(hours=24, seconds=1))
    assert await service.resolve(signed_in.token) is None
    busy = await service.register(new_email(), PASSWORD, "10.0.0.9")
    for _ in range(14):
        assert await service.resolve(busy.token) is not None
        clock.advance(timedelta(hours=23))
    clock.advance(timedelta(days=1))
    assert await service.resolve(busy.token) is None


class CrashError(Exception):
    pass


async def crash(*_: object) -> None:
    raise CrashError


class CrashingTokens(SecretTokens):
    def __init__(self) -> None:
        self.armed = False

    def new_token(self) -> str:
        if self.armed:
            raise CrashError
        return super().new_token()


async def two_sessions(service: Accounts, email: str) -> tuple[str, str]:
    first = (await service.register(email, PASSWORD, "10.0.0.9")).token
    second = (await service.login(email, PASSWORD, "10.0.0.1", None)).token
    return first, second


async def assert_nothing_changed(
    service: Accounts, engine: AsyncEngine, email: str, old_hash: str, tokens: tuple[str, ...]
) -> None:
    assert await stored_hash(engine, email) == old_hash
    for token in tokens:
        assert await service.resolve(token) is not None
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(email, "a-much-better-passphrase")
    assert (await service.authenticate(email, PASSWORD)).email == email


async def test_a_crash_before_revoking_sessions_leaves_the_old_password_and_sessions(
    app_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = accounts(app_engine, FrozenClock())
    email = new_email()
    tokens = await two_sessions(service, email)
    old_hash = await stored_hash(app_engine, email)
    principal = await service.resolve(tokens[0])
    assert principal is not None
    target = "haatly.identity.adapters.sql_identity.SqlSessionRepository.delete_for_user"
    monkeypatch.setattr(target, crash)
    with pytest.raises(CrashError):
        await service.change_password(principal, PASSWORD, "a-much-better-passphrase")
    monkeypatch.undo()
    await assert_nothing_changed(service, app_engine, email, old_hash, tokens)


async def test_a_crash_before_the_new_session_leaves_the_old_password_and_sessions(
    app_engine: AsyncEngine,
) -> None:
    clock = FrozenClock()
    tokens_source = CrashingTokens()
    service = accounts(app_engine, clock)
    service.tokens = tokens_source
    email = new_email()
    tokens = await two_sessions(service, email)
    old_hash = await stored_hash(app_engine, email)
    principal = await service.resolve(tokens[0])
    assert principal is not None
    tokens_source.armed = True
    with pytest.raises(CrashError):
        await service.change_password(principal, PASSWORD, "a-much-better-passphrase")
    tokens_source.armed = False
    await assert_nothing_changed(service, app_engine, email, old_hash, tokens)


async def test_registration_is_limited_to_ten_an_hour_per_ip(app_engine: AsyncEngine) -> None:
    clock = FrozenClock()
    service = accounts(app_engine, clock)
    for _ in range(10):
        await service.register(new_email(), PASSWORD, "10.0.0.1")
    with pytest.raises(RateLimitedError) as limited:
        await service.register(new_email(), PASSWORD, "10.0.0.1")
    assert 0 < limited.value.retry_after_seconds <= 3600
    assert (await service.register(new_email(), PASSWORD, "10.0.0.2")).user.email
    clock.advance(timedelta(hours=1))
    assert (await service.register(new_email(), PASSWORD, "10.0.0.1")).user.email


async def test_a_refused_registration_still_counts_towards_the_limit(api: Api) -> None:
    taken = new_email()
    await register(api, taken)
    body = {"email": taken, "password": PASSWORD}
    statuses = [
        (await api.call("POST", "/v1/auth/register", json=body)).status_code for _ in range(9)
    ]
    assert statuses == [409] * 9
    limited = await api.call("POST", "/v1/auth/register", json=body | {"email": new_email()})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) > 0
