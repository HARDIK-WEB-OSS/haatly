from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from haatly.platform.public import (
    ApiError,
    NonBlank,
    NulStripped,
    OriginCheckMiddleware,
    RequestIdMiddleware,
    bounded,
    register_error_handlers,
)

pytestmark = pytest.mark.anyio

ORIGIN = "https://haatly.example"


class Note(BaseModel):
    text: Annotated[str, NulStripped, bounded(5), NonBlank]


def build_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(OriginCheckMiddleware, allowed_origin=ORIGIN, exempt_paths=["/hooks"])
    app.add_middleware(RequestIdMiddleware)

    @app.post("/notes")
    async def add_note(note: Note) -> Note:
        return note

    @app.post("/hooks")
    async def hook() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/teapot")
    async def teapot() -> None:
        raise ApiError(418, "teapot", "short and stout", {"X-Kind": "tea"})

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail")

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as opened:
        yield opened


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": ORIGIN}, 200),
        ({"Referer": f"{ORIGIN}/stores/1"}, 200),
        ({}, 403),
        ({"Origin": "https://evil.example"}, 403),
        ({"Referer": "not a url"}, 403),
        ({"Origin": "null"}, 403),
    ],
)
async def test_unsafe_requests_need_our_origin(
    client: httpx.AsyncClient, headers: dict[str, str], status: int
) -> None:
    response = await client.post("/notes", json={"text": "hi"}, headers=headers)
    assert response.status_code == status
    if status == 403:
        assert response.json()["error"]["code"] == "origin_not_allowed"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_exempt_paths_and_safe_methods_skip_the_origin_check(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.post("/hooks")).status_code == 200
    assert (await client.get("/teapot")).status_code == 418


async def test_every_error_has_one_shape(client: httpx.AsyncClient) -> None:
    teapot = await client.get("/teapot")
    assert teapot.json()["error"]["code"] == "teapot"
    assert teapot.headers["x-kind"] == "tea"
    missing = await client.get("/nowhere")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")
    wrong_method = await client.delete("/teapot", headers={"Origin": ORIGIN})
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"
    invalid = await client.post("/notes", json={"text": "x" * 6}, headers={"Origin": ORIGIN})
    assert invalid.json()["error"] == {
        "code": "validation_error",
        "message": "invalid: body.text",
        "request_id": invalid.headers["x-request-id"],
    }


async def test_unexpected_errors_hide_their_details(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret" not in response.text


@pytest.mark.parametrize(("raw", "kept"), [("a\x00b", "ab"), ("  ok ", "ok")])
def test_text_is_stripped_of_nul_and_whitespace(raw: str, kept: str) -> None:
    assert Note(text=raw).text == kept


@pytest.mark.parametrize("raw", ["", "\x00", " \x00 ", "toolong"])
def test_blank_or_long_text_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="text"):
        Note(text=raw)
