from haatly.identity.adapters.routes import (
    SESSION_COOKIE,
    PrincipalDep,
    accounts_of,
    optional_principal,
    presented_token,
    require_principal,
    set_session_cookie,
)
from haatly.identity.adapters.routes import router as identity_router
from haatly.identity.adapters.security import Argon2idHasher, SecretTokens
from haatly.identity.adapters.tables import metadata as identity_metadata
from haatly.identity.adapters.unit_of_work import (
    SqlIdentityUnitOfWork,
    SqlIdentityUnitOfWorkFactory,
)
from haatly.identity.app.accounts import (
    Accounts,
    InvalidCredentialsError,
    RateLimitedError,
    context_of,
)
from haatly.identity.app.ports import EmailTakenError, PasswordHasher
from haatly.identity.domain.model import Principal, Session, SessionPolicy, User, hash_token

__all__ = [
    "SESSION_COOKIE",
    "Accounts",
    "Argon2idHasher",
    "EmailTakenError",
    "InvalidCredentialsError",
    "PasswordHasher",
    "Principal",
    "PrincipalDep",
    "RateLimitedError",
    "SecretTokens",
    "Session",
    "SessionPolicy",
    "SqlIdentityUnitOfWork",
    "SqlIdentityUnitOfWorkFactory",
    "User",
    "accounts_of",
    "context_of",
    "hash_token",
    "identity_metadata",
    "identity_router",
    "optional_principal",
    "presented_token",
    "require_principal",
    "set_session_cookie",
]
