import asyncio
import socket
import time
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
async def client_for(
    settings: Settings, ledger: LedgerSettings | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, ledger=ledger)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client


def unreachable_ledger() -> LedgerSettings:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return LedgerSettings(
        tigerbeetle_addresses=f"127.0.0.1:{port}", tigerbeetle_timeout_seconds=0.5
    )


async def test_health_and_ready(migrated: Settings, ledger_settings: LedgerSettings) -> None:
    async with client_for(migrated, ledger_settings) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        ready = await client.get("/ready")
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})


async def test_ready_reports_an_unreachable_database(
    migrated: Settings, ledger_settings: LedgerSettings
) -> None:
    broken = migrated.model_copy(update={"db_app_password": SecretStr("wrong")})
    async with client_for(broken, ledger_settings) as client:
        ready = await client.get("/ready")
    assert (ready.status_code, ready.json()) == (503, {"status": "database unavailable"})


async def test_ready_reports_an_unreachable_ledger(migrated: Settings) -> None:
    async with client_for(migrated, unreachable_ledger()) as client:
        started = time.monotonic()
        ready = await client.get("/ready")
        elapsed = time.monotonic() - started
    assert (ready.status_code, ready.json()) == (503, {"status": "ledger unavailable"})
    assert elapsed < 5


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.PRODUCTION,
        "db_owner_password": SecretStr("o" * 32),
        "db_app_password": SecretStr("a" * 32),
        "public_origin": "https://haatly.example",
        "trusted_proxies": "172.30.53.2",
    }
    return Settings.model_validate(values | overrides)


async def test_production_hides_interactive_docs() -> None:
    async with client_for(production_settings(), unreachable_ledger()) as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"db_app_password": SecretStr("short")}, "production secrets are too short"),
        ({"public_origin": "http://haatly.example"}, "production origin must be https"),
        ({"trusted_proxies": ""}, "production needs HAATLY_TRUSTED_PROXIES"),
        ({"trusted_proxies": " , "}, "production needs HAATLY_TRUSTED_PROXIES"),
    ],
)
def test_production_refuses_weak_settings(overrides: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        production_settings(**overrides)


def test_the_api_refuses_to_start_in_production_without_trusted_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "HAATLY_ENVIRONMENT": "production",
        "HAATLY_DB_OWNER_PASSWORD": "o" * 32,
        "HAATLY_DB_APP_PASSWORD": "a" * 32,
        "HAATLY_PUBLIC_ORIGIN": "https://haatly.example",
        "HAATLY_TRUSTED_PROXIES": "",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match="production needs HAATLY_TRUSTED_PROXIES"):
        create_app()


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
