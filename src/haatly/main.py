from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.identity.public import (
    Accounts,
    Argon2idHasher,
    PasswordHasher,
    SecretTokens,
    SqlIdentityUnitOfWorkFactory,
    identity_router,
)
from haatly.membership.public import (
    Memberships,
    SqlMembershipUnitOfWorkFactory,
    membership_router,
)
from haatly.platform.public import (
    DbRole,
    Environment,
    OriginCheckMiddleware,
    RateLimiter,
    RequestIdMiddleware,
    Settings,
    SqlUnitOfWorkFactory,
    SystemClock,
    Uuid7Source,
    create_engine,
    database_url,
    register_error_handlers,
)
from haatly.platform.public import router as platform_router
from haatly.stores.public import SqlStoresUnitOfWorkFactory, Stores, stores_router


def attach_services(app: FastAPI, engine: AsyncEngine, hasher: PasswordHasher) -> None:
    clock = SystemClock()
    ids = Uuid7Source(clock)
    tokens = SecretTokens()
    limiter = RateLimiter(SqlUnitOfWorkFactory(engine), clock)
    app.state.accounts = Accounts(
        SqlIdentityUnitOfWorkFactory(engine), hasher, tokens, limiter, ids, clock
    )
    app.state.stores = Stores(SqlStoresUnitOfWorkFactory(engine), ids, clock)
    app.state.memberships = Memberships(SqlMembershipUnitOfWorkFactory(engine), tokens, ids, clock)


def create_app(settings: Settings | None = None, hasher: PasswordHasher | None = None) -> FastAPI:
    resolved = settings or Settings()
    engine = create_engine(database_url(resolved, DbRole.APP))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    exposes_docs = resolved.environment is not Environment.PRODUCTION
    app = FastAPI(
        title="Haatly",
        lifespan=lifespan,
        docs_url="/docs" if exposes_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if exposes_docs else None,
    )
    app.state.engine = engine
    app.state.settings = resolved
    attach_services(app, engine, hasher or Argon2idHasher())
    register_error_handlers(app)
    app.add_middleware(OriginCheckMiddleware, allowed_origin=resolved.public_origin)
    app.add_middleware(RequestIdMiddleware)
    for router in (platform_router, identity_router, stores_router, membership_router):
        app.include_router(router)
    return app
