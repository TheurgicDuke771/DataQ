from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Lives here (not in `secrets.py`, which imports this module) so the startup validator and the
# `_build_store` fallback cannot drift apart.
_REDIS_STORE_REMOVED: Final = (
    "SECRET_STORE='redis' was removed in ADR 0039 — the store kept credentials in "
    "plaintext. Use SECRET_STORE=openbao (set OPENBAO_ADDR + OPENBAO_TOKEN; "
    "`docker compose up` starts the vault) and re-enter connection credentials. "
    "Then PURGE the old plaintext values, which outlive the switch: "
    "redis-cli --scan --pattern 'dataq:secret:*' | xargs -r redis-cli del. See "
    "docs/site/adr/0039-openbao-self-hosted-secret-backend.md"
)

# Named constants because `_blank_email_transport_means_default` must hand back the same values a
# blank env var falls through to.
AuthEmailTlsMode = Literal["starttls", "implicit", "none"]
_DEFAULT_AUTH_EMAIL_SMTP_PORT: Final = 587
_DEFAULT_AUTH_EMAIL_TLS_MODE: Final[AuthEmailTlsMode] = "starttls"
_AUTH_EMAIL_TRANSPORT_DEFAULTS: Final[dict[str, object]] = {
    "auth_email_smtp_port": _DEFAULT_AUTH_EMAIL_SMTP_PORT,
    "auth_email_tls_mode": _DEFAULT_AUTH_EMAIL_TLS_MODE,
}

# Default MCP transport Host allowlist (ACA internal-ingress shape + compose service name +
# loopback); override via MCP_ALLOWED_HOSTS (#728).
_MCP_DEFAULT_ALLOWED_HOSTS = ("*.azurecontainerapps.io", "api", "localhost", "127.0.0.1")


