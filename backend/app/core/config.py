from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    database_url: str = Field(default="postgresql+psycopg2://dataq:dataq@localhost:5432/dataq")
    redis_url: str = Field(default="redis://localhost:6379/0")

    applicationinsights_connection_string: str | None = None

    sample_failures_retention_days: int = 30

    azure_tenant_id: str | None = None
    azure_api_client_id: str | None = None
    azure_spa_client_id: str | None = None
    azure_api_scope: str = "user_impersonation"

    auth_dev_bypass: bool = False

    secret_store: Literal["env", "azure_key_vault"] = (
        "env"  # noqa: S105 — mode selector, not a password
    )
    azure_key_vault_url: str | None = None

    @property
    def azure_auth_configured(self) -> bool:
        return bool(self.azure_tenant_id and self.azure_api_client_id)

    @property
    def azure_api_scope_uri(self) -> str | None:
        if not self.azure_api_client_id:
            return None
        return f"api://{self.azure_api_client_id}/{self.azure_api_scope}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
