import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from haatly.membership.app.ports import (
    MembershipUnitOfWork,
    MembershipUnitOfWorkFactory,
    TokenGenerator,
)
from haatly.membership.domain.model import (
    Invitation,
    Membership,
    MembershipLevel,
    MembershipStatus,
    invitation_lifetime,
)
from haatly.platform.kernel import (
    ActorRole,
    Clock,
    IdSource,
    RateLimiter,
    RateLimitRule,
    TenantContext,
)

JOIN_PER_USER = RateLimitRule("join.user", 20, timedelta(hours=1))
INVITE_PER_STORE = RateLimitRule("invite.store", 50, timedelta(days=1))


class MembershipNotFoundError(LookupError):
    pass


class InvalidInvitationError(LookupError):
    pass


class AlreadyMemberError(Exception):
    pass


class JoinRefusedError(Exception):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_user(context: TenantContext) -> UUID:
    if context.user_id is None:
        raise JoinRefusedError
    return context.user_id


def require_store(context: TenantContext) -> UUID:
    if context.role is not ActorRole.SELLER or context.store_id is None:
        raise MembershipNotFoundError
    return context.store_id


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: Invitation
    token: str


@dataclass
class Memberships:
    uow_factory: MembershipUnitOfWorkFactory
    tokens: TokenGenerator
    ids: IdSource
    clock: Clock
    limiter: RateLimiter

    def new_membership(
        self, store_id: UUID, user_id: UUID, level: MembershipLevel, status: MembershipStatus
    ) -> Membership:
        return Membership(self.ids.new_id(), store_id, user_id, level, status, self.clock.now())

    async def request_join(self, context: TenantContext, store_id: UUID) -> Membership:
        user_id = require_user(context)
        await self.limiter.enforce(JOIN_PER_USER, str(user_id))
        async with self.uow_factory(context) as uow:
            if not await uow.store_gate.is_live(store_id):
                raise MembershipNotFoundError
            if await uow.store_gate.owner_of(store_id) == user_id:
                raise JoinRefusedError
            existing = await uow.memberships.of_user(store_id, user_id)
            if existing is not None:
                return self.refuse_if_blocked(existing)
            pending = self.new_membership(
                store_id, user_id, MembershipLevel.CONNECTED, MembershipStatus.PENDING
            )
            created = await uow.memberships.request(pending, self.clock.now())
            if created is None:
                return self.refuse_if_blocked(await self.existing(uow, store_id, user_id))
        return created

    def refuse_if_blocked(self, membership: Membership) -> Membership:
        if membership.status is MembershipStatus.BLOCKED:
            raise JoinRefusedError
        return membership

    async def existing(
        self, uow: MembershipUnitOfWork, store_id: UUID, user_id: UUID
    ) -> Membership:
        membership = await uow.memberships.of_user(store_id, user_id)
        if membership is None:
            raise MembershipNotFoundError
        return membership

    async def mine(self, context: TenantContext) -> Sequence[Membership]:
        user_id = require_user(context)
        async with self.uow_factory(context) as uow:
            return await uow.memberships.for_user(user_id)

    async def of_my_store(self, context: TenantContext) -> Sequence[Membership]:
        store_id = require_store(context)
        async with self.uow_factory(context) as uow:
            return await uow.memberships.for_store(store_id)

    async def decide(
        self, context: TenantContext, membership_id: UUID, approve: bool
    ) -> Membership:
        require_store(context)
        allowed = (MembershipStatus.PENDING,) if approve else tuple(MembershipStatus)
        status = MembershipStatus.ACTIVE if approve else MembershipStatus.BLOCKED
        async with self.uow_factory(context) as uow:
            changed = await uow.memberships.set_status(
                membership_id, allowed, status, self.clock.now()
            )
        if changed is None:
            raise MembershipNotFoundError
        return changed

    async def invite(self, context: TenantContext, label: str, days: int) -> IssuedInvitation:
        store_id = require_store(context)
        user_id = require_user(context)
        await self.limiter.enforce(INVITE_PER_STORE, str(store_id))
        now = self.clock.now()
        token = self.tokens.new_token()
        invitation = Invitation(
            self.ids.new_id(),
            store_id,
            user_id,
            label,
            now + invitation_lifetime(days),
            None,
            None,
            None,
            now,
        )
        async with self.uow_factory(context) as uow:
            await uow.invitations.add(invitation, hash_token(token))
        return IssuedInvitation(invitation, token)

    async def invitations(self, context: TenantContext) -> Sequence[Invitation]:
        store_id = require_store(context)
        async with self.uow_factory(context) as uow:
            return await uow.invitations.for_store(store_id)

    async def revoke(self, context: TenantContext, invitation_id: UUID) -> Invitation:
        require_store(context)
        async with self.uow_factory(context) as uow:
            revoked = await uow.invitations.revoke(invitation_id, self.clock.now())
        if revoked is None:
            raise MembershipNotFoundError
        return revoked

    async def accept(self, context: TenantContext, token: str) -> Membership:
        user_id = require_user(context)
        now = self.clock.now()
        async with self.uow_factory(context) as uow:
            invitation = await uow.invitations.present(hash_token(token))
            if invitation is None or not invitation.is_open(now):
                raise InvalidInvitationError
            await self.check_acceptable(uow, invitation.store_id, user_id)
            if not await uow.invitations.claim(invitation.id, user_id, now):
                raise InvalidInvitationError
            member = self.new_membership(
                invitation.store_id, user_id, MembershipLevel.SELLER_ADDED, MembershipStatus.ACTIVE
            )
            joined = await uow.memberships.join_by_invitation(
                member, invitation.created_by_user_id, now
            )
            if joined is None:
                raise AlreadyMemberError
        return joined

    async def check_acceptable(
        self, uow: MembershipUnitOfWork, store_id: UUID, user_id: UUID
    ) -> None:
        if not await uow.store_gate.accepts_members(store_id):
            raise InvalidInvitationError
        if await uow.store_gate.owner_of(store_id) == user_id:
            raise JoinRefusedError
        existing = await uow.memberships.of_user(store_id, user_id)
        if existing is not None and existing.status is MembershipStatus.BLOCKED:
            raise JoinRefusedError
        if existing is not None and existing.is_full_member:
            raise AlreadyMemberError