def _allowed_email_set(raw: str) -> frozenset[str]:
    """Parse a comma-separated signup allowlist into normalized addresses."""
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _allowed_domain_set(raw: str) -> frozenset[str]:
    """Parse a comma-separated allowlist into normalized domains; a leading `@` is
    tolerated so `@acme.io` doesn't silently never match.
    """
    return frozenset(
        part.strip().lower().lstrip("@") for part in raw.split(",") if part.strip().lstrip("@")
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # App config lives in .env.app; the root .env is compose/infra-only and NOT read here — the
        # split keeps infra keys from tripping extra=forbid (#209).
        env_file=".env.app",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Real URL (with password) comes from the environment; this default is only a credential-less
    # placeholder for the no-env case.
    database_url: str = Field(default="postgresql+psycopg2://localhost:5432/dataq")
    redis_url: str = Field(default="redis://localhost:6379/0")

    applicationinsights_connection_string: str | None = None

    # Generic OTLP/HTTP exporter endpoint (#589), base-endpoint semantics; may be set alongside the
    # Azure exporter.
    otel_exporter_otlp_endpoint: str | None = None

    # OpenLineage emission (ADR 0034, #758) — dark by default.
    openlineage_url: str | None = None
    openlineage_disabled: bool = False

    # Lineage catalog pull (ADR 0034, #762) — dark by default; only `marquez` is implemented.
    lineage_provider: str = ""
    marquez_url: str | None = None

    # Warehouse-native lineage pull (ADR 0034, #858) — off by default: it queries ACCOUNT_USAGE /
    # system.access, which need grants the principal may lack.
    warehouse_lineage_enabled: bool = False

    # Snowflake GET_LINEAGE seeds walked per refresh (#892) — a latency/cost bound.
    warehouse_lineage_max_seeds: int = 500

    # Hours after which a warehouse lineage source shows STALE independent of error state (#1091) —
    # catches a refresh loop that silently STOPS. 0 disables.
    lineage_stale_after_hours: int = 48

    # Workspace-wide poll-staleness alert (#1052), derived from DB writes and checked from the API
    # process, NOT the worker.
    poll_staleness_alert_after_s: int = 1800

    # How far back a #1186 trigger_env_near_miss row counts as "current" (#1199).
    trigger_env_near_miss_recent_hours: int = Field(default=48, ge=0)

    # Declared data-residency jurisdiction (G4/#434) — a DECLARATION, not an enforcement (the IaC
    # pins reality).
    deployment_region: str = ""

    sample_failures_retention_days: int = 30

    # Zero-sample privacy mode (#1676): when True, no failing-row sample is ever
    # PERSISTED — `_build_result` forces `sample_failures` null and drops the
    # monitor "provoking cell" (`unparsed_value`, #989) at write time, so every
    # downstream reader (results API, alerts, MCP) inherits the suppression for
    # free rather than needing its own gate.
    privacy_zero_sample_mode: bool = False

    # Audit-log retention (ADR 0041 §2.7).
    audit_retention_days: int = 365

    # Audit hash-chain external anchor (ADR 0041 §9 / #1460) — dark by default, same
    # posture as LINEAGE_PROVIDER: the chain is computed either way, only the anchor
    # publish is gated.
    tamper_anchor: Literal["none", "webhook"] = "none"
    tamper_anchor_webhook_url: str = ""
    tamper_anchor_webhook_secret: str = ""

    # OTP-code retention sweep (#1136) — hygiene.
    otp_codes_retention_hours: int = 24

    # Stuck-run reaper (#309).
    stuck_run_threshold_minutes: int = 60

    # llm_invocations reaper (#1644): pending covers a lost dispatch (API died before
    # send_task, or the broker dropped the message). `llm_invoke` has its own Celery
    # queue (#1777) so it no longer waits behind a `run_suite` backlog to be PULLED —
    # but queue separation adds no execution slot, so a burst of llm_invoke dispatches
    # (or a mid-rollout window where the worker isn't yet consuming the new queue) can
    # still leave one queued past a tight threshold; 15m was measured against
    # dispatch-loss alone and false-killed a merely-queued request, hence the margin.
    # Running covers a worker SIGKILL/OOM mid-provider-call —
    # 10m was margin above ONLY the 120s provider timeout, but `execute_invocation`
    # marks `running` before the kind builder runs, and the check-suggestion builder
    # does live warehouse work (list_columns + a full profile) inside that window; a
    # cold/suspended warehouse resume plus a wide table's profile plus the provider's
    # own worst case (120s timeout x up to 2 attempts, `max_retries=1`) can plausibly
    # cross 10m for a healthy call (#1726). Both are still heuristics, not proof of
    # death — see `_PENDING_REAP_REASON`/`_RUNNING_REAP_REASON` in `llm_service.py`,
    # which admit the ambiguity rather than assert a cause neither reap can verify.
    llm_invocation_pending_threshold_minutes: int = 30
    llm_invocation_running_threshold_minutes: int = 20
    # Recycle a prefork child past this RSS (KiB) so a large materialisation can't ratchet the
    # worker baseline (#755). 0 disables.
    worker_max_memory_per_child_kb: int = 1_500_000
    # Prefork pool size. Celery's default is the HOST's core count, not the container's (#1790).
    worker_concurrency: int = 4

    # Orphan-asset sweep (#770).
    asset_orphan_retention_days: int = 30

    # Per-connection cap on tables synced per tick (#919).
    asset_inventory_max_tables: int = 2000

    # Orphan-SECRET sweep (#1059): credential writes are outside the DB transaction, so a failure
    # can strand a vault entry forever.
    secret_orphan_grace_days: int = 30
    secret_orphan_purge: bool = False

    # Beat liveness watchdog (#904) — makes an "alive but doing nothing" worker exit so the platform
    # restarts it.
    beat_watchdog_stale_after_s: int = 600
    beat_watchdog_interval_s: int = 60

    azure_tenant_id: str | None = None
    azure_api_client_id: str | None = None
    azure_spa_client_id: str | None = None
    azure_api_scope: str = "user_impersonation"

    # Allow guest (B2B) identities to authenticate; default off (the library's own secure default).
    azure_allow_guest_users: bool = False

    # Generic OIDC (ADR 0026 amendment): any standards-compliant issuer.
    oidc_issuer: str | None = None
    oidc_audience: str | None = None

    # App-side signup gate for generic OIDC (#1386) — without it the IdP's own registration policy
    # silently becomes DataQ's (the open Cognito pool incident).
    oidc_allowed_emails: str = ""
    oidc_allowed_domains: str = ""

    # Role a NEW OIDC/AAD-provisioned user lands on (ADR 0033 decision 8, extended to OIDC after
    # #1386's domain allowlist).
    auth_oidc_default_role: Literal["member", "viewer"] = "member"

    auth_dev_bypass: bool = False

    # Browser origins allowed cross-origin; empty = none (same-origin needs none).
    cors_allow_origins: str = ""

    # Public base URL (scheme+host, no trailing slash) for webhook-config display (#490) and alert
    # deep links (#416).
    public_base_url: str = ""

    # ── Rate limiting (#725, ADR 0035) ─────────────────────────────────────── Fixed-window (60s)
    # throttle on every public surface, keyed per sha256(bearer) or client-IP.
    rate_limit_enabled: bool = True
    rate_limit_authenticated_per_minute: int = 300  # per sha256(bearer) bucket
    rate_limit_unauthenticated_per_minute: int = 120  # per client-IP bucket
    rate_limit_webhook_per_minute: int = (
        120  # per provider + client-IP bucket on /api/v1/orchestration/events/* (#785)
    )
    rate_limit_webhook_ip_per_minute: int = (
        240  # per-IP ceiling across ALL webhook buckets from one IP (#785)
    )
    rate_limit_auth_per_minute: int = (
        10  # per-IP bucket for /api/v1/auth/* (#1127), checked before the bearer
        # branch so a token can't dodge it; per-email counters are service-level.
    )
    rate_limit_llm_per_minute: int = (
        10  # per-principal LLM mutations (ADR 0042) — each one is an outbound model call
    )
    rate_limit_llm_ip_per_minute: int = (
        30  # per-IP ceiling across all llm buckets — the rotated-token backstop
    )
    rate_limit_ip_per_minute: int = (
        1200  # per-IP ceiling across all bearer buckets (rotated-token backstop)
    )
    rate_limit_xff_trusted_hops: int = (
        1  # count of trusted proxies appending XFF; pick entry hops-from-right
    )
    # Per-IP buckets key on a PREFIX, not the full address (#789): rotating NAT/proxy pools spread a
    # burst across many /32s. /32 and /128 disable grouping.
    rate_limit_ipv4_prefix: int = Field(default=24, ge=8, le=32)
    rate_limit_ipv6_prefix: int = Field(default=64, ge=32, le=128)

    # ── Scale-aware execution (#595, G-b) ──────────────────────────────────── Hard caps on what a
    # run may materialise, checked by a cheap probe BEFORE the read.
    run_max_scan_bytes: int = Field(default=134_217_728, ge=0)
    run_max_scan_rows: int = Field(default=1_500_000, ge=0)

    # UC SQL pushdown (#1532).
    uc_sql_pushdown: bool = True

    # Row cap per comparison side (ADR 0015) — a memory guardrail: both sides materialise for the
    # diff; over-cap fails fast, never a truncated diff.
    comparison_max_rows: int = 100_000

    # Workspace-admin allowlist, matched case-insensitively against the IdP email (a generic
    # identity attribute — no Azure claim read, ADR 0010/0013).
    workspace_admin_emails: str = ""

    # Host values the FastMCP transport guard accepts on /mcp (#728) — configurable so non-ACA
    # deployments aren't hardcoded out (ADR 0010/0013).
    mcp_allowed_hosts: str = ""

    # 'redis' is REMOVED (ADR 0039) but stays in the Literal for one cycle so `_build_store` can
    # answer with a migration path instead of a bare parse error.
    secret_store: Literal["env", "openbao", "redis", "azure_key_vault", "aws_secrets_manager"] = (
        "env"  # noqa: S105 — mode selector, not a password
    )
    azure_key_vault_url: str | None = None

    # AWS Secrets Manager: region/credentials resolve ambiently from the task's IAM role.
    aws_secrets_manager_prefix: str = "dataq/"

    # OpenBao / Vault KV v2 (ADR 0039) — the contract is the API, not the vendor.
    openbao_addr: str | None = None
    # Phase-1 auth token — a secret, never logged (the logger-level redactor covers `token` keys and
    # bare token shapes).
    openbao_token: str | None = None
    # KV v2 mount point (`secret` is the dev-mode mount).
    openbao_mount: str = "secret"
    # AppRole auth (ADR 0039 phase 2, #1054).
    openbao_role_id: str | None = None
    openbao_secret_id: str | None = None

    # SecretStore KEY names for the webhook secrets (ADR 0006/0007/0029) — never the secret values.
    adf_webhook_secret_name: str = "adf-webhook-secret"  # noqa: S105 — KV key name, not a secret
    airflow_webhook_secret_name: str = "airflow-webhook-secret"  # noqa: S105 — KV key name
    dbt_webhook_secret_name: str = "dbt-webhook-secret"  # noqa: S105 — KV key name

    # SecretStore key for the workspace Teams webhook URL (token-bearing, so it lives in the
    # SecretStore).
    teams_webhook_secret_name: str | None = None

    # SSRF allowlist (host suffixes) for the per-suite Teams webhook URL, which a suite editor
    # supplies and the server POSTs.
    teams_webhook_allowed_hosts: str = "webhook.office.com,logic.azure.com"

    # SecretStore key for the workspace Slack webhook URL; same shape/policy as Teams.
    slack_webhook_secret_name: str | None = None
    # SSRF allowlist for the Slack webhook host.
    slack_webhook_allowed_hosts: str = "hooks.slack.com"

    # ── Email (SMTP) alerting ──────────────────────────────────────────────── Active only when
    # email_to + email_username + email_password_secret_name are all set (else a quiet no-op).
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_username: str | None = None
    email_from: str | None = None  # defaults to email_username when unset
    email_to: str = ""  # comma-separated recipients; empty → no email alerting
    email_password_secret_name: str | None = None

    # ── Email OTP sign-in (ADR 0032, #734) ─────────────────────────────────── A DELIBERATELY
    # SEPARATE block from the `EMAIL_*` alert mailer above.
    auth_email_smtp_host: str | None = None
    auth_email_smtp_port: int = _DEFAULT_AUTH_EMAIL_SMTP_PORT
    auth_email_username: str | None = None
    auth_email_from: str | None = None
    # SecretStore *key* holding the SMTP password — never the password.
    auth_email_password_secret_name: str | None = None
    # Short: runs INSIDE the sign-in request, so a hung relay must fail fast rather than hold a
    # worker thread for the connect default (minutes).
    auth_email_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # SMTP transport variant (#1146). "implicit" = SMTPS (:465, TLS from first byte — calling
    # starttls() there is the #1146 failure). "none" is a deliberate plaintext downgrade.
    auth_email_tls_mode: AuthEmailTlsMode = _DEFAULT_AUTH_EMAIL_TLS_MODE

    # PEM CA bundle for the OTP mailer only (#1146).
    auth_email_ca_bundle: str | None = None

    # Per-ADMIN cap on POST /admin/auth-email/test, fixed 10-min window (#1147): every call opens a
    # real SMTP connection, and hammering the relay can get the REAL sign-in mailer blocked.
    admin_email_preflight_per_10min: int = Field(default=3, ge=0)

    # Mandatory signup gating (ADR 0032 decision 5) — eligible iff email or domain listed.
    auth_otp_allowed_emails: str = ""
    auth_otp_allowed_domains: str = ""

    # Role a JIT-provisioned OTP signup lands on (ADR 0033 decision 8).
    auth_otp_default_role: Literal["member", "viewer"] = "member"

    # Fixed session horizon, no refresh pair (ADR 0032 decision 3); bounded so no deployment can
    # mint a de-facto immortal browser credential.
    auth_session_ttl_hours: int = Field(default=24, ge=1, le=720)

    # `Secure` on the session cookie.
    auth_session_cookie_secure: bool | None = None

    # Per-EMAIL OTP request cap, 10-min window (#1127) — the middleware can't see the body, so it
    # can't bound codes per mailbox.
    auth_otp_request_per_email_per_10min: int = Field(default=3, ge=0)

    # Constant-time latency FLOOR on otp/request (#1137): the uniform body closed enumeration but
    # the asymmetric work behind it re-opened a timing channel.
    auth_otp_request_min_seconds: float = Field(default=1.0, ge=0, le=30)

    # The same floor on otp/verify (#1141), own number: it hides DB-round-trip milliseconds, not
    # SMTP.
    auth_otp_verify_min_seconds: float = Field(default=0.5, ge=0, le=30)

    # Consecutive failed polls before an alert (#837); fires on the CROSSING only. 0 disables the
    # push (the health badge and #828 warning remain).
    orchestration_poll_failure_alert_threshold: int = 3

    # ── Snowflake probe (Week 1 exit-gate endpoint) ────────────────────────── All optional: unset
    # → the probe still dispatches a run, which fails-soft.
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
        """Normalized admin emails; empty when unset → no admins (safe default)."""
        return frozenset(
            part.strip().lower() for part in self.workspace_admin_emails.split(",") if part.strip()
        )

    def is_admin_email(self, email: str | None) -> bool:
        """True iff `email` is allowlisted — the ONE normalization (strip + lower,
        null-safe) shared by the REST gate and `suite_authz`.
        """
        normalized = (email or "").strip().lower()
        return bool(normalized) and normalized in self.workspace_admin_email_set

    @property
    def cors_allow_origin_list(self) -> list[str]:
        """Parsed CORS origins (stripped, empties dropped). Empty → CORS off."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def mcp_allowed_host_list(self) -> list[str]:
        """Parsed MCP Host allowlist; empty → the ACA-shaped default. Read via this
        property, never the raw field.
        """
        parsed = [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]
        return parsed or list(_MCP_DEFAULT_ALLOWED_HOSTS)

    @field_validator("auth_email_smtp_port", "auth_email_tls_mode", mode="before")
    @classmethod
    def _blank_email_transport_means_default(cls, value: object, info: ValidationInfo) -> object:
        """A BLANK port/TLS-mode env var means "default", not a boot-time parse error
        (#1150 — the local stack blanks the whole AUTH_EMAIL_* block from one
        switch). Neither field is in `_AUTH_EMAIL_REQUIRED`, so this never makes a
        partial block look complete.
        """
        if isinstance(value, str) and not value.strip():
            # .get(..., value) not [...]: an unmapped field would be a boot crash; falling through
            # to the ordinary parse error is the safer failure.
            return _AUTH_EMAIL_TRANSPORT_DEFAULTS.get(info.field_name or "", value)
        return value

    @field_validator("auth_session_cookie_secure", mode="before")
    @classmethod
    def _blank_cookie_secure_means_infer(cls, value: object) -> object:
        """An EMPTY `AUTH_SESSION_COOKIE_SECURE=` means "infer" — the documented
        blank in `.env.app.example` must not refuse to boot.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def auth_otp_allowed_email_set(self) -> frozenset[str]:
        """Normalized allow-listed OTP signup addresses."""
        return _allowed_email_set(self.auth_otp_allowed_emails)

    @property
    def auth_otp_allowed_domain_set(self) -> frozenset[str]:
        """Normalized allow-listed OTP signup domains."""
        return _allowed_domain_set(self.auth_otp_allowed_domains)

    @property
    def oidc_allowed_email_set(self) -> frozenset[str]:
        """Normalized allow-listed generic-OIDC signup addresses (#1386)."""
        return _allowed_email_set(self.oidc_allowed_emails)

    @property
    def oidc_allowed_domain_set(self) -> frozenset[str]:
        """Normalized allow-listed generic-OIDC signup domains (#1386)."""
        return _allowed_domain_set(self.oidc_allowed_domains)

    @property
    def oidc_allowlist_configured(self) -> bool:
        """True iff an app-side OIDC access gate is in force; False = every identity
        the issuer vouches for is admitted (see the field comment).
        """
        return bool(self.oidc_allowed_email_set or self.oidc_allowed_domain_set)

    @property
    def auth_email_configured(self) -> bool:
        """True iff the whole OTP mailer block is present (transport is possible)."""
        return not _missing_auth_email_vars(self)

    @property
    def otp_auth_configured(self) -> bool:
        """True iff email OTP sign-in is ON: complete mailer block AND a non-empty
        allowlist. The frontend twin is the nginx-injected DATAQ_AUTH_MODE (ADR
        0028); the two are kept in sync by docs + `_validate_otp_auth`.
        """
        return self.auth_email_configured and bool(
            self.auth_otp_allowed_email_set or self.auth_otp_allowed_domain_set
        )

    @property
    def azure_auth_configured(self) -> bool:
        return bool(self.azure_tenant_id and self.azure_api_client_id)

    @property
    def azure_api_scope_uri(self) -> str | None:
        if not self.azure_api_client_id:
            return None
        return f"api://{self.azure_api_client_id}/{self.azure_api_scope}"

    @property
    def generic_oidc_configured(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_audience)

    @property
    def dev_bypass_allowed(self) -> bool:
        """Whether the dev-bypass identity may be minted at all — the predicate the
        auth mode ladder binds on, and the membership exemption (ADR 0043 decision 5).
        """
        return (
            self.environment == "dev"
            and self.auth_dev_bypass
            and not self.azure_auth_configured
            and not self.generic_oidc_configured
        )

    @property
    def dev_bypass_active(self) -> bool:
        """Whether dev bypass is the mode the ladder actually SELECTS.

        `dev_bypass_allowed` stays true on a stack that also configures email
        OTP — the ladder picks OTP there and never mints the bypass identity, so
        a check that exempted on `dev_bypass_allowed` alone would be off on the
        default local stack while looking enforced.
        """
        return self.dev_bypass_allowed and not self.otp_auth_configured

    @model_validator(mode="after")
    def _validate_generic_oidc(self) -> "Settings":
        """Reject a half-configured or ambiguous generic-OIDC setup at startup —
        never a silent fall-through to the next auth mode. Mutually exclusive with
        Azure: neither `core.auth` nor `mcp.auth` disambiguates two validators; OTP
        still layers on either (ADR 0032 decision 1).
        """
        issuer = (self.oidc_issuer or "").strip()
        audience = (self.oidc_audience or "").strip()
        if bool(issuer) != bool(audience):
            supplied, absent = (
                ("OIDC_ISSUER", "OIDC_AUDIENCE") if issuer else ("OIDC_AUDIENCE", "OIDC_ISSUER")
            )
            raise ValueError(f"{supplied} is set without {absent} — both are required together.")
        if issuer and audience:
            if not issuer.startswith(("http://", "https://")):
                raise ValueError(
                    f"OIDC_ISSUER must start with http:// or https:// (got {issuer!r})"
                )
            if self.azure_auth_configured:
                raise ValueError(
                    "OIDC_ISSUER/OIDC_AUDIENCE and AZURE_TENANT_ID/AZURE_API_CLIENT_ID are "
                    "mutually exclusive — configure one real-IdP auth mode, not both."
                )
        return self

    @model_validator(mode="after")
    def _validate_secret_store(self) -> "Settings":
        """Reject an unusable secret-store config at STARTUP, not on first (lazy)
        use — otherwise the api boots healthy and dies mid-run in the worker,
        reported as a connection failure (the #954 shape). Gated on the selected
        mode so other modes carry no required fields.
        """
        # Local named without "secret": Ruff S105/Bandit B105 flag the literal comparison as a
        # hardcoded password.
        mode = self.secret_store
        if mode == "openbao":
            # .strip(): a whitespace-only value would pass truthiness and fail much later as "vault
            # unreachable", pointing at the network not the env file.
            role_id = (self.openbao_role_id or "").strip()
            secret_id = (self.openbao_secret_id or "").strip()
            if bool(role_id) != bool(secret_id):
                # Never fall back to the token on half an AppRole — a silent credential-path
                # downgrade is a security regression.
                supplied, absent = (
                    ("OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID")
                    if role_id
                    else ("OPENBAO_SECRET_ID", "OPENBAO_ROLE_ID")
                )
                raise ValueError(
                    f"{supplied} is set without {absent} — AppRole auth needs both. "
                    "Set both, or neither and use OPENBAO_TOKEN."
                )
            # Collected, not short-circuited: report every missing var in one boot.
            missing = []
            if not (self.openbao_addr or "").strip():
                missing.append("OPENBAO_ADDR")
            if not role_id and not (self.openbao_token or "").strip():
                missing.append("OPENBAO_TOKEN (or OPENBAO_ROLE_ID + OPENBAO_SECRET_ID)")
            if missing:
                raise ValueError(f"SECRET_STORE='openbao' requires {' and '.join(missing)}")
            addr = (self.openbao_addr or "").strip()
            if not addr.startswith(("http://", "https://")):
                # httpx would report this as `openbao_unreachable` — a network diagnosis for a one-
                # word config typo.
                raise ValueError(f"OPENBAO_ADDR must start with http:// or https:// (got {addr!r})")
            if not self.openbao_mount.strip().strip("/"):
                # An empty mount builds `/v1//data/<name>`, which 404s every secret.
                raise ValueError("OPENBAO_MOUNT must not be empty")
        elif mode == "azure_key_vault" and not self.azure_key_vault_url:
            raise ValueError("SECRET_STORE='azure_key_vault' requires AZURE_KEY_VAULT_URL")
        elif mode == "aws_secrets_manager" and not self.aws_secrets_manager_prefix.strip():
            # An empty prefix collapses names onto the account's bare namespace.
            raise ValueError("AWS_SECRETS_MANAGER_PREFIX must not be empty")
        elif mode == "redis":
            raise ValueError(_REDIS_STORE_REMOVED)
        return self

    @model_validator(mode="after")
    def _validate_tamper_anchor(self) -> "Settings":
        """Same startup-not-mid-run reasoning as `_validate_secret_store`: an
        unusable anchor config should fail the boot, not the first purge.
        """
        if self.tamper_anchor == "webhook":
            missing = []
            if not self.tamper_anchor_webhook_url.strip():
                missing.append("TAMPER_ANCHOR_WEBHOOK_URL")
            if not self.tamper_anchor_webhook_secret.strip():
                missing.append("TAMPER_ANCHOR_WEBHOOK_SECRET")
            if missing:
                raise ValueError(f"TAMPER_ANCHOR='webhook' requires {' and '.join(missing)}")
            url = self.tamper_anchor_webhook_url.strip()
            if not url.startswith(("http://", "https://")):
                raise ValueError(
                    f"TAMPER_ANCHOR_WEBHOOK_URL must start with http:// or https:// (got {url!r})"
                )
        return self

    @model_validator(mode="after")
    def _validate_otp_auth(self) -> "Settings":
        """Refuse to boot on a HALF-configured OTP block (ADR 0032 decision 2): the
        anti-enumeration uniform response makes an empty allowlist indistinguishable
        from working — nobody would ever see an error, they'd just never get a code.
        Gated on "the operator touched this block at all".
        """
        missing_email = _missing_auth_email_vars(self)
        has_allowlist = bool(self.auth_otp_allowed_email_set or self.auth_otp_allowed_domain_set)
        touched = has_allowlist or len(missing_email) < len(_AUTH_EMAIL_REQUIRED)
        if not touched:
            return self
        # Collected, not short-circuited: report every problem in one boot.
        problems: list[str] = []
        if missing_email:
            problems.append(
                "email OTP sign-in is partially configured — missing "
                + ", ".join(missing_email)
                + " (set them, or clear every AUTH_EMAIL_* / AUTH_OTP_ALLOWED_* "
                "value to turn OTP off)"
            )
        if not has_allowlist:
            problems.append(
                "email OTP sign-in requires a signup allowlist — set "
                "AUTH_OTP_ALLOWED_EMAILS and/or AUTH_OTP_ALLOWED_DOMAINS. "
                "There is no open registration (ADR 0032 decision 5); an empty "
                "allowlist means nobody can ever sign in, and the anti-enumeration "
                "uniform response would hide that from every user who tried"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _validate_email_tls(self) -> "Settings":
        """Fail at boot, not mid-send, on an AUTH_EMAIL_CA_BUNDLE naming no file
        (#1146). Checked even when the OTP block is otherwise untouched.
        """
        bundle = (self.auth_email_ca_bundle or "").strip()
        if bundle and not Path(bundle).is_file():
            raise ValueError(
                f"AUTH_EMAIL_CA_BUNDLE={bundle!r} does not name an existing file. "
                "Set it to the PEM path your OTP mailer's SMTP relay certificate "
                "chains to, or clear it to use the system trust store."
            )
        return self


# Module-level so `auth_email_configured` and the startup validator read the SAME list — "what we
# check" and "what we name in the error" must not drift.
_AUTH_EMAIL_REQUIRED: Final = (
    ("AUTH_EMAIL_SMTP_HOST", "auth_email_smtp_host"),
    ("AUTH_EMAIL_USERNAME", "auth_email_username"),
    ("AUTH_EMAIL_FROM", "auth_email_from"),
    ("AUTH_EMAIL_PASSWORD_SECRET_NAME", "auth_email_password_secret_name"),
)


def _missing_auth_email_vars(settings: "Settings") -> list[str]:
    """Env-var NAMES of the unset/blank OTP mailer fields. `.strip()`, not bare
    truthiness: a whitespace-only host would fail at send time as a DNS error.
    """
    return [
        env_name
        for env_name, field in _AUTH_EMAIL_REQUIRED
        if not str(getattr(settings, field) or "").strip()
    ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
