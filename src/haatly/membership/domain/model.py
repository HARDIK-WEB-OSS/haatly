from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

MAX_INVITATION_DAYS = 30


class MembershipLevel(StrEnum):
    CONNECTED = "connected"
    SELLER_ADDED = "seller_added"


class MembershipStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Membership:
    id: UUID
    store_id: UUID
    user_id: UUID
    level: MembershipLevel
    status: MembershipStatus
    created_at: datetime

    @property
    def is_full_member(self) -> bool:
        return self.level is MembershipLevel.SELLER_ADDED and self.status is MembershipStatus.ACTIVE


@dataclass(frozen=True)
class Invitation:
    id: UUID
    store_id: UUID
    created_by_user_id: UUID
    label: str
    expires_at: datetime
    accepted_by_user_id: UUID | None
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    def is_open(self, now: datetime) -> bool:
        return self.accepted_at is None and self.revoked_at is None and now < self.expires_at


def invitation_lifetime(days: int) -> timedelta:
    if not 1 <= days <= MAX_INVITATION_DAYS:
        raise ValueError("invitation lifetime is out of range")
    return timedelta(days=days)
