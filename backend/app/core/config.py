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

    # Generic OTLP/HTTP exporter endpoint (#589) — the standard OTel contract
    # (OTEL_EXPORTER_OTLP_ENDPOINT). When set, spans AND logs also export to this
    # OTLP consumer (Grafana/Tempo, Jaeger, Datadog, …) via the OTLP/HTTP exporter,
    # with base-endpoint semantics (`/v1/traces` + `/v1/logs` appended). The Azure
    # Monitor exporter (APPLICATIONINSIGHTS_CONNECTION_STRING) is just one backend
    # behind the same seam (ADR 0010); both may be set at once — that's the parity
    # check (same trace/log in App Insights AND a local collector). Neither set ⇒
    # telemetry is off. The standard sibling env vars (OTEL_EXPORTER_OTLP_HEADERS,
    # _TIMEOUT, …) are read by the exporter itself.
    otel_exporter_otlp_endpoint: str | None = None

    # OpenLineage emission (ADR 0034, #758) — dark by default. When
    # OPENLINEAGE_URL is set (and OPENLINEAGE_DISABLED is not truthy) the run
    # lifecycle emits START/COMPLETE/FAIL/ABORT RunEvents (with DQ facets) to that
    # HTTP receiver (Marquez, an OpenLineage collector, …). Typed here — not read
    # from raw os.environ — so a value in `.env.app` (which the process env never
    # sees) still activates emission. The library-owned advanced transports
    # (OPENLINEAGE__TRANSPORT__* / OPENLINEAGE_CONFIG) stay in raw env, read by the
    # client itself; those are the only OpenLineage vars NOT surfaced here.
    #   OPENLINEAGE_URL=http://marquez:5000
    openlineage_url: str | None = None
    openlineage_disabled: bool = False

    # Lineage catalog pull (ADR 0034, #762) — dark by default. The `LineageProvider`
    # seam pulls a lineage graph from a governance catalog and caches it into
    # `lineage_edges` (source='marquez'). Unset `lineage_provider` → no pull (the beat
    # task no-ops). Only `marquez` is implemented; DataHub/OpenMetadata/Purview are
    # deferred behind the same seam. Typed here (not raw os.environ) so a `.env.app`
    # value activates it. `marquez_url` is the reference server's base URL.
    #   LINEAGE_PROVIDER=marquez
    #   MARQUEZ_URL=http://marquez:5000
    lineage_provider: str = ""
    marquez_url: str | None = None

    sample_failures_retention_days: int = 30

    # Stuck-run reaper (#309): a run committed `queued` (before `send_task`) — or
    # left `running` by a worker that died mid-execution — past this age is driven
    # to terminal `failed` by the beat janitor so it can't linger forever. Must
    # comfortably exceed the longest plausible suite run so a slow-but-alive run is
    # never reaped (a false reap self-corrects when the worker later commits its
    # real outcome, but would emit a spurious alert).
    stuck_run_threshold_minutes: int = 60

    # Orphan-asset sweep (#770, ADR 0034 — "asset rows accrete; last_seen + a
    # sweep, not deletes, is the cleanup posture"). An asset whose `last_seen`
    # hasn't advanced in this many days AND that no suite/run/lineage_edge (and,
    # once #761 lands, incident) still references is deleted by the beat janitor.
    # Deliberately generous — must comfortably outlive the slowest suite schedule
    # and the lineage-refresh poll cadence, or a legitimately-live asset would be
    # swept and immediately re-created on the next refresh. <=0 disables the sweep.
    asset_orphan_retention_days: int = 30

    azure_tenant_id: str | None = None
    azure_api_client_id: str | None = None
    azure_spa_client_id: str | None = None
    azure_api_scope: str = "user_impersonation"

    # Allow guest (B2B / external) identities in the tenant to authenticate.
    # Default off (fastapi-azure-auth's own secure default): the token validator
    # rejects guests with 403 "Guest users not allowed". Enable for deployments
    # whose legitimate users sign in with a guest account (e.g. a personal
    # Microsoft account invited into the tenant). Still bounded by tenant
    # membership + the API scope; orthogonal to WORKSPACE_ADMIN_EMAILS.
    azure_allow_guest_users: bool = False

    auth_dev_bypass: bool = False

    # Browser origins allowed to call the API cross-origin (the Static Web App ↔
    # Container Apps split in prod — PR #40 nit). Comma-separated; empty = no
    # cross-origin allowed (same-origin / dev proxy needs none). Provider-neutral:
    # it's a list of origins, not an Azure concept. Read via `cors_allow_origin_list`.
    #   CORS_ALLOW_ORIGINS=https://app.example.com,https://dataq.example.com
    cors_allow_origins: str = ""

    # Public base URL of the deployed app (scheme+host, no trailing slash). Used to
    # assemble the inbound orchestration webhook URLs the admin webhook-config
    # surface shows (#490) AND the "View run" deep links in Slack/email alerts
    # (/results/<run_id>, #416). Set to the public host on deploy (the frontend
    # origin that proxies /api). Empty → webhook URLs fall back to the request's own
    # base URL, and alerts omit the deep link.
    #   PUBLIC_BASE_URL=https://dataq.example.com
    public_base_url: str = ""

    # ── Rate limiting (#725, ADR 0035) ───────────────────────────────────────
    # Fixed-window (60s) request throttle on every public surface — REST, the
    # orchestration webhooks, and the mounted /mcp app — keyed per sha256(bearer)
    # for authenticated traffic and per client-IP otherwise. Fail-open (a Redis
    # outage disables limiting, logged). Defaults are generous; tighten
    # RATE_LIMIT_WEBHOOK_PER_MINUTE to your orchestrator's callback cadence.
    rate_limit_enabled: bool = True
    rate_limit_authenticated_per_minute: int = 300  # per sha256(bearer) bucket
    rate_limit_unauthenticated_per_minute: int = 120  # per client-IP bucket
    rate_limit_webhook_per_minute: int = 120  # per client-IP, /api/v1/orchestration/events/*
    rate_limit_ip_per_minute: int = (
        1200  # per-IP ceiling across all bearer buckets (rotated-token backstop)
    )
    rate_limit_xff_trusted_hops: int = (
        1  # count of trusted proxies appending XFF; pick entry hops-from-right
    )

    # ── Comparison checks (ADR 0015) ─────────────────────────────────────────
    # Default row cap per comparison side. Both sides materialize in worker
    # memory for the diff (#793), so this is a memory guardrail, not a tuning
    # knob — over-cap runs fail fast (never a silently truncated diff). A check
    # may override via config.max_rows; scale-aware execution (G-b) is the path
    # past in-memory limits.
    comparison_max_rows: int = 100_000

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
    # SecretStore key holding the dbt callback HMAC signing key (ADR 0029; sibling
    # of the Airflow key). → KV_SECRET_DBT_WEBHOOK_SECRET in dev, KV secret
    # `dbt-webhook-secret` in prod. App-level (shared across dbt connections); the
    # per-connection secret is the artifacts-store read credential, not this.
    dbt_webhook_secret_name: str = "dbt-webhook-secret"  # noqa: S105 — KV key name

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

    # ── Slack alerting (workspace-level incoming webhook) ────────────────────
    # SecretStore key holding the Slack incoming-webhook URL
    # (https://hooks.slack.com/services/...). The URL carries a token, so it
    # lives in the SecretStore. Unset → no Slack alerting (quiet no-op). Resolved
    # per run so a rotated webhook is picked up. One ResultPublisher impl behind
    # the registry composite (ADR 0011) — same per-suite alert_on policy as Teams.
    slack_webhook_secret_name: str | None = None
    # SSRF allowlist for the Slack webhook host (POSTed server-side).
    slack_webhook_allowed_hosts: str = "hooks.slack.com"

    # ── Email (SMTP) alerting ────────────────────────────────────────────────
    # Non-secret SMTP coordinates live in config; the password (e.g. a Gmail
    # app-password) lives in the SecretStore by name. Email alerting is active
    # only when email_to, email_username, and email_password_secret_name are all
    # set (else a quiet no-op). STARTTLS on the submission port.
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_username: str | None = None
    email_from: str | None = None  # defaults to email_username when unset
    email_to: str = ""  # comma-separated recipients; empty → no email alerting
    email_password_secret_name: str | None = None

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

    def is_admin_email(self, email: str | None) -> bool:
        """True iff `email` is in the workspace-admin allowlist. The one
        normalization (strip + lower, null-safe) both the REST gate
        (`core.auth.is_workspace_admin`) and the per-suite gate (`suite_authz`)
        share — so the two can't drift."""
        normalized = (email or "").strip().lower()
        return bool(normalized) and normalized in self.workspace_admin_email_set

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
