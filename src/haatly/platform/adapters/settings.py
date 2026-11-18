from enum import StrEnum
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PRODUCTION_SECRET_LENGTH = 24


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class InsecureProductionSettingsError(ValueError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HAATLY_", env_file=".env", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    db_host: str = "127.0.0.1"
    db_port: int = 55432
    db_name: str = "haatly"
    db_owner_password: SecretStr
    db_app_password: SecretStr
    public_origin: str = "https://localhost:8443"
    worker_idle_seconds: float = 5.0

    @model_validator(mode="after")
    def refuse_weak_production_secrets(self) -> Self:
        if self.environment is not Environment.PRODUCTION:
            return self
        for secret in (self.db_owner_password, self.db_app_password):
            if len(secret.get_secret_value()) < MIN_PRODUCTION_SECRET_LENGTH:
                raise InsecureProductionSettingsError("production secrets are too short")
        if not self.public_origin.startswith("https://"):
            raise InsecureProductionSettingsError("production origin must be https")
        return self
