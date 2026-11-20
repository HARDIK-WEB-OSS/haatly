from datetime import datetime

from haatly.identity.app.ports import SessionRepository
from haatly.identity.domain.model import Principal, SessionPolicy, hash_token


async def resolve_session(
    sessions: SessionRepository, presented: str, now: datetime, policy: SessionPolicy
) -> Principal | None:
    found = await sessions.with_user(hash_token(presented))
    if found is None or not policy.is_live(found.session, now):
        return None
    if policy.needs_touch(found.session, now):
        await sessions.touch(found.session.id, now)
    return Principal(found.session.user_id, found.is_admin)
