from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # App config lives in .env.app (host dev reads it directly; compose injects
        # it into the api/worker containers via env_file). The root .env is
        # compose/infra-only (POSTGRES_*, VITE_*) and is NOT read here. extra=forbid
        # catches typo'd/stale keys — the split keeps those infra keys from tripping
        # it. See #209.
        env_file=".env.app",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # No DB credentials embedded in code. The real URL (with password) comes from
    # the environment: docker-compose and CI set DATABASE_URL; local host dev gets
    # it from .env (setup.sh bootstraps + exports it). This default is only a
    # credential-less placeholder for the no-env case.
    database_url: str = Field(default="postgresql+psycopg2://localhost:5432/dataq")
    redis_url: str = Field(default="redis://localhost:6379/0")

    applicationinsights_connection_string: str | None = None

    sample_failures_retention_days: int = 30

    azure_tenant_id: str | None = None
    azure_api_client_id: str | None = None
    azure_spa_client_id: str | None = None
    azure_api_scope: str = "user_impersonation"

    auth_dev_bypass: bool = False

    secret_store: Literal["env", "redis", "azure_key_vault"] = (
        "env"  # noqa: S105 — mode selector, not a password
    )
    azure_key_vault_url: str | None = None

    # SecretStore key holding the ADF webhook shared secret (ADR 0006). Resolved
    # via SecretStore.get → EnvSecretStore maps it to KV_SECRET_ADF_WEBHOOK_SECRET
    # in dev, Key Vault secret `adf-webhook-secret` in prod. Not the secret value.
    adf_webhook_secret_name: str = "adf-webhook-secret"  # noqa: S105 — KV key name, not a secret
    # SecretStore key holding the Airflow callback HMAC signing key (ADR 0007).
    # → KV_SECRET_AIRFLOW_WEBHOOK_SECRET in dev, KV secret `airflow-webhook-secret`
    # in prod. The signing key, not a webhook value.
    airflow_webhook_secret_name: str = "airflow-webhook-secret"  # noqa: S105 — KV key name

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
