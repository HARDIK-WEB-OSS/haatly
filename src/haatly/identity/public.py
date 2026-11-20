from haatly.identity.adapters.make_admin import main as make_admin_main
from haatly.identity.adapters.make_admin import make_admin
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
from haatly.identity.adapters.sql_identity import SqlSessionRepository
from haatly.identity.adapters.tables import metadata as identity_metadata
from haatly.identity.adapters.unit_of_work import (
    SqlIdentityUnitOfWork,
    SqlIdentityUnitOfWorkFactory,
)
from haatly.identity.app.accounts import (
    Accounts,
    InvalidCredentialsError,
    context_of,
)
from haatly.identity.app.admins import AdminGrant, AdminGrants, UnknownUserError
from haatly.identity.app.ports import EmailTakenError, PasswordHasher
from haatly.identity.domain.model import Principal, Session, SessionPolicy, User, hash_token

__all__ = [
    "SESSION_COOKIE",
    "Accounts",
    "AdminGrant",
    "AdminGrants",
    "Argon2idHasher",
    "EmailTakenError",
    "InvalidCredentialsError",
    "PasswordHasher",
    "Principal",
    "PrincipalDep",
    "SecretTokens",
    "Session",
    "SessionPolicy",
    "SqlIdentityUnitOfWork",
    "SqlIdentityUnitOfWorkFactory",
    "SqlSessionRepository",
    "UnknownUserError",
    "User",
    "accounts_of",
    "context_of",
    "hash_token",
    "identity_metadata",
    "identity_router",
    "make_admin",
    "make_admin_main",
    "optional_principal",
    "presented_token",
    "require_principal",
    "set_session_cookie",
]
