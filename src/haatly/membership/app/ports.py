from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from haatly.membership.domain.model import Invitation, Membership, MembershipStatus
from haatly.platform.kernel import TenantContext, UnitOfWork


class MembershipRepository(Protocol):
    async def of_user(self, store_id: UUID, user_id: UUID) -> Membership | None: ...

    async def request(self, membership: Membership, now: datetime) -> Membership | None: ...

    async def join_by_invitation(
        self, membership: Membership, invited_by: UUID, now: datetime
    ) -> Membership | None: ...

    async def for_user(self, user_id: UUID) -> Sequence[Membership]: ...

    async def for_store(self, store_id: UUID) -> Sequence[Membership]: ...

    async def set_status(
        self,
        membership_id: UUID,
        allowed_from: Sequence[MembershipStatus],
        status: MembershipStatus,
        now: datetime,
    ) -> Membership | None: ...


class InvitationRepository(Protocol):
    async def add(self, invitation: Invitation, token_hash: str) -> None: ...

    async def present(self, token_hash: str) -> Invitation | None: ...

    async def claim(self, invitation_id: UUID, user_id: UUID, now: datetime) -> bool: ...

    async def for_store(self, store_id: UUID) -> Sequence[Invitation]: ...

    async def revoke(self, invitation_id: UUID, now: datetime) -> Invitation | None: ...


class TokenGenerator(Protocol):
    def new_token(self) -> str: ...


class StoreGate(Protocol):
    async def accepts_members(self, store_id: UUID) -> bool: ...

    async def is_live(self, store_id: UUID) -> bool: ...

    async def owner_of(self, store_id: UUID) -> UUID | None: ...


class MembershipUnitOfWork(UnitOfWork, Protocol):
    @property
    def memberships(self) -> MembershipRepository: ...

    @property
    def invitations(self) -> InvitationRepository: ...

    @property
    def store_gate(self) -> StoreGate: ...

    async def __aenter__(self) -> Self: ...


class MembershipUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> MembershipUnitOfWork: ...
