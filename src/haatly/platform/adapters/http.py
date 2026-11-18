import logging
import secrets
from collections.abc import Collection
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BeforeValidator, StringConstraints
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("haatly.http")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
REQUEST_ID_HEADER = "X-Request-ID"
CODES_BY_STATUS = {
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def strip_nul(value: object) -> object:
    return value.replace("\x00", "") if isinstance(value, str) else value


def require_content(value: str) -> str:
    if not value:
        raise ValueError("must not be blank")
    return value


NulStripped = BeforeValidator(strip_nul)
NonBlank = AfterValidator(require_content)


def bounded(max_length: int) -> StringConstraints:
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def error_body(request: Request, code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "request_id": request_id_of(request)}}


def error_response(
    request: Request, status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(request, code, message), status_code=status, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(request, error.status, error.code, error.message, error.headers)

    @app.exception_handler(HTTPException)
    async def on_http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = CODES_BY_STATUS.get(error.status_code, "http_error")
        return error_response(request, error.status_code, code, code.replace("_", " "))

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        return error_response(request, 422, "validation_error", f"invalid: {', '.join(fields)}")

    @app.exception_handler(Exception)
    async def on_unexpected_error(request: Request, _: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"request_id": request_id_of(request)})
        return error_response(request, 500, "internal_error", "something went wrong")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(16)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)


def origin_of(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


class OriginCheckMiddleware:
    def __init__(
        self, app: ASGIApp, allowed_origin: str, exempt_paths: Collection[str] = ()
    ) -> None:
        self.app = app
        self.allowed_origin = allowed_origin
        self.exempt_paths = frozenset(exempt_paths)

    def allows(self, scope: Scope) -> bool:
        if scope["method"] not in UNSAFE_METHODS or scope["path"] in self.exempt_paths:
            return True
        headers = Headers(scope=scope)
        origin = headers.get("origin") or origin_of(headers.get("referer"))
        return origin == self.allowed_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.allows(scope):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        response = error_response(request, 403, "origin_not_allowed", "cross-site request refused")
        await response(scope, receive, send)
