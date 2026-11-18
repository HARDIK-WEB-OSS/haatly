from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ActorRole(StrEnum):
    PUBLIC = "public"
    BUYER = "buyer"
    SELLER = "seller"
    ADMIN = "admin"
    SYSTEM = "system"


class InvalidTenantContextError(ValueError):
    pass


ROLES_REQUIRING_USER = frozenset({ActorRole.BUYER, ActorRole.SELLER, ActorRole.ADMIN})
ROLES_WITHOUT_USER = frozenset({ActorRole.PUBLIC, ActorRole.SYSTEM})


@dataclass(frozen=True)
class TenantContext:
    role: ActorRole
    user_id: UUID | None = None
    store_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.role in ROLES_REQUIRING_USER and self.user_id is None:
            raise InvalidTenantContextError(f"{self.role} needs a user")
        if self.role in ROLES_WITHOUT_USER and self.user_id is not None:
            raise InvalidTenantContextError(f"{self.role} cannot carry a user")
        if self.role is ActorRole.SELLER and self.store_id is None:
            raise InvalidTenantContextError("seller needs a store")

    def settings(self) -> dict[str, str]:
        return {
            "app.role": self.role.value,
            "app.user_id": "" if self.user_id is None else str(self.user_id),
            "app.store_id": "" if self.store_id is None else str(self.store_id),
        }


PUBLIC = TenantContext(ActorRole.PUBLIC)
SYSTEM = TenantContext(ActorRole.SYSTEM)
