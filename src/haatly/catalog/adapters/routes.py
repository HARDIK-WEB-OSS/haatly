from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from haatly.catalog.app.catalog import Catalog, CatalogNotFoundError
from haatly.catalog.domain.model import (
    MAX_PRICE_PAISE,
    InvalidPriceError,
    ProductChanges,
    ProductStatus,
    ProductVisibility,
    SellerProduct,
    ShopProduct,
    ShopProductDetail,
    Variant,
    VariantChanges,
    VariantStatus,
)
from haatly.platform.public import ApiError, NonBlank, NulStripped, bounded
from haatly.stores.public import ContextDep, SellerContextDep

TitleText = Annotated[str, NulStripped, bounded(150), NonBlank]
DescriptionText = Annotated[str, NulStripped, bounded(4000), NonBlank]
LabelText = Annotated[str, NulStripped, bounded(80), NonBlank]
SearchText = Annotated[str, NulStripped, bounded(100), NonBlank]
PricePaise = Annotated[int, Field(gt=0, le=MAX_PRICE_PAISE, strict=True)]


def examples(*values: dict[str, Any]) -> ConfigDict:
    return ConfigDict(extra="forbid", json_schema_extra={"examples": list(values)})


class NewProduct(BaseModel):
    model_config = examples({"title": "{new_name}", "description": "{new_name}"})
    title: TitleText
    description: DescriptionText
    visibility: ProductVisibility = ProductVisibility.PUBLIC


class ProductPatch(BaseModel):
    model_config = examples({"title": "{new_name}", "status": "active"})
    title: TitleText | None = None
    description: DescriptionText | None = None
    visibility: ProductVisibility | None = None
    status: ProductStatus | None = None


class NewVariant(BaseModel):
    model_config = examples({"label": "{new_name}", "price_paise": 1})
    label: LabelText
    price_paise: PricePaise


class VariantPatch(BaseModel):
    model_config = examples({"price_paise": 1})
    label: LabelText | None = None
    status: VariantStatus | None = None
    price_paise: PricePaise | None = None


class VariantOut(BaseModel):
    id: UUID
    label: str
    status: VariantStatus
    price_paise: int

    @classmethod
    def of(cls, variant: Variant) -> "VariantOut":
        return cls(
            id=variant.id,
            label=variant.label,
            status=variant.status,
            price_paise=variant.price_paise,
        )


class SellerProductOut(BaseModel):
    id: UUID
    title: str
    description: str
    visibility: ProductVisibility
    status: ProductStatus
    variants: list[VariantOut]

    @classmethod
    def of(cls, item: SellerProduct) -> "SellerProductOut":
        product = item.product
        return cls(
            id=product.id,
            title=product.title,
            description=product.description,
            visibility=product.visibility,
            status=product.status,
            variants=[VariantOut.of(variant) for variant in item.variants],
        )


class ShopProductOut(BaseModel):
    id: UUID
    store_id: UUID
    title: str
    description: str
    members_only: bool
    price_from_paise: int | None

    @classmethod
    def of(cls, product: ShopProduct) -> "ShopProductOut":
        return cls(
            id=product.id,
            store_id=product.store_id,
            title=product.title,
            description=product.description,
            members_only=product.visibility is ProductVisibility.MEMBERS,
            price_from_paise=product.price_from_paise,
        )


class ShopVariantOut(BaseModel):
    id: UUID
    label: str
    price_paise: int | None


class ShopProductDetailOut(ShopProductOut):
    variants: list[ShopVariantOut]

    @classmethod
    def detail(cls, detail: ShopProductDetail) -> "ShopProductDetailOut":
        summary = ShopProductOut.of(detail.product)
        variants = [
            ShopVariantOut(id=v.id, label=v.label, price_paise=v.price_paise)
            for v in detail.variants
        ]
        return cls(**summary.model_dump(), variants=variants)


def catalog_of(request: Request) -> Catalog:
    catalog: Catalog = request.app.state.catalog
    return catalog


CatalogDep = Annotated[Catalog, Depends(catalog_of)]
NOT_FOUND = ApiError(status.HTTP_404_NOT_FOUND, "not_found", "not found")
INVALID_PRICE = ApiError(422, "validation_error", "price is out of range")

router = APIRouter()


@router.post("/seller/products", status_code=status.HTTP_201_CREATED)
async def create_product(
    body: NewProduct, context: SellerContextDep, catalog: CatalogDep
) -> SellerProductOut:
    product = await catalog.create_product(context, body.title, body.description, body.visibility)
    return SellerProductOut.of(SellerProduct(product, ()))


@router.get("/seller/products")
async def seller_products(context: SellerContextDep, catalog: CatalogDep) -> list[SellerProductOut]:
    return [SellerProductOut.of(item) for item in await catalog.seller_products(context)]


async def found[T](work: Any) -> T:
    try:
        result: T = await work
    except CatalogNotFoundError as error:
        raise NOT_FOUND from error
    except InvalidPriceError as error:
        raise INVALID_PRICE from error
    return result


@router.get("/seller/products/{product_id}")
async def seller_product(
    product_id: UUID, context: SellerContextDep, catalog: CatalogDep
) -> SellerProductOut:
    return SellerProductOut.of(await found(catalog.seller_product(context, product_id)))


@router.patch("/seller/products/{product_id}")
async def update_product(
    product_id: UUID, body: ProductPatch, context: SellerContextDep, catalog: CatalogDep
) -> SellerProductOut:
    changes = ProductChanges(body.title, body.description, body.visibility, body.status)
    await found(catalog.update_product(context, product_id, changes))
    return SellerProductOut.of(await found(catalog.seller_product(context, product_id)))


@router.post("/seller/products/{product_id}/variants", status_code=status.HTTP_201_CREATED)
async def add_variant(
    product_id: UUID, body: NewVariant, context: SellerContextDep, catalog: CatalogDep
) -> VariantOut:
    variant: Variant = await found(
        catalog.add_variant(context, product_id, body.label, body.price_paise)
    )
    return VariantOut.of(variant)


@router.patch("/seller/variants/{variant_id}")
async def update_variant(
    variant_id: UUID, body: VariantPatch, context: SellerContextDep, catalog: CatalogDep
) -> VariantOut:
    changes = VariantChanges(body.label, body.status, body.price_paise)
    variant: Variant = await found(catalog.update_variant(context, variant_id, changes))
    return VariantOut.of(variant)


@router.get("/stores/{store_id}/products")
async def storefront(
    store_id: UUID,
    context: ContextDep,
    catalog: CatalogDep,
    q: Annotated[SearchText | None, Query()] = None,
) -> list[ShopProductOut]:
    return [ShopProductOut.of(item) for item in await catalog.storefront(context, store_id, q)]


@router.get("/stores/{store_id}/products/{product_id}")
async def storefront_product(
    store_id: UUID, product_id: UUID, context: ContextDep, catalog: CatalogDep
) -> ShopProductDetailOut:
    detail: ShopProductDetail = await found(
        catalog.storefront_product(context, store_id, product_id)
    )
    return ShopProductDetailOut.detail(detail)
