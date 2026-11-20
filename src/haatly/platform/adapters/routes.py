from collections.abc import Awaitable, Callable

import asyncpg
from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter()

ReadinessProbe = Callable[[], Awaitable[bool]]


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
async def ready(request: Request, response: Response) -> dict[str, str]:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, asyncpg.PostgresError, OSError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database unavailable"}
    probes: dict[str, ReadinessProbe] = request.app.state.readiness_probes
    for name, probe in probes.items():
        if not await probe():
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": f"{name} unavailable"}
    return {"status": "ready"}
