from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict

from haatly.identity.public import (
    Principal,
    accounts_of,
    optional_principal,
    presented_token,
    set_session_cookie,
)
from haatly.platform.public import (
    ActorRole,
    ApiError,
    NonBlank,
    NulStripped,
    TenantContext,
    bounded,
)
from haatly.stores.app.ports import AlreadyOwnsStoreError, SlugTakenError
from haatly.stores.app.stores import NotAllowedError, StoreNotFoundError, Stores, Viewer
from haatly.stores.domain.model import (
    InvalidSlugError,
    PriceVisibility,
    Store,
    StoreChanges,
    StoreStatus,
)

NameText = Annotated[str, NulStripped, bounded(120), NonBlank]
SlugText = Annotated[str, NulStripped, bounded(50), NonBlank]
DescriptionText = Annotated[str, NulStripped, bounded(2000), NonBlank]


class NewStore(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "{new_name}", "slug": "{new_slug}"}]},
    )
    name: NameText
    slug: SlugText


class StorePatch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"description": "{new_name}", "is_published": True}]},
    )
    name: NameText | None = None
    description: DescriptionText | None = None
    is_published: bool | None = None
    price_visibility: PriceVisibility | None = None


class PublicStoreOut(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str

    @classmethod
    def of(cls, store: Store) -> "PublicStoreOut":
        return cls(id=store.id, name=store.name, slug=store.slug, description=store.description)


class StoreOut(PublicStoreOut):
    owner_user_id: UUID
    status: StoreStatus
    is_published: bool
    price_visibility: PriceVisibility

    @classmethod
    def of(cls, store: Store) -> "StoreOut":
        return cls(
            id=store.id,
            name=store.name,
            slug=store.slug,
            description=store.description,
            owner_user_id=store.owner_user_id,
            status=store.status,
            is_published=store.is_published,
            price_visibility=store.price_visibility,
        )


def stores_of(request: Request) -> Stores:
    stores: Stores = request.app.state.stores
    return stores


StoresDep = Annotated[Stores, Depends(stores_of)]


async def current_context(
    principal: Annotated[Principal | None, Depends(optional_principal)], stores: StoresDep
) -> TenantContext:
    viewer = None if principal is None else Viewer(principal.user_id, principal.is_admin)
    return await stores.context_for(viewer)


ContextDep = Annotated[TenantContext, Depends(current_context)]


def require_user(context: ContextDep) -> TenantContext:
    if context.user_id is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "not_authenticated", "sign in first")
    return context


def require_seller(context: Annotated[TenantContext, Depends(require_user)]) -> TenantContext:
    if context.role is not ActorRole.SELLER:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "you have no store")
    return context


def require_admin(context: Annotated[TenantContext, Depends(require_user)]) -> TenantContext:
    if context.role is not ActorRole.ADMIN:
        raise ApiError(status.HTTP_403_FORBIDDEN, "forbidden", "admins only")
    return context


UserContextDep = Annotated[TenantContext, Depends(require_user)]
SellerContextDep = Annotated[TenantContext, Depends(require_seller)]
AdminContextDep = Annotated[TenantContext, Depends(require_admin)]
NOT_FOUND = ApiError(status.HTTP_404_NOT_FOUND, "not_found", "store not found")

router = APIRouter()


@router.post("/stores", status_code=status.HTTP_201_CREATED)
async def create_store(
    body: NewStore,
    context: UserContextDep,
    request: Request,
    response: Response,
    stores: StoresDep,
) -> StoreOut:
    try:
        store = await stores.create(context, body.name, body.slug)
    except InvalidSlugError as error:
        raise ApiError(422, "validation_error", "slug is not acceptable") from error
    except SlugTakenError as error:
        raise ApiError(409, "slug_taken", "that slug is taken") from error
    except (AlreadyOwnsStoreError, NotAllowedError) as error:
        raise ApiError(409, "already_has_store", "you already have a store") from error
    principal = Principal(store.owner_user_id, is_admin=False)
    token = await accounts_of(request).rotate(principal, presented_token(request) or "")
    set_session_cookie(response, token)
    return StoreOut.of(store)


@router.get("/stores/{store_id}")
async def public_store(store_id: UUID, context: ContextDep, stores: StoresDep) -> PublicStoreOut:
    try:
        return PublicStoreOut.of(await stores.visible(context, store_id))
    except StoreNotFoundError as error:
        raise NOT_FOUND from error


@router.get("/seller/store")
async def own_store(context: SellerContextDep, stores: StoresDep) -> StoreOut:
    return StoreOut.of(await stores.own(context))


@router.patch("/seller/store")
async def update_own_store(
    body: StorePatch, context: SellerContextDep, stores: StoresDep
) -> StoreOut:
    changes = StoreChanges(body.name, body.description, body.is_published, body.price_visibility)
    try:
        return StoreOut.of(await stores.update_own(context, changes))
    except StoreNotFoundError as error:
        raise NOT_FOUND from error


@router.get("/admin/stores")
async def stores_by_status(
    context: AdminContextDep,
    stores: StoresDep,
    status: StoreStatus = StoreStatus.PENDING,
) -> list[StoreOut]:
    return [StoreOut.of(store) for store in await stores.by_status(context, status)]


async def moderate(context: TenantContext, stores: Stores, store_id: UUID, action: str) -> StoreOut:
    try:
        return StoreOut.of(await stores.moderate(context, store_id, action))
    except StoreNotFoundError as error:
        raise NOT_FOUND from error


@router.post("/admin/stores/{store_id}/approve")
async def approve_store(store_id: UUID, context: AdminContextDep, stores: StoresDep) -> StoreOut:
    return await moderate(context, stores, store_id, "approve")


@router.post("/admin/stores/{store_id}/suspend")
async def suspend_store(store_id: UUID, context: AdminContextDep, stores: StoresDep) -> StoreOut:
    return await moderate(context, stores, store_id, "suspend")


@router.post("/admin/stores/{store_id}/reinstate")
async def reinstate_store(store_id: UUID, context: AdminContextDep, stores: StoresDep) -> StoreOut:
    return await moderate(context, stores, store_id, "reinstate")
