from enum import StrEnum
from ipaddress import ip_network
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


class InvalidTrustedProxyError(ValueError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HAATLY_", env_file=".env", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    db_host: str = "127.0.0.1"
    db_port: int = 55432
    db_name: str = "haatly"
    db_owner_password: SecretStr
    db_app_password: SecretStr
    db_ops_password: SecretStr | None = None
    public_origin: str = "https://localhost:8443"
    worker_idle_seconds: float = 5.0
    trusted_proxies: str = ""

    @property
    def trusted_proxy_list(self) -> list[str]:
        return [entry.strip() for entry in self.trusted_proxies.split(",") if entry.strip()]

    @model_validator(mode="after")
    def check_trusted_proxies(self) -> Self:
        for entry in self.trusted_proxy_list:
            try:
                ip_network(entry, strict=False)
            except ValueError as error:
                raise InvalidTrustedProxyError(f"not an IP address or network: {entry}") from error
        return self

    @model_validator(mode="after")
    def refuse_weak_production_secrets(self) -> Self:
        if self.environment is not Environment.PRODUCTION:
            return self
        for secret in (self.db_owner_password, self.db_app_password):
            if len(secret.get_secret_value()) < MIN_PRODUCTION_SECRET_LENGTH:
                raise InsecureProductionSettingsError("production secrets are too short")
        if not self.public_origin.startswith("https://"):
            raise InsecureProductionSettingsError("production origin must be https")
        if not self.trusted_proxy_list:
            raise InsecureProductionSettingsError("production needs HAATLY_TRUSTED_PROXIES")
        return self
