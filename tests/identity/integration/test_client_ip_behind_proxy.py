import json
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.main import create_app
from haatly.platform.public import Settings
from tests.world import PASSWORD, unique

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_SECRETS = {
    "POSTGRES_SUPERUSER_PASSWORD": "unused",
    "HAATLY_DB_OWNER_PASSWORD": "unused",
    "HAATLY_DB_APP_PASSWORD": "unused",
    "HAATLY_DB_OPS_PASSWORD": "unused",
}
CADDY = "172.30.53.2"
OUTSIDER = "198.51.100.7"
ATTACKER = "203.0.113.66"
VICTIM = "203.0.113.77"


@pytest.fixture(scope="module")
def compose(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    empty_env = tmp_path_factory.mktemp("compose") / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    docker = shutil.which("docker")
    assert docker is not None
    arguments = ["--env-file", str(empty_env), "--profile", "app", "config", "--format", "json"]
    result = subprocess.run(  # noqa: S603
        [docker, "compose", *arguments],
        cwd=REPO_ROOT,
        env=os.environ | COMPOSE_SECRETS,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    config: dict[str, Any] = json.loads(result.stdout)
    return config


def trusted_proxies(compose: dict[str, Any]) -> str:
    api = compose["services"]["api"]
    assert "--no-proxy-headers" in api["command"]
    value: str = api["environment"]["HAATLY_TRUSTED_PROXIES"]
    return value


@dataclass
class Edge:
    app: Any
    origin: str

    async def post(
        self, path: str, body: dict[str, str], peer: str, forwarded_for: str | None
    ) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app, client=(peer, 40000))
        headers = {"Origin": self.origin}
        if forwarded_for is not None:
            headers["X-Forwarded-For"] = forwarded_for
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
            return await client.post(path, json=body, headers=headers)

    async def login(
        self, email: str, password: str, peer: str, forwarded_for: str | None
    ) -> httpx.Response:
        body = {"email": email, "password": password}
        return await self.post("/v1/auth/login", body, peer, forwarded_for)


@pytest.fixture
async def edge(
    migrated: Settings, app_engine: AsyncEngine, compose: dict[str, Any]
) -> AsyncIterator[Edge]:
    configured = migrated.model_copy(update={"trusted_proxies": trusted_proxies(compose)})
    app = create_app(configured)
    try:
        yield Edge(app, migrated.public_origin)
    finally:
        await app.state.engine.dispose()


async def registered(edge: Edge) -> str:
    email = f"{unique('victim')}@example.test"
    body = {"email": email, "password": PASSWORD}
    response = await edge.post("/v1/auth/register", body, CADDY, "192.0.2.1")
    assert response.status_code == 201, response.text
    return email


def test_the_api_trusts_only_caddy_on_its_own_network(compose: dict[str, Any]) -> None:
    services = compose["services"]
    assert trusted_proxies(compose) == CADDY
    assert services["caddy"]["networks"]["edge"]["ipv4_address"] == CADDY
    [pool] = compose["networks"]["edge"]["ipam"]["config"]
    assert ip_address(CADDY) in ip_network(pool["subnet"])
    assert ip_address(CADDY) not in ip_network(pool["ip_range"])
    assert {name for name, spec in services.items() if "edge" in spec.get("networks", {})} == {
        "caddy",
        "api",
    }
    assert list(services["caddy"]["networks"]) == ["edge"]


async def test_limits_apply_per_real_client_ip_through_a_trusted_proxy(edge: Edge) -> None:
    for attempt in range(50):
        email = f"nobody{attempt}@example.test"
        response = await edge.login(email, PASSWORD, CADDY, ATTACKER)
        assert response.status_code == 401
    limited = await edge.login("nobody@example.test", PASSWORD, CADDY, ATTACKER)
    assert limited.status_code == 429
    elsewhere = await edge.login("nobody@example.test", PASSWORD, CADDY, VICTIM)
    assert elsewhere.status_code == 401


async def test_forwarded_for_from_an_untrusted_peer_is_ignored(edge: Edge) -> None:
    email = await registered(edge)
    for attempt in range(10):
        response = await edge.login(email, "wrong-password", OUTSIDER, f"192.0.2.{attempt + 10}")
        assert response.status_code == 401
    spoofed = await edge.login(email, PASSWORD, OUTSIDER, "192.0.2.200")
    assert spoofed.status_code == 429


async def test_bad_logins_from_one_ip_do_not_lock_the_victim_out(edge: Edge) -> None:
    email = await registered(edge)
    for _ in range(10):
        response = await edge.login(email, "wrong-password", CADDY, ATTACKER)
        assert response.status_code == 401
    assert (await edge.login(email, "wrong-password", CADDY, ATTACKER)).status_code == 429
    victim = await edge.login(email, PASSWORD, CADDY, VICTIM)
    assert victim.status_code == 200, victim.text


@pytest.mark.parametrize("value", ["caddy", "172.30.53.300", "10.0.0.0/33", "172.30.53.2,nope"])
def test_trusted_proxies_must_be_addresses_or_networks(value: str) -> None:
    with pytest.raises(ValidationError, match="not an IP address or network"):
        Settings.model_validate(
            {"db_owner_password": "x", "db_app_password": "x", "trusted_proxies": value}
        )


async def test_without_trusted_proxies_forwarded_for_is_never_believed(
    migrated: Settings, app_engine: AsyncEngine
) -> None:
    app = create_app(migrated.model_copy(update={"trusted_proxies": ""}))
    try:
        edge = Edge(app, migrated.public_origin)
        email = await registered(edge)
        for attempt in range(10):
            response = await edge.login(email, "wrong-password", CADDY, f"192.0.2.{attempt + 10}")
            assert response.status_code == 401
        assert (await edge.login(email, PASSWORD, CADDY, VICTIM)).status_code == 429
    finally:
        await app.state.engine.dispose()
