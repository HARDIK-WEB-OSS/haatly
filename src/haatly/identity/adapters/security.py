import asyncio
import secrets
from functools import cached_property

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

TIMING_DECOY = "haatly-timing-equaliser"


class Argon2idHasher:
    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self.hasher = hasher or PasswordHasher(type=Type.ID)

    @cached_property
    def dummy_hash(self) -> str:
        return self.hasher.hash(TIMING_DECOY)

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self.hasher.hash, password)

    def check(self, password_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    async def verify(self, password_hash: str, password: str) -> bool:
        return await asyncio.to_thread(self.check, password_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        return self.hasher.check_needs_rehash(password_hash)

    async def burn(self, password: str) -> None:
        await self.verify(self.dummy_hash, password)


class SecretTokens:
    def new_token(self) -> str:
        return secrets.token_urlsafe(32)
