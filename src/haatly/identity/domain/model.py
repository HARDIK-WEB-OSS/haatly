import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

MAX_EMAIL_LENGTH = 254
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidEmailError(ValueError):
    pass


class WeakPasswordError(ValueError):
    pass


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_SHAPE.fullmatch(email):
        raise InvalidEmailError
    return email


def check_password(password: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise WeakPasswordError
    return password


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    password_hash: str
    is_admin: bool
    created_at: datetime


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    is_admin: bool


@dataclass(frozen=True)
class Session:
    id: UUID
    user_id: UUID
    token_hash: str
    created_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class SessionHolder:
    session: Session
    is_admin: bool


@dataclass(frozen=True)
class SessionPolicy:
    idle: timedelta = timedelta(hours=24)
    absolute: timedelta = timedelta(days=14)
    touch_after: timedelta = timedelta(minutes=1)

    def is_live(self, session: Session, now: datetime) -> bool:
        return now < session.created_at + self.absolute and now < session.last_seen_at + self.idle

    def needs_touch(self, session: Session, now: datetime) -> bool:
        return now - session.last_seen_at >= self.touch_after
