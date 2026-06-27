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

    # Browser origins allowed to call the API cross-origin (the Static Web App ↔
    # Container Apps split in prod — PR #40 nit). Comma-separated; empty = no
    # cross-origin allowed (same-origin / dev proxy needs none). Provider-neutral:
    # it's a list of origins, not an Azure concept. Read via `cors_allow_origin_list`.
    #   CORS_ALLOW_ORIGINS=https://app.example.com,https://dataq.example.com
    cors_allow_origins: str = ""

    # Workspace-admin allowlist — emails permitted to use the /admin read
    # endpoints (all-suites / all-users / access overview). Single-tenant, so this
    # is the whole-workspace admin set, distinct from the per-suite
    # view/edit/admin/owner ladder in suite_authz. Matched case-insensitively
    # against the IdP-supplied email — a generic identity attribute, so no
    # Azure/Entra claim is read in service code (ADR 0010/0013, CLAUDE.md §11).
    # Stored as a comma-separated string (not list[str]) to sidestep
    # pydantic-settings' JSON decoding of complex env values; read it via the
    # normalised `workspace_admin_email_set` property, never the raw field.
    #   WORKSPACE_ADMIN_EMAILS=ada@acme.io,grace@acme.io
    workspace_admin_emails: str = ""

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

    # SecretStore key holding the workspace MS Teams incoming-webhook URL (the URL
    # carries a token, so it lives in the SecretStore, not in config). Unset →
    # no Teams alerting (the no-op publisher). The value is the webhook URL,
    # resolved per run via SecretStore so a rotated webhook is picked up;
    # per-suite notification config (a later PR) extends the resolver. Provider-
    # neutral: Teams is one ResultPublisher impl behind the registry (ADR 0011).
    teams_webhook_secret_name: str | None = None

    # SSRF allowlist for the per-suite Teams webhook URL. The webhook is supplied
    # by a suite editor and POSTed server-side, so its host is constrained to this
    # comma-separated set of suffixes (defaults to MS Teams incoming-webhook +
    # Power Automate workflow hosts; extend via env for a private relay). Stored as
    # a string — not list[str] — like workspace_admin_emails, to sidestep Pydantic
    # env list-parsing.
    teams_webhook_allowed_hosts: str = "webhook.office.com,logic.azure.com"

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
    def workspace_admin_email_set(self) -> frozenset[str]:
        """Normalised (lower-cased, stripped) admin emails for membership tests.

        Empty when unset → no workspace admins (every /admin request 403s), the
        safe default.
        """
        return frozenset(
            part.strip().lower() for part in self.workspace_admin_emails.split(",") if part.strip()
        )

    @property
    def cors_allow_origin_list(self) -> list[str]:
        """Parsed CORS origins (stripped, empties dropped). Empty → CORS off."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

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
