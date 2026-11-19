import asyncio
import sys
from collections.abc import Sequence

from haatly.identity.adapters.unit_of_work import SqlIdentityUnitOfWorkFactory
from haatly.identity.app.admins import AdminGrant, AdminGrants, UnknownUserError
from haatly.identity.domain.model import InvalidEmailError
from haatly.platform.public import (
    DbRole,
    Settings,
    SystemClock,
    Uuid7Source,
    create_engine,
    database_url,
)

MESSAGES = {
    AdminGrant.GRANTED: "granted admin to {email}",
    AdminGrant.ALREADY_ADMIN: "{email} is already an admin; nothing changed",
}


async def make_admin(settings: Settings, email: str) -> AdminGrant:
    engine = create_engine(database_url(settings, DbRole.OPS), pool_size=1)
    clock = SystemClock()
    try:
        grants = AdminGrants(SqlIdentityUnitOfWorkFactory(engine), Uuid7Source(clock), clock)
        return await grants.grant(email, DbRole.OPS.value)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        sys.stderr.write("usage: make_admin <email>\n")
        return 2
    [email] = arguments
    try:
        grant = asyncio.run(make_admin(settings or Settings(), email))
    except (UnknownUserError, InvalidEmailError):
        sys.stderr.write(f"no account is registered with {email}\n")
        return 1
    sys.stdout.write(MESSAGES[grant].format(email=email) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
