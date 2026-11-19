from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

from haatly.identity.app.accounts import (
    Accounts,
    InvalidCredentialsError,
    SignedIn,
)
from haatly.identity.app.ports import EmailTakenError
from haatly.identity.domain.model import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
    InvalidEmailError,
    Principal,
    User,
    WeakPasswordError,
)
from haatly.platform.public import ApiError, NonBlank, NulStripped, bounded, strip_nul

SESSION_COOKIE = "haatly_session"
SESSION_MAX_AGE = timedelta(days=14)

EmailText = Annotated[str, NulStripped, bounded(MAX_EMAIL_LENGTH), NonBlank]
PasswordText = Annotated[
    str, BeforeValidator(strip_nul), StringConstraints(max_length=MAX_PASSWORD_LENGTH)
]


def examples(*values: dict[str, Any]) -> ConfigDict:
    return ConfigDict(extra="forbid", json_schema_extra={"examples": list(values)})


class Registration(BaseModel):
    model_config = examples({"email": "{new_email}", "password": "{password}"})
    email: EmailText
    password: PasswordText


class Credentials(BaseModel):
    model_config = examples({"email": "{email}", "password": "{password}"})
    email: EmailText
    password: PasswordText


class PasswordChange(BaseModel):
    model_config = examples({"current_password": "{password}", "new_password": "{new_password}"})
    current_password: PasswordText
    new_password: PasswordText


class UserOut(BaseModel):
    id: UUID
    email: str
    is_admin: bool

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(id=user.id, email=user.email, is_admin=user.is_admin)


def accounts_of(request: Request) -> Accounts:
    accounts: Accounts = request.app.state.accounts
    return accounts


def presented_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE) or None


async def optional_principal(request: Request) -> Principal | None:
    token = presented_token(request)
    return None if token is None else await accounts_of(request).resolve(token)


async def require_principal(
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> Principal:
    if principal is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "not_authenticated", "sign in first")
    return principal


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_MAX_AGE.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def signed_in(response: Response, result: SignedIn) -> UserOut:
    set_session_cookie(response, result.token)
    return UserOut.of(result.user)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


AccountsDep = Annotated[Accounts, Depends(accounts_of)]
PrincipalDep = Annotated[Principal, Depends(require_principal)]
INVALID_INPUT = (InvalidEmailError, WeakPasswordError)

router = APIRouter()


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: Registration, request: Request, response: Response, accounts: AccountsDep
) -> UserOut:
    try:
        result = await accounts.register(body.email, body.password, client_ip(request))
        return signed_in(response, result)
    except INVALID_INPUT as error:
        raise ApiError(422, "validation_error", "email or password is not acceptable") from error
    except EmailTakenError as error:
        raise ApiError(409, "email_taken", "that email is already registered") from error


@router.post("/auth/login")
async def login(
    body: Credentials, request: Request, response: Response, accounts: AccountsDep
) -> UserOut:
    try:
        result = await accounts.login(
            body.email, body.password, client_ip(request), presented_token(request)
        )
    except InvalidCredentialsError as error:
        raise ApiError(401, "invalid_credentials", "email or password is wrong") from error
    return signed_in(response, result)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, accounts: AccountsDep) -> None:
    token = presented_token(request)
    if token is not None:
        await accounts.logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


@router.get("/me")
async def me(principal: PrincipalDep, accounts: AccountsDep) -> UserOut:
    return UserOut.of(await accounts.me(principal))


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange, principal: PrincipalDep, response: Response, accounts: AccountsDep
) -> None:
    try:
        token = await accounts.change_password(principal, body.current_password, body.new_password)
    except InvalidCredentialsError as error:
        raise ApiError(401, "invalid_credentials", "current password is wrong") from error
    except WeakPasswordError as error:
        raise ApiError(422, "validation_error", "new password is not acceptable") from error
    set_session_cookie(response, token)
