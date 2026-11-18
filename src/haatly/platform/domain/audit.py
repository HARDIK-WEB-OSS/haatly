from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from haatly.platform.domain.jobs import JsonObject
from haatly.platform.domain.tenancy import TenantContext


@dataclass(frozen=True)
class AuditAction:
    action: str
    subject_type: str
    subject_id: UUID
    details: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class AuditEntry:
    id: UUID
    occurred_at: datetime
    actor: TenantContext
    what: AuditAction
