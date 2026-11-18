from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from haatly.platform.public import DbRole, Environment, Settings, create_engine, database_url
from haatly.platform.public import router as platform_router


def create_app(settings: Settings | None = None) -> FastAPI:
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
    app.include_router(platform_router)
    return app
