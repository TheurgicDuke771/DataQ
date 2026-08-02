from functools import lru_cache
from typing import Final, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Migration message for the secret store ADR 0039 removed. Lives here (not in
# `secrets.py`, which imports this module) so the startup validator and the
# `_build_store` fallback cannot drift apart.
_REDIS_STORE_REMOVED: Final = (
    "SECRET_STORE='redis' was removed in ADR 0039 — the store kept credentials in "
    "plaintext. Use SECRET_STORE=openbao (set OPENBAO_ADDR + OPENBAO_TOKEN; "
    "`docker compose up` starts the vault) and re-enter connection credentials. "
    "Then PURGE the old plaintext values, which outlive the switch: "
    "redis-cli --scan --pattern 'dataq:secret:*' | xargs -r redis-cli del. See "
    "docs/adr/0039-openbao-self-hosted-secret-backend.md"
)

# Default MCP transport Host allowlist: the Azure Container Apps internal-ingress
# FQDN shape the frontend nginx proxies to, plus the compose service name and
# loopback. Overridable via MCP_ALLOWED_HOSTS for any other deploy target (#728).
_MCP_DEFAULT_ALLOWED_HOSTS = ("*.azurecontainerapps.io", "api", "localhost", "127.0.0.1")


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

    # Warehouse-native lineage pull (ADR 0034, #858) — dark by default. When true, the
    # beat task refreshes `lineage_edges` for every Snowflake / Unity Catalog connection
    # straight from the warehouse's own lineage views (Snowflake OBJECT_DEPENDENCIES /
    # ACCESS_HISTORY; UC system.access.table_lineage) — no dbt/manifest hop. Off by
    # default because it queries ACCOUNT_USAGE / system.access, which need a grant the
    # connection's principal may not have; turn it on once those grants are in place.
    #   WAREHOUSE_LINEAGE_ENABLED=true
    warehouse_lineage_enabled: bool = False

    # Snowflake GET_LINEAGE per-seed traversal (#892, ADR 0040): how many enumerated
    # tables the Enterprise tier walks per refresh. Each seed costs TWO round trips
    # (upstream + downstream), so this is a latency/cost bound, not a safety one.
    # Overflow walks the first N in catalog order and logs `get_lineage_seeds_truncated`
    # — never a silent cap (a partial traversal reads as a complete graph). <=0 removes
    # the bound. Only the Snowflake GET_LINEAGE tier reads it; the ACCESS_HISTORY /
    # OBJECT_DEPENDENCIES tiers are single set-based queries with no seed list.
    #   WAREHOUSE_LINEAGE_MAX_SEEDS=500
    warehouse_lineage_max_seeds: int = 500

    # Lineage staleness surface (#1091): a warehouse lineage source whose last
    # refresh is older than this many hours is surfaced as STALE in the asset view,
    # independently of error/degraded. The gap this closes: a refresh loop that
    # silently STOPS (no error, no degradation — beat starved, feature later
    # disabled, task deleted) previously rendered as healthy while serving
    # 9-day-old lineage. 2x the daily beat cadence by default, so one skipped
    # wall-clock tick (beat down at the moment) never flags; 0 disables.
    #   LINEAGE_STALE_AFTER_HOURS=48
    lineage_stale_after_hours: int = 48

    # Workspace-wide orchestration-poll staleness alert (#1052): alert when
    # max(last_polled_at) over ALL orchestration connections is older than this.
    # Deliberately derived from DB writes alone and checked from the API process
    # (main.py lifespan loop), NOT the worker: every incident in the #905 class
    # (#852 exporter starvation, #854 row-lock wait, the wedged broker reconnect)
    # had a worker that looked alive and wrote nothing — a per-connection signal
    # computed by the worker cannot fire when the worker is what died. 3x the
    # 10-min poll interval by default; 0 disables the loop entirely.
    #   POLL_STALENESS_ALERT_AFTER_S=1800
    poll_staleness_alert_after_s: int = 1800

    sample_failures_retention_days: int = 30

    # OTP-code retention sweep (#1136). `otp_codes.email` is stored in plaintext
    # (deliberately — the "newest live code for this address" lookup needs it), so
    # each row is a sign-in-attempt timestamp against an address: PII with no
    # operational value once spent/expired. Comfortably longer than the 10-minute
    # code TTL (`otp_service.CODE_TTL_MINUTES`) — this is hygiene, not a security
    # control (the caps in `otp_service` are the security), so there is no reason
    # to purge aggressively. Daily cadence, same posture as the sample-failures
    # sweep above. <=0 is guarded in `otp_service.purge_expired_codes` itself (a
    # no-op, returns 0) — NOT merely "expires instantly": that function's cutoff is
    # `now - older_than_hours`, so a non-positive value collapses it to "now" and
    # would delete EVERY row, including ones just minted, without the guard.
    otp_codes_retention_hours: int = 24

    # Stuck-run reaper (#309): a run committed `queued` (before `send_task`) — or
    # left `running` by a worker that died mid-execution — past this age is driven
    # to terminal `failed` by the beat janitor so it can't linger forever. Must
    # comfortably exceed the longest plausible suite run so a slow-but-alive run is
    # never reaped (a false reap self-corrects when the worker later commits its
    # real outcome, but would emit a spurious alert).
    stuck_run_threshold_minutes: int = 60
    # Recycle a Celery prefork child once its resident size passes this (KiB), so a
    # large materialisation cannot ratchet the worker baseline up run-over-run
    # (#755). 0 disables. 1_500_000 KiB (~1.4 GiB) sits under the 2 GiB prod worker
    # with headroom for the parent + a starting child.
    worker_max_memory_per_child_kb: int = 1_500_000

    # Orphan-asset sweep (#770, ADR 0034 — "asset rows accrete; last_seen + a
    # sweep, not deletes, is the cleanup posture"). An asset whose `last_seen`
    # hasn't advanced in this many days AND that no suite/run/lineage_edge (and,
    # once #761 lands, incident) still references is deleted by the beat janitor.
    # Deliberately generous — must comfortably outlive the slowest suite schedule
    # and the lineage-refresh poll cadence, or a legitimately-live asset would be
    # swept and immediately re-created on the next refresh. <=0 disables the sweep.
    asset_orphan_retention_days: int = 30

    # Warehouse inventory sync (#919, ADR 0040): per-connection cap on tables
    # synced per tick. Overflow syncs the first N in catalog order and logs
    # `inventory_sync_truncated` — never a silent cap. <=0 removes the bound.
    asset_inventory_max_tables: int = 2000

    # Orphan-SECRET sweep (#1059). A credential write is not part of the DB
    # transaction that creates the row referencing it, so any failure after the
    # write leaves the credential in the vault forever, unreferenced. Nothing else
    # cleans these up: `SecretStore.delete` runs only on an explicit entity delete,
    # and the #838 expiry sweep is driven off connection rows, so an orphan is
    # invisible to it too.
    #
    # `secret_orphan_grace_days` must comfortably exceed the longest window in which
    # a secret legitimately exists without its row committed. <=0 disables the sweep.
    #
    # `secret_orphan_purge` is the destructive half and is OFF by default,
    # deliberately breaking the pattern of the other janitors: what gets deleted here
    # is a live warehouse credential, unrecoverable once purged, so the first release
    # must make the problem visible rather than act on it. Turn it on once the
    # reported counts have been reviewed on a real vault.
    secret_orphan_grace_days: int = 30
    secret_orphan_purge: bool = False

    # Beat liveness watchdog (#904). Three post-deploy rolls have left the worker
    # "alive but doing nothing" — container Healthy, Celery ready, zero scheduled
    # tasks executed, only the DB telling the truth — each cleared by a manual
    # revision restart. The watchdog makes the worker notice and exit so the
    # platform restarts it. `beat_watchdog_stale_after_s` must be several times
    # the fastest beat interval (60s `dispatch_due_schedules`), so a slow tick is
    # never mistaken for a wedge; it doubles as the boot grace period. <=0
    # disables the watchdog entirely (a clean off-switch, like the sweeps above).
    beat_watchdog_stale_after_s: int = 600
    beat_watchdog_interval_s: int = 60

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
    rate_limit_webhook_per_minute: int = (
        120  # per provider + client-IP bucket, /api/v1/orchestration/events/* (#785) —
        # each provider (adf/airflow/dbt) has its own bucket at this cap, NOT a
        # per-IP total; the total is rate_limit_webhook_ip_per_minute below.
    )
    rate_limit_webhook_ip_per_minute: int = (
        240  # per-IP ceiling across ALL webhook buckets from one IP (#785) — bounds
        # the aggregate a single IP can spend across provider buckets.
    )
    rate_limit_auth_per_minute: int = (
        10  # per-IP bucket for /api/v1/auth/* (#1127) — a strict cap on the
        # unauthenticated OTP mint/verify surface, checked before the bearer
        # branch so a token can't dodge it. The per-email counters (ADR 0032
        # §8, #734) are a separate, service-level layer this middleware can't
        # implement (no access to the parsed request body).
    )
    rate_limit_ip_per_minute: int = (
        1200  # per-IP ceiling across all bearer buckets (rotated-token backstop)
    )
    rate_limit_xff_trusted_hops: int = (
        1  # count of trusted proxies appending XFF; pick entry hops-from-right
    )
    # Per-IP buckets key on an address PREFIX, not the full address (#789): a
    # client egressing through a rotating NAT/proxy pool spreads requests across
    # many /32s in one allocation, so no single full-address bucket ever fills —
    # observed live (a 200-request burst landed on 11 distinct /24-pool IPs, none
    # near the cap). Grouping by prefix makes the pool share one bucket. Trade-off:
    # a prefix that legitimately hosts many independent clients (CGNAT) shares the
    # cap — tune the masks per deployment. /32 and /128 disable grouping.
    rate_limit_ipv4_prefix: int = Field(default=24, ge=8, le=32)
    rate_limit_ipv6_prefix: int = Field(default=64, ge=32, le=128)

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

    # Host values the FastMCP transport guard accepts on `/mcp` (#728). The
    # deploy-target coupling used to be hardcoded in `mcp/server.py`, which is the
    # one thing ADR 0010/0013 says must never live in app code — any non-ACA
    # deployment whose proxy forwards a different upstream Host got a 421 with no
    # way to configure it. Comma-separated (not list[str]) for the same
    # pydantic-settings reason as `workspace_admin_emails`; read it via
    # `mcp_allowed_host_list`. Empty keeps the ACA-shaped default.
    #   MCP_ALLOWED_HOSTS=*.example.internal,api,localhost
    mcp_allowed_hosts: str = ""

    # 'redis' is REMOVED (ADR 0039 — it kept credentials in plaintext) but stays in
    # the Literal for one cycle so `_build_store` can answer with a migration path
    # instead of pydantic emitting a bare "Input should be …" that names no cause.
    secret_store: Literal["env", "openbao", "redis", "azure_key_vault"] = (
        "env"  # noqa: S105 — mode selector, not a password
    )
    azure_key_vault_url: str | None = None

    # OpenBao / Vault KV v2 (ADR 0039). The contract is the API, not the vendor —
    # OPENBAO_ADDR may point at OpenBao (what we ship), Vault Community/Enterprise,
    # or HCP Vault. Required when SECRET_STORE=openbao; validated in `_build_store`
    # rather than here so the other modes don't have to carry them.
    openbao_addr: str | None = None
    # Phase-1 auth is a raw token (ADR 0039 decision 4; AppRole is #1054). A secret —
    # never logged: it travels in the X-Vault-Token header, and the logger-level
    # redactor covers `token` keys and bare token shapes.
    openbao_token: str | None = None
    # KV v2 mount point. `secret` is what dev mode mounts; a production vault often
    # mounts per-team paths instead.
    openbao_mount: str = "secret"
    # AppRole auth (ADR 0039 phase 2, #1054) — what a production self-hosted
    # deployment should run instead of a static token. `role_id` is an IDENTIFIER
    # (safe in config); `secret_id` is the credential. Set BOTH or neither: a partial
    # config is rejected at startup rather than silently falling back to
    # OPENBAO_TOKEN, because quietly downgrading a credential path is a security
    # regression, not a convenience.
    openbao_role_id: str | None = None
    openbao_secret_id: str | None = None

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

    # ── Email OTP sign-in (ADR 0032, #734) ───────────────────────────────────
    # A DELIBERATELY SEPARATE block from the `EMAIL_*` alert mailer above. The two
    # have opposite contracts (ADR 0032 decision 7): an alert send is best-effort
    # and no-ops when unconfigured, while an OTP send is synchronous on the sign-in
    # request path with its failures surfaced to the caller. Sharing one config
    # block would let a rate-limited or misconfigured *alert* channel block
    # sign-in — and vice versa.
    #
    # OTP mode is ON when this block is COMPLETE (host + username + from +
    # password-secret-name) **and** at least one signup allowlist entry exists.
    # `_validate_otp_auth` below refuses to boot on any partial configuration,
    # naming the missing vars — ADR 0032 decision 2's fail-closed contract.
    auth_email_smtp_host: str | None = None
    auth_email_smtp_port: int = 587
    auth_email_username: str | None = None
    auth_email_from: str | None = None
    # SecretStore *key* holding the SMTP password — never the password.
    auth_email_password_secret_name: str | None = None
    # Seconds before the SMTP submission gives up. Short: this runs INSIDE the
    # sign-in request, so a hung relay must fail fast into the 502 rather than hold
    # a worker thread for the connect default (minutes).
    auth_email_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # Signup gating — mandatory, no open registration (ADR 0032 decision 5). An
    # address is eligible iff it is in AUTH_OTP_ALLOWED_EMAILS, or its domain is in
    # AUTH_OTP_ALLOWED_DOMAINS. Comma-separated strings, not list[str], for the same
    # pydantic-settings reason as `workspace_admin_emails`; read them through the
    # normalized frozenset properties below, never the raw fields.
    #   AUTH_OTP_ALLOWED_EMAILS=ada@acme.io,grace@acme.io
    #   AUTH_OTP_ALLOWED_DOMAINS=acme.io
    auth_otp_allowed_emails: str = ""
    auth_otp_allowed_domains: str = ""

    # Session lifetime (ADR 0032 decision 3) — a FIXED horizon with no refresh pair;
    # re-running the OTP flow is the refresh. Bounded above so a deployment cannot
    # configure a de-facto immortal browser credential.
    auth_session_ttl_hours: int = Field(default=24, ge=1, le=720)

    # `Secure` on the session cookie. `None` (the default) infers it per request
    # from `X-Forwarded-Proto: https` (or a directly-HTTPS request), which is the
    # only HTTPS signal that survives the nginx proxy (ADR 0028 §5). Force it with
    # `true`/`false` when a deployment's proxy does not set that header. This is
    # NOT cosmetic: a hard-coded `Secure` makes the cookie silently vanish on a
    # plain-HTTP dev stack, which is the single most likely dev-vs-prod footgun in
    # the whole feature.
    auth_session_cookie_secure: bool | None = None

    # Per-EMAIL OTP request cap (#1127 second half), fixed 10-minute window. This
    # is the tight screw on the mint surface: the middleware's per-IP `auth` class
    # (RATE_LIMIT_AUTH_PER_MINUTE) is the backstop, but it cannot see the request
    # BODY and therefore cannot bound how many codes one mailbox receives from a
    # botnet. Enforced in the service layer and ACTIVE EVEN WHEN
    # RATE_LIMIT_ENABLED=false — dev and E2E disable the middleware, and an OTP
    # mail-bomb control that a test harness silently switches off is not a control.
    # 0 disables it (not recommended).
    auth_otp_request_per_email_per_10min: int = Field(default=3, ge=0)

    # ── Connection poll-health alerting (#837) ───────────────────────────────
    # How many CONSECUTIVE failed orchestration polls a connection may rack up
    # before DataQ pushes an alert. At the 10-minute poll cadence the default (3)
    # means ~30 minutes of a genuinely dead poll, which rides out the transient
    # blips (a 502, a restarting orchestrator) that would otherwise cry wolf. The
    # alert fires on the CROSSING only, so a connection dead for a week alerts once,
    # not 1,008 times. 0 disables the push entirely (the connection-health badge and
    # the lineage warning from #828 remain either way).
    orchestration_poll_failure_alert_threshold: int = 3

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
    def mcp_allowed_host_list(self) -> list[str]:
        """Parsed MCP transport Host allowlist (stripped, empties dropped).

        Empty → `_MCP_DEFAULT_ALLOWED_HOSTS`, which keeps the Azure Container Apps
        deployment working with no config. Read via this property, never the raw field.
        """
        parsed = [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]
        return parsed or list(_MCP_DEFAULT_ALLOWED_HOSTS)

    @field_validator("auth_session_cookie_secure", mode="before")
    @classmethod
    def _blank_cookie_secure_means_infer(cls, value: object) -> object:
        """An EMPTY `AUTH_SESSION_COOKIE_SECURE=` means "infer", not a parse error.

        Every optional key in `.env.app.example` ships blank, and pydantic parses a
        blank string for `bool | None` as an invalid boolean — so shipping this key
        blank (which is the documented, recommended setting) would refuse to boot.
        A three-state flag needs a spelling for its third state, and in a dotenv
        that spelling is "nothing after the =".
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def auth_otp_allowed_email_set(self) -> frozenset[str]:
        """Normalized (strip + lower) allow-listed signup addresses.

        Same normalization as `is_admin_email` and the `uq_users_email_lower`
        index — the identity surface has exactly one rule (ADR 0032 decision 6).
        """
        return frozenset(
            part.strip().lower() for part in self.auth_otp_allowed_emails.split(",") if part.strip()
        )

    @property
    def auth_otp_allowed_domain_set(self) -> frozenset[str]:
        """Normalized allow-listed signup domains. A leading `@` is tolerated and
        stripped, because `@acme.io` is what an operator naturally writes and a
        silently-never-matching allowlist is a fail-OPEN-looking config error (every
        address reads as ineligible, and the uniform response hides it)."""
        return frozenset(
            part.strip().lower().lstrip("@")
            for part in self.auth_otp_allowed_domains.split(",")
            if part.strip().lstrip("@")
        )

    @property
    def auth_email_configured(self) -> bool:
        """True iff the whole OTP mailer block is present (transport is possible)."""
        return not _missing_auth_email_vars(self)

    @property
    def otp_auth_configured(self) -> bool:
        """True iff email OTP sign-in is ON: a complete mailer block AND a non-empty
        signup allowlist. This is the backend half of ADR 0032 decision 2's two
        coordinated mode selectors — its frontend twin is the nginx-injected
        `DATAQ_AUTH_MODE` enum, which the backend never reads (ADR 0028). Documented
        together in `.env.app.example` and `deploy/README.md`; they cannot be derived
        from one another, so they are kept in sync by documentation and by
        `_validate_otp_auth` refusing every partial state on this side."""
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

    @model_validator(mode="after")
    def _validate_secret_store(self) -> "Settings":
        """Reject an unusable secret-store config **at startup**, not on first use.

        `_build_store` is reached lazily — through a FastAPI `Depends` or a Celery
        task — so without this the api boots, answers `/healthz`, and only dies when
        something reaches for a credential: in the worker, mid-run, reported as a
        connection failure. That is the #954 shape the store's own error handling
        exists to prevent, and it would be perverse to reproduce it in the config
        that configures it.

        Gated on the selected mode so the other modes carry no required fields
        (a Key Vault deploy must not have to set `OPENBAO_*`, and vice versa).
        """
        # Bound to a local whose name has no "secret" in it: both Ruff S105 and
        # Bandit B105 flag `secret_store == "<literal>"` as a hardcoded password,
        # and one local reads better than stacking two suppressions on three lines.
        mode = self.secret_store
        if mode == "openbao":
            # `.strip()` because a whitespace-only value is not a value: it would pass
            # a bare truthiness check and then fail much later as "vault unreachable"
            # or a 403, pointing the operator at the network instead of the env file.
            role_id = (self.openbao_role_id or "").strip()
            secret_id = (self.openbao_secret_id or "").strip()
            if bool(role_id) != bool(secret_id):
                # Half an AppRole. Falling back to the token here would be the
                # dangerous reading: an operator who set ROLE_ID meant to stop using
                # the static token, and a silent downgrade is exactly the failure the
                # harness webserver_config guards against on the auth side.
                supplied, absent = (
                    ("OPENBAO_ROLE_ID", "OPENBAO_SECRET_ID")
                    if role_id
                    else ("OPENBAO_SECRET_ID", "OPENBAO_ROLE_ID")
                )
                raise ValueError(
                    f"{supplied} is set without {absent} — AppRole auth needs both. "
                    "Set both, or neither and use OPENBAO_TOKEN."
                )
            # Collected, not short-circuited: an operator missing both should learn
            # both in one run rather than fix one, re-run, and discover the other.
            missing = []
            if not (self.openbao_addr or "").strip():
                missing.append("OPENBAO_ADDR")
            if not role_id and not (self.openbao_token or "").strip():
                missing.append("OPENBAO_TOKEN (or OPENBAO_ROLE_ID + OPENBAO_SECRET_ID)")
            if missing:
                raise ValueError(f"SECRET_STORE='openbao' requires {' and '.join(missing)}")
            addr = (self.openbao_addr or "").strip()
            if not addr.startswith(("http://", "https://")):
                # httpx raises UnsupportedProtocol, which is an HTTPError, so the store
                # would report this as `openbao_unreachable` — a network diagnosis for
                # a one-word config typo.
                raise ValueError(f"OPENBAO_ADDR must start with http:// or https:// (got {addr!r})")
            if not self.openbao_mount.strip().strip("/"):
                # An empty mount builds `/v1//data/<name>`, which the vault 404s —
                # i.e. every credential in the workspace reports as missing.
                raise ValueError("OPENBAO_MOUNT must not be empty")
        elif mode == "azure_key_vault" and not self.azure_key_vault_url:
            raise ValueError("SECRET_STORE='azure_key_vault' requires AZURE_KEY_VAULT_URL")
        elif mode == "redis":
            raise ValueError(_REDIS_STORE_REMOVED)
        return self

    @model_validator(mode="after")
    def _validate_otp_auth(self) -> "Settings":
        """Refuse to boot on a HALF-configured email OTP block (ADR 0032 decision 2).

        The failure this prevents is specific and nasty: a deployment that comes up,
        serves `/healthz`, renders a sign-in screen — and cannot log anybody in,
        because the mailer has no password secret or the allowlist is empty. Since
        `otp/request` returns the SAME uniform response for an ineligible address
        (anti-enumeration, decision 4), an empty allowlist is *indistinguishable
        from working* to the person trying to sign in. Nobody would ever see an
        error; they would just never receive a code.

        Same shape and same reasoning as `_validate_secret_store` above: fail at
        startup naming the missing vars, rather than lazily at first use.

        Gated on "the operator touched this block at all", so a Azure-only or
        dev-bypass deployment carries none of these fields.
        """
        missing_email = _missing_auth_email_vars(self)
        has_allowlist = bool(self.auth_otp_allowed_email_set or self.auth_otp_allowed_domain_set)
        touched = has_allowlist or len(missing_email) < len(_AUTH_EMAIL_REQUIRED)
        if not touched:
            return self
        # Collected, not short-circuited (the `_validate_secret_store` precedent): an
        # operator missing three vars should learn all three in one boot, not fix one
        # and rediscover the next on every redeploy.
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


# The four vars that make the OTP mailer transport possible. Module-level so the
# `auth_email_configured` property and the startup validator read the SAME list —
# the drift between "what we check" and "what we name in the error" is exactly the
# bug a fail-closed validator exists to avoid.
_AUTH_EMAIL_REQUIRED: Final = (
    ("AUTH_EMAIL_SMTP_HOST", "auth_email_smtp_host"),
    ("AUTH_EMAIL_USERNAME", "auth_email_username"),
    ("AUTH_EMAIL_FROM", "auth_email_from"),
    ("AUTH_EMAIL_PASSWORD_SECRET_NAME", "auth_email_password_secret_name"),
)


def _missing_auth_email_vars(settings: "Settings") -> list[str]:
    """The env-var NAMES of the OTP mailer fields that are unset/blank.

    `.strip()`, not bare truthiness: a whitespace-only SMTP host is not a host, and
    would otherwise pass configuration and fail at send time as a DNS error —
    pointing the operator at their network instead of their env file.
    """
    return [
        env_name
        for env_name, field in _AUTH_EMAIL_REQUIRED
        if not str(getattr(settings, field) or "").strip()
    ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
