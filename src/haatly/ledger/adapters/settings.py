from pydantic_settings import BaseSettings, SettingsConfigDict


class LedgerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HAATLY_", env_file=".env", extra="ignore")

    tigerbeetle_addresses: str = "127.0.0.1:13000"
    tigerbeetle_cluster_id: int = 0
    tigerbeetle_timeout_seconds: float = 5.0
    ledger_backstop_seconds: float = 30.0
    ledger_reconcile_every_seconds: float = 60.0
