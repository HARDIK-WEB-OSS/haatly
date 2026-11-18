from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.platform.adapters.tables import audit_log, rate_limit_counters
from haatly.platform.domain.audit import AuditEntry


class SqlAuditRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def append(self, entry: AuditEntry) -> None:
        await self.connection.execute(
            insert(audit_log).values(
                id=entry.id,
                occurred_at=entry.occurred_at,
                actor_role=entry.actor.role.value,
                actor_user_id=entry.actor.user_id,
                action=entry.what.action,
                subject_type=entry.what.subject_type,
                subject_id=entry.what.subject_id,
                details=dict(entry.what.details),
            )
        )


class SqlRateLimitRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def hit(self, key: str, window_start: datetime) -> int:
        statement = insert(rate_limit_counters).values(key=key, window_start=window_start, count=1)
        statement = statement.on_conflict_do_update(
            index_elements=[rate_limit_counters.c.key, rate_limit_counters.c.window_start],
            set_={"count": rate_limit_counters.c.count + 1},
        )
        result = await self.connection.execute(statement.returning(rate_limit_counters.c.count))
        return int(result.scalar_one())
