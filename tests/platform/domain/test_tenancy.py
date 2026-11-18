from uuid import UUID

import pytest

from haatly.platform.domain.tenancy import (
    PUBLIC,
    SYSTEM,
    ActorRole,
    InvalidTenantContextError,
    TenantContext,
)

USER = UUID("018f0000-0000-7000-8000-000000000001")
STORE = UUID("018f0000-0000-7000-8000-000000000002")


def test_public_and_system_carry_no_identity() -> None:
    assert PUBLIC.settings() == {"app.role": "public", "app.user_id": "", "app.store_id": ""}
    assert SYSTEM.settings()["app.role"] == "system"


def test_seller_settings_carry_user_and_store() -> None:
    context = TenantContext(ActorRole.SELLER, USER, STORE)
    assert context.settings() == {
        "app.role": "seller",
        "app.user_id": str(USER),
        "app.store_id": str(STORE),
    }


@pytest.mark.parametrize(
    ("role", "user", "store"),
    [
        (ActorRole.BUYER, None, None),
        (ActorRole.ADMIN, None, None),
        (ActorRole.SELLER, USER, None),
        (ActorRole.SELLER, None, STORE),
        (ActorRole.PUBLIC, USER, None),
        (ActorRole.SYSTEM, USER, None),
    ],
)
def test_inconsistent_contexts_are_rejected(
    role: ActorRole, user: UUID | None, store: UUID | None
) -> None:
    with pytest.raises(InvalidTenantContextError):
        TenantContext(role, user, store)
