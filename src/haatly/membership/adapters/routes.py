from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from haatly.identity.public import accounts_of
from haatly.membership.app.memberships import (
    AlreadyMemberError,
    InvalidInvitationError,
    JoinRefusedError,
    MembershipNotFoundError,
    Memberships,
)
from haatly.membership.domain.model import (
    MAX_INVITATION_DAYS,
    Invitation,
    Membership,
    MembershipLevel,
    MembershipStatus,
)
from haatly.platform.public import ApiError, NonBlank, NulStripped, bounded
from haatly.stores.public import SellerContextDep, UserContextDep

LabelText = Annotated[str, NulStripped, bounded(100), NonBlank]
TokenText = Annotated[str, NulStripped, bounded(100), NonBlank]


def examples(*values: dict[str, Any]) -> ConfigDict:
    return ConfigDict(extra="forbid", json_schema_extra={"examples": list(values)})


class NewInvitation(BaseModel):
    model_config = examples({"label": "{new_name}", "expires_in_days": 7})
    label: LabelText
    expires_in_days: int = Field(default=7, ge=1, le=MAX_INVITATION_DAYS)


class InvitationAcceptance(BaseModel):
    model_config = examples({"token": "{invitation_token}"})
    token: TokenText


class MembershipOut(BaseModel):
    id: UUID
    store_id: UUID
    level: MembershipLevel
    status: MembershipStatus

    @classmethod
    def of(cls, membership: Membership) -> "MembershipOut":
        return cls(
            id=membership.id,
            store_id=membership.store_id,
            level=membership.level,
            status=membership.status,
        )


class MemberOut(MembershipOut):
    user_id: UUID
    email: str


class InvitationOut(BaseModel):
    id: UUID
    label: str
    expires_at: datetime
    accepted: bool
    revoked: bool

    @classmethod
    def of(cls, invitation: Invitation) -> "InvitationOut":
        return cls(
            id=invitation.id,
            label=invitation.label,
            expires_at=invitation.expires_at,
            accepted=invitation.accepted_at is not None,
            revoked=invitation.revoked_at is not None,
        )


class IssuedInvitationOut(InvitationOut):
    token: str


def memberships_of(request: Request) -> Memberships:
    memberships: Memberships = request.app.state.memberships
    return memberships


MembershipsDep = Annotated[Memberships, Depends(memberships_of)]
NOT_FOUND = ApiError(status.HTTP_404_NOT_FOUND, "not_found", "not found")

router = APIRouter()


@router.post("/stores/{store_id}/join", status_code=status.HTTP_201_CREATED)
async def join_store(
    store_id: UUID, context: UserContextDep, memberships: MembershipsDep
) -> MembershipOut:
    try:
        return MembershipOut.of(await memberships.request_join(context, store_id))
    except MembershipNotFoundError as error:
        raise NOT_FOUND from error
    except JoinRefusedError as error:
        raise ApiError(403, "join_refused", "you cannot join this store") from error


@router.get("/me/memberships")
async def my_memberships(
    context: UserContextDep, memberships: MembershipsDep
) -> list[MembershipOut]:
    return [MembershipOut.of(item) for item in await memberships.mine(context)]


@router.get("/seller/members")
async def store_members(
    context: SellerContextDep, memberships: MembershipsDep, request: Request
) -> list[MemberOut]:
    members = await memberships.of_my_store(context)
    emails = await accounts_of(request).emails([member.user_id for member in members])
    return [
        MemberOut(
            id=member.id,
            store_id=member.store_id,
            level=member.level,
            status=member.status,
            user_id=member.user_id,
            email=emails.get(member.user_id, ""),
        )
        for member in members
    ]


async def decide(
    context: Any, memberships: Memberships, membership_id: UUID, *, approve: bool
) -> MembershipOut:
    try:
        return MembershipOut.of(await memberships.decide(context, membership_id, approve))
    except MembershipNotFoundError as error:
        raise NOT_FOUND from error


@router.post("/seller/members/{membership_id}/approve")
async def approve_member(
    membership_id: UUID, context: SellerContextDep, memberships: MembershipsDep
) -> MembershipOut:
    return await decide(context, memberships, membership_id, approve=True)


@router.post("/seller/members/{membership_id}/block")
async def block_member(
    membership_id: UUID, context: SellerContextDep, memberships: MembershipsDep
) -> MembershipOut:
    return await decide(context, memberships, membership_id, approve=False)


@router.post("/seller/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    body: NewInvitation, context: SellerContextDep, memberships: MembershipsDep
) -> IssuedInvitationOut:
    issued = await memberships.invite(context, body.label, body.expires_in_days)
    summary = InvitationOut.of(issued.invitation)
    return IssuedInvitationOut(**summary.model_dump(), token=issued.token)


@router.get("/seller/invitations")
async def list_invitations(
    context: SellerContextDep, memberships: MembershipsDep
) -> list[InvitationOut]:
    return [InvitationOut.of(item) for item in await memberships.invitations(context)]


@router.post("/seller/invitations/{invitation_id}/revoke")
async def revoke_invitation(
    invitation_id: UUID, context: SellerContextDep, memberships: MembershipsDep
) -> InvitationOut:
    try:
        return InvitationOut.of(await memberships.revoke(context, invitation_id))
    except MembershipNotFoundError as error:
        raise NOT_FOUND from error


@router.post("/invitations/accept")
async def accept_invitation(
    body: InvitationAcceptance, context: UserContextDep, memberships: MembershipsDep
) -> MembershipOut:
    try:
        return MembershipOut.of(await memberships.accept(context, body.token))
    except InvalidInvitationError as error:
        raise ApiError(404, "invalid_invitation", "this invitation cannot be used") from error
    except JoinRefusedError as error:
        raise ApiError(403, "join_refused", "you cannot join this store") from error
    except AlreadyMemberError as error:
        raise ApiError(409, "already_member", "you are already a member") from error
