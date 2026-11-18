import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from haatly.ledger.public import LedgerSettings
from haatly.main import create_app
from haatly.platform.public import Environment, Settings
from haatly.worker import built_worker

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        await app.state.engine.dispose()


async def test_health_and_ready(migrated: Settings) -> None:
    async with client_for(migrated) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        ready = await client.get("/ready")
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})


async def test_ready_reports_an_unreachable_database(migrated: Settings) -> None:
    broken = migrated.model_copy(update={"db_app_password": SecretStr("wrong")})
    async with client_for(broken) as client:
        ready = await client.get("/ready")
    assert (ready.status_code, ready.json()) == (503, {"status": "database unavailable"})


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.PRODUCTION,
        "db_owner_password": SecretStr("o" * 32),
        "db_app_password": SecretStr("a" * 32),
        "public_origin": "https://haatly.example",
    }
    return Settings.model_validate(values | overrides)


async def test_production_hides_interactive_docs() -> None:
    async with client_for(production_settings()) as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"db_app_password": SecretStr("short")}, "production secrets are too short"),
        ({"public_origin": "http://haatly.example"}, "production origin must be https"),
    ],
)
def test_production_refuses_weak_settings(overrides: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        production_settings(**overrides)


async def test_worker_runs_until_stopped(
    migrated: Settings, ledger_settings: LedgerSettings
) -> None:
    quick = migrated.model_copy(update={"worker_idle_seconds": 0.05})
    async with built_worker(quick, ledger_settings) as worker:
        assert await worker.run_once() == 2
        stop = asyncio.Event()
        running = asyncio.create_task(worker.run(stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.wait_for(running, timeout=5)
