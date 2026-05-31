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

    # SecretStore key holding the ADF webhook shared secret (ADR 0006). Resolved
    # via SecretStore.get → EnvSecretStore maps it to KV_SECRET_ADF_WEBHOOK_SECRET
    # in dev, Key Vault secret `adf-webhook-secret` in prod. Not the secret value.
    adf_webhook_secret_name: str = "adf-webhook-secret"  # noqa: S105 — KV key name, not a secret

    # ── Snowflake probe (Week 1 exit-gate endpoint) ──────────────────────────
    # Config for the single seeded dev Snowflake connection the probe runs
    # against. All optional: when unset the probe still creates + dispatches a
    # run, which then fails-soft (no live warehouse). secret_ref names the
    # SecretStore entry holding the password (e.g. KV_SECRET_SNOWFLAKE_DEV).
    probe_snowflake_account: str | None = None
    probe_snowflake_user: str | None = None
    probe_snowflake_database: str | None = None
    probe_snowflake_schema: str | None = None
    probe_snowflake_warehouse: str | None = None
    probe_snowflake_role: str | None = None
    probe_snowflake_table: str | None = None
    probe_snowflake_secret_ref: str | None = None

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
