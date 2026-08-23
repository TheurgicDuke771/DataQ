import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

# ── Status / type value sets (TEXT + CHECK; not native PG enums for migration ergonomics) ──
CONNECTION_TYPES = (
    "snowflake",
    "adls_gen2",
    "s3",
    "unity_catalog",
    "iceberg",
    "adf",
    "airflow",
    "dbt",
)
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
# Severity tiers (ADR 0005) bear health-score weight; operational statuses (#122) carry NO weight
# and MUST be excluded from the health-score N.
RESULT_SEVERITY_TIERS = ("pass", "warn", "fail", "critical")
# Backwards-compatible alias (was private until #889).
_RESULT_SEVERITY_TIERS = RESULT_SEVERITY_TIERS
RESULT_OPERATIONAL_STATUSES = ("skip", "error")
RESULT_STATUSES = _RESULT_SEVERITY_TIERS + RESULT_OPERATIONAL_STATUSES
# Failing tiers → rank, worst last — the ONE shared run-outcome ordering (#655); distinct from the
# ADR 0005 health-penalty weights in dashboard_service.
SEVERITY_RANK: dict[str, int] = {
    tier: rank for rank, tier in enumerate((t for t in _RESULT_SEVERITY_TIERS if t != "pass"), 1)
}
# The failing-tier set, single-sourced with the rank order; alerting imports it.
FAILING_TIERS: tuple[str, ...] = tuple(SEVERITY_RANK)


def worst_severity(statuses: Iterable[str]) -> str | None:
    """Highest failing tier present (``critical`` > ``fail`` > ``warn``) or ``None``
    when none breached — `pass`/`skip`/`error` never rank (#655).
    """
    present = [s for s in statuses if s in FAILING_TIERS]
    return max(present, key=lambda s: SEVERITY_RANK[s]) if present else None


# Monitor-kind discriminator (ADR 0012; `comparison` per ADR 0014/0015).
CHECK_KINDS = ("expectation", "freshness", "volume", "schema_drift", "anomaly", "comparison")
COMPARISON_KIND = "comparison"
# Check engines (ADR 0036) — WHO evaluates, orthogonal to `kind`.
CHECK_ENGINES = ("gx", "dmf", "dqx", "dataplex")
GX_ENGINE = "gx"
# DQ dimensions (ADR 0038) — third axis, orthogonal to `kind` and `engine`.
DQ_DIMENSIONS = (
    "accuracy",
    "completeness",
    "consistency",
    "integrity",
    "timeliness",
    "uniqueness",
    "validity",
)
PIPELINE_RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ORCHESTRATION_PROVIDERS = ("adf", "airflow", "dbt")
PERMISSIONS = ("view", "edit", "admin")
# Coarse workspace roles (ADR 0033) — orthogonal to per-suite PERMISSIONS; neither replaces the
# other.
ADMIN_ROLE = "admin"
DEFAULT_WORKSPACE_ROLE = "member"
VIEWER_ROLE = "viewer"
WORKSPACE_ROLES = (ADMIN_ROLE, DEFAULT_WORKSPACE_ROLE, VIEWER_ROLE)
ENVS = ("dev", "qa", "uat", "prod")
# Per-suite alert threshold: 'fail' = fail/critical only, 'warn' = warn+, 'always' = all.
ALERT_ON_POLICIES = ("fail", "warn", "always")

# Incident lifecycle (ADR 0034 decision 4, #761): open → acknowledged → resolved; a resolved row
# never reopens (a new incident links via `prior_incident_id`).
INCIDENT_STATUSES = ("open", "acknowledged", "resolved")
INCIDENT_ACTIVE_STATUSES = ("open", "acknowledged")
# Who resolved: a user, or the engine on the first passing result. NULL until resolved.
INCIDENT_RESOLVED_BY = ("user", "auto")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def _in_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


class User(Base):
    """A human identity — one row per normalized email. `aad_object_id` is nullable
    (OTP users have none, ADR 0032) and despite the name holds ANY OIDC issuer's
    subject claim (a rename is a breaking schema change); `oidc_issuer` disambiguates.
    """

    __tablename__ = "users"
    # lower(email) unique INDEX — Postgres can't express a unique constraint over an expression.
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        _in_check("role", WORKSPACE_ROLES, "role_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    aad_object_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # Issuer that authenticated this row; descriptive pairing, not an identity key.
    oidc_issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    # True once self-set via PATCH /me (#1139); while False, sign-in paths may sync the name from
    # token claims — once True they never overwrite it.
    display_name_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Stored half of workspace-admin (ADR 0033, #740); WORKSPACE_ADMIN_EMAILS is only a bootstrap
    # seed + break-glass.
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{DEFAULT_WORKSPACE_ROLE}'")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ApiKey(Base):
    """A DataQ-issued PAT (ADR 0026 phase 1, #461). Only the SHA-256 digest is stored
    (never in the SecretStore); authenticates as its owner through `get_current_user`,
    inheriting the owner's grants — no separate authz model.
    """

    __tablename__ = "api_keys"
    # Unique INDEX, not constraint: a constraint would be auto-named differently in create_all test
    # DBs vs prod (#990 parity), and code keying on constraint names would diverge.
    __table_args__ = (Index("uq_api_keys_key_hash", "key_hash", unique=True),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # First characters of the token (`dq_live_ab12`) — safe to list/log.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class UserSession(Base):
    """An email-OTP browser session (ADR 0032 decision 3): opaque `dq_sess_` token,
    SHA-256 digest stored. No refresh pair — fixed `expires_at`, re-running OTP is the
    refresh; expiry/revocation enforced at the auth seam on every resolve. Named
    `UserSession` because `Session` collides with SQLAlchemy's.
    """

    __tablename__ = "sessions"
    # Unique INDEX, not constraint — same #990 name-parity reason as ApiKey; doubles as the O(1)
    # auth lookup index.
    __table_args__ = (Index("uq_sessions_token_hash", "token_hash", unique=True),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256, not a KDF: high-entropy machine token, per-request verify must stay an indexed lookup
    # (ADR 0026 rationale).
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class OtpCode(Base):
    """A one-time email sign-in code (ADR 0032 decision 4). The protection is the
    caps (TTL, single use, MAX_ATTEMPTS, supersede-on-re-request), not the hash.
    Keyed on normalized email and deliberately NOT FK'd to `users` — a code is
    requested before any user row need exist. `consumed_at` = redeemed OR superseded.
    """

    __tablename__ = "otp_codes"
    __table_args__ = (Index("ix_otp_codes_email_created_at", "email", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Incremented by an atomic UPDATE … RETURNING *before* the comparison, so two concurrent guesses
    # can't share one pre-increment value (otp_service.verify_code).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()


class Asset(Base):
    """A first-class data asset (ADR 0034): the browse/reason grain; suites stay the
    execution/authz grain. Identity = the OpenLineage dataset naming spec, adopted
    verbatim — (namespace, name) unique; DEV/QA accounts are DISTINCT assets by design.
    `connection_id` is a provenance hint, not identity (SET NULL on delete).
    """

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("namespace", "name", name="uq_assets_namespace_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    namespace: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Metadata, not identity (the OL spec keys namespace on physical isolation).
    env: Mapped[str | None] = mapped_column(String(16))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="SET NULL")
    )
    #: Warehouse-native column classification (G3, #433): the governance FLOOR of the redaction
    #: ladder — a rung no suite policy can lift.
    column_tags: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    #: Distinguishes "never looked" from "looked and found none" for auditors.
    column_tags_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Set only via the workspace-Admin-only PATCH /assets/{id} (#760).
    description: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        _in_check("type", CONNECTION_TYPES, "type_valid"),
        _in_check("env", ENVS, "env_valid"),
        UniqueConstraint("name", "env", name="uq_connections_name_env"),
        # Orchestration providers are singletons per (type, env) (ADR 0004, #72); partial index —
        # datasources may legitimately repeat per env.
        Index(
            "uq_connections_orchestrator_type_env",
            "type",
            "env",
            unique=True,
            postgresql_where=text(
                "type IN (" + ", ".join(f"'{p}'" for p in ORCHESTRATION_PROVIDERS) + ")"
            ),
        ),
        Index("ix_connections_created_by", "created_by"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Nullable by design: NULL during the row-flush→secret-write window and for credential-less auth
    # modes (managed identity / IAM role, ADR 0010/0011).
    secret_ref: Mapped[str | None] = mapped_column(String(256))
    # Per-engine capability flags (ADR 0036 §3), probe-written; classified remediation only, never
    # raw exception text.
    engine_capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Provenance, not lifecycle ownership — SET NULL; RESTRICT would make a user un-erasable (GDPR
    #: Art 17, #432/#1319).
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # ── Poll health (#828): a failing poll as a fact about the connection ──── NULL on non-
    # orchestration / never-polled rows = "unknown", never healthy.
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Classified, redaction-safe reason — never raw exception text (#536 precedent).
    last_poll_error: Mapped[str | None] = mapped_column(String(512))
    # Consecutive failures, reset on success; a counter (not a bool) so the UI can say "failing for
    # ~6 days" and thresholds can ride on it.
    consecutive_poll_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # When a FAILING alert was actually DELIVERED (#843); NULL = none outstanding.
    health_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Credential expiry (#838) ────────────────────────────────────────────── Derived CACHE of
    # the credential's self-stated expiry.
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Stamped on every read attempt (#1024): NULL here = never looked; set here + NULL above =
    # looked, genuinely no expiry.
    credential_expiry_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Warehouse-native lineage refresh state (#858) ──────────────────────────── All NULL on a
    # connection never refreshed and on non-warehouse types.
    lineage_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last refresh time + answering tier (`LineageTier`), so the UI can qualify the graph instead of
    # showing a bare empty state (#828).
    lineage_last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lineage_last_tier: Mapped[str | None] = mapped_column(String(64))
    lineage_degraded_reason: Mapped[str | None] = mapped_column(String(512))
    # Classified, redaction-safe (mirrors last_poll_error). NULL = last refresh ran.
    lineage_last_error: Mapped[str | None] = mapped_column(String(512))

    # ── Inventory-sync outcome state (#1104), mirrors lineage_last_* ───────────── (the connection
    # test's SELECT 1 never exercises the enumeration query).
    inventory_sync_last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Classified, redaction-safe. NULL means the last attempt succeeded.
    inventory_sync_last_error: Mapped[str | None] = mapped_column(String(512))
    # Start of the CURRENT failure streak ("failing since <ts>"); set on the first failure after a
    # success, cleared on the next success, NULL while healthy.
    inventory_sync_failing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Zero-table enumeration state (#1242) ───────────────────────────────────── Zero tables is
    # NOT an error (INFORMATION_SCHEMA is privilege-filtered; an empty DB is legitimate).
    inventory_sync_last_table_count: Mapped[int | None] = mapped_column(Integer)
    # Set when the count drops N>0 → 0, untouched while it stays 0, cleared when >0.
    inventory_sync_zero_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectionVersion(Base):
    """Immutable snapshot of a connection's editable, NON-secret state, written on
    create and each successful name/config update. The credential is never copied —
    a rotation records NO version. `version_no` is a per-connection sequence from 1;
    rows cascade-delete with the connection but survive their author (SET NULL).
    """

    __tablename__ = "connection_versions"
    __table_args__ = (
        UniqueConstraint("connection_id", "version_no", name="uq_connection_versions_conn_version"),
        Index("ix_connection_versions_connection_id", "connection_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # type/env are immutable but snapshotted for a self-contained record.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()

    author: Mapped["User | None"] = relationship()

    @property
    def changed_by_name(self) -> str | None:
        """Author display name/email, or None for a system actor / removed user.
        Callers that serialize this must `selectinload(ConnectionVersion.author)`.
        """
        return (self.author.display_name or self.author.email) if self.author else None


class Suite(Base):
    __tablename__ = "suites"
    __table_args__ = (
        Index("ix_suites_connection_id", "connection_id"),
        Index("ix_suites_created_by", "created_by"),
        Index("ix_suites_asset_id", "asset_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=False
    )
    # Datasource-shaped run target (#215), resolved by `run_target.resolve_target`.
    target: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Asset the target resolves to (ADR 0034); fail-soft NULL rather than blocking the save.
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL")
    )
    # Column-redaction policy for failing-row samples (#415): `{"identifier_column": str,
    # "pii_columns": [str]}` — identifier always shown, pii always masked.
    column_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    #: Provenance, not lifecycle ownership — SET NULL; RESTRICT would make a user un-erasable (GDPR
    #: Art 17, #432/#1319).
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    checks: Mapped[list["Check"]] = relationship(
        back_populates="suite", cascade="all, delete-orphan"
    )


class Check(Base):
    __tablename__ = "checks"
    __table_args__ = (
        _in_check("kind", CHECK_KINDS, "kind_valid"),
        _in_check("engine", CHECK_ENGINES, "engine_valid"),
        _in_check("dimension", DQ_DIMENSIONS, "dimension_valid"),
        # ADR 0015: source ref presence ⇔ kind='comparison', DB-enforced so the run path can trust a
        # comparison row always has a source.
        CheckConstraint(
            "(kind = 'comparison') = (source_connection_id IS NOT NULL)",
            name="comparison_source_presence",
        ),
        Index("ix_checks_suite_id", "suite_id"),
        Index("ix_checks_source_connection_id", "source_connection_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Monitor-kind discriminator (ADR 0012); the run path dispatches on this.
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'expectation'")
    )
    # Evaluating engine (ADR 0036).
    engine: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'gx'"))
    expectation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # DQ dimension (ADR 0038): derived at author time then STORED (SQL GROUP BY + override survival,
    # #889).
    dimension: Mapped[str | None] = mapped_column(String(32))
    # Comparison baseline connection (ADR 0015); non-NULL exactly for kind='comparison'.
    source_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="RESTRICT")
    )
    # Optional severity thresholds (ADR 0005). NULL → plain pass/fail.
    warn_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    fail_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    critical_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Mute alerts until this moment (UTC); NULL/past = active.
    alert_snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    suite: Mapped["Suite"] = relationship(back_populates="checks")


class MonitorBaseline(Base):
    """The CURRENT reference state a stateful monitor kind diffs against (#592,
    ADR 0012) — UNIQUE per check; re-baseline REPLACES the row (history lives in
    `results`). One kind-shaped JSONB serves schema_drift and anomaly (#593).
    Shape metadata only, never row data — no PII / retention involvement.
    """

    __tablename__ = "monitor_baselines"
    __table_args__ = (
        UniqueConstraint("check_id", name="uq_monitor_baselines_check"),
        _in_check("kind", CHECK_KINDS, "kind_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checks.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized for queryability; the check's kind is the authority.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL = captured automatically by the run path (first run of the check).
    captured_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CheckVersion(Base):
    """Immutable snapshot of a check's editable state, written on create and each
    update; source for the history drawer and restore (#283 — restore re-validates
    and records a NEW version; history is additive). `version_no` is a per-check
    sequence from 1; rows cascade-delete with the check, survive their author.
    """

    __tablename__ = "check_versions"
    __table_args__ = (
        UniqueConstraint("check_id", "version_no", name="uq_check_versions_check_version"),
        Index("ix_check_versions_check_id", "check_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checks.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # kind is immutable but snapshotted for a self-contained record.
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Snapshotted so restore reproduces the evaluator (ADR 0036); deliberately NOT CHECK-constrained
    # — history must stay writable if the vocabulary changes.
    engine: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'gx'"))
    expectation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # Snapshotted like every editable field; deliberately NOT CHECK-constrained here (a snapshot
    # records what was).
    dimension: Mapped[str | None] = mapped_column(String(32))
    # Plain UUID, deliberately NO FK (ADR 0015/0020): a snapshot must outlive a deleted source
    # connection, so history never blocks a connection delete.
    source_connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warn_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    fail_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    critical_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()

    author: Mapped["User | None"] = relationship()

    @property
    def changed_by_name(self) -> str | None:
        """Author display name/email, or None for a system actor / removed user.
        Callers that serialize this must `selectinload(CheckVersion.author)`.
        """
        return (self.author.display_name or self.author.email) if self.author else None


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        _in_check("status", RUN_STATUSES, "status_valid"),
        Index("ix_runs_suite_id", "suite_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_asset_id", "asset_id"),
        # Health ranking (#999): mirrors datasource_health's per-suite LATERAL ORDER BY.
        Index(
            "ix_runs_suite_created",
            "suite_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        # Trigger-dedup race guard (#308): one suite run per orchestration event.
        Index(
            "uq_runs_suite_triggered_by",
            "suite_id",
            "triggered_by",
            unique=True,
            postgresql_where=text(
                "triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%' "
                "OR triggered_by LIKE 'dbt:%'"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # CASCADE (#540): runs (and results) die with the suite (ADR 0020 posture).
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    # Stamped at dispatch (ADR 0034): records the asset the run actually ran against, never
    # rewritten by later target changes.
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(256))
    # Captured at dispatch so cancel can revoke a still-queued task.
    celery_task_id: Mapped[str | None] = mapped_column(String(155))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Redaction-safe classified reason for a `failed` run (#605) — never raw adapter text (can carry
    # DSN/credential fragments).
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created_at()


#: The ONE ordering key for a suite's checks and anything listed per check.
CHECK_ORDER = (Check.created_at.nulls_last(), Check.id)


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        _in_check("status", RESULT_STATUSES, "status_valid"),
        Index("ix_results_run_id", "run_id"),
        Index("ix_results_check_id", "check_id"),
        # Retention-sweep partial indexes (#323), one per independently-swept column; each predicate
        # is TEXTUALLY identical to its sweep query's WHERE.
        Index(
            "ix_results_unpurged_created",
            "created_at",
            postgresql_where=text(
                "sample_failures_purged_at IS NULL AND sample_failures IS NOT NULL "
                "AND jsonb_typeof(sample_failures) <> 'null'"
            ),
        ),
        # Covers the `observed_value` sweep (#323 F1) — its predicate shares no term with the index
        # above, so without this it fell back to seq scans per batch.
        Index(
            "ix_results_unpurged_observed",
            "created_at",
            postgresql_where=text("jsonb_typeof(observed_value -> 'observed_value') = 'array'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    # CASCADE (#540): without it a suite that had ever run 500'd on delete.
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checks.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # SQL-aggregatable scalar + per-check runtime (ADR 0012); metric_value is the trend-friendly
    # mirror of the JSONB observed_value, NULL when no scalar.
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # none_as_null on all three (#907): None means "absent" and must be SQL NULL.
    observed_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    expected_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    sample_failures: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    sample_failures_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How much of the dataset the check saw (#595).
    sampling: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = _created_at()


class Share(Base):
    __tablename__ = "shares"
    __table_args__ = (
        _in_check("permission", PERMISSIONS, "permission_valid"),
        UniqueConstraint("suite_id", "user_id", name="uq_shares_suite_user"),
        Index("ix_shares_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    permission: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    # Default-lazy on purpose: the hot authz path (`effective_permission`) must not pay a users
    # join; `list_shares` selectinloads it.
    user: Mapped["User"] = relationship()


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        _in_check("provider", ORCHESTRATION_PROVIDERS, "provider_valid"),
        _in_check("status", PIPELINE_RUN_STATUSES, "status_valid"),
        UniqueConstraint("provider", "provider_run_id", name="uq_pipeline_runs_provider_run"),
        Index("ix_pipeline_runs_provider_pipeline", "provider", "pipeline_or_dag_id"),
        Index("ix_pipeline_runs_connection_id", "connection_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # CASCADE (#753): observation rows are meaningless once their polling connection is gone; the
    # bare FK 500'd on delete (migration a3b4c5d6e7f8).
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    provider_run_id: Mapped[str] = mapped_column(String(256), nullable=False)
    pipeline_or_dag_id: Mapped[str] = mapped_column(String(256), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(2048))
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class TriggerBinding(Base):
    __tablename__ = "trigger_bindings"
    __table_args__ = (
        _in_check("provider", ORCHESTRATION_PROVIDERS, "provider_valid"),
        _in_check("env", ENVS, "env_valid"),
        UniqueConstraint(
            "provider",
            "pipeline_or_dag_id",
            "env",
            "suite_id",
            name="uq_trigger_bindings_lookup",
        ),
        Index(
            "ix_trigger_bindings_provider_pipeline_env",
            "provider",
            "pipeline_or_dag_id",
            "env",
        ),
        Index("ix_trigger_bindings_suite_id", "suite_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    pipeline_or_dag_id: Mapped[str] = mapped_column(String(256), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Schedule(Base):
    """A cron schedule that fires a suite run (A7). The beat dispatcher scans
    `enabled AND next_run_at <= now()` (indexed; cron parsed only on fire).
    NO-BACKFILL: a fire advances `next_run_at` to the next FUTURE occurrence, so a
    downtime gap fires at most once. `cron` is evaluated in `timezone` (DST-aware).
    """

    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_suite_id", "suite_id"),
        # The dispatcher's hot path.
        Index("ix_schedules_enabled_next_run_at", "enabled", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    cron: Mapped[str] = mapped_column(String(128), nullable=False)
    # IANA tz name the cron is evaluated in.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Precomputed next fire (UTC); the dispatcher never parses cron on the scan.
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Provenance, not lifecycle ownership — SET NULL; RESTRICT would make a user un-erasable (GDPR
    #: Art 17, #432/#1319).
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class SuiteNotification(Base):
    """Per-suite alert delivery config (one row per suite): whether (`enabled`), at
    what threshold (`alert_on`), and where — per-channel overrides falling back to
    the workspace config when NULL. Suites with no row use the default policy
    (alert on warn+). Cascade-deleted with the suite.
    """

    __tablename__ = "suite_notifications"
    __table_args__ = (
        _in_check("alert_on", ALERT_ON_POLICIES, "alert_on_valid"),
        UniqueConstraint("suite_id", name="uq_suite_notifications_suite_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Default 'warn' matches the no-config fallback so saving a config doesn't silently change the
    # threshold.
    alert_on: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'warn'"))
    # Per-suite Teams webhook — URL is token-bearing, so only the SecretStore ref is stored (NULL →
    # workspace webhook).
    webhook_secret_ref: Mapped[str | None] = mapped_column(String(256))
    # Per-suite Slack webhook ref, same shape (#633).
    slack_webhook_secret_ref: Mapped[str | None] = mapped_column(String(256))
    # Comma-separated addresses — not a secret, stored inline (NULL → EMAIL_TO, #633).
    email_recipients: Mapped[str | None] = mapped_column(String(1024))
    # Auto-resolve an active incident on first passing result (ADR 0034, #761); no row means the
    # default (on) — `incident_service.auto_resolve_enabled`.
    auto_resolve_incidents: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class LineageEdge(Base):
    """A directed upstream→downstream edge (ADR 0034) — a refreshed CACHE of external truth, keyed
    by provenance (no cross-source/cross-project merge); stale edges are pruned per source.
    """

    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint(
            "upstream_asset_id",
            "downstream_asset_id",
            "source",
            "connection_id",
            name="uq_lineage_edges_up_down_source_conn",
        ),
        # Dedup key for connection-less sources (#762) — see migration 1a2b3c4d5e6f.
        Index(
            "uq_lineage_edges_up_down_source_nullconn",
            "upstream_asset_id",
            "downstream_asset_id",
            "source",
            unique=True,
            postgresql_where=text("connection_id IS NULL"),
        ),
        Index("ix_lineage_edges_upstream", "upstream_asset_id"),
        Index("ix_lineage_edges_downstream", "downstream_asset_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    upstream_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    downstream_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    # No CHECK — sources grow.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Provenance + prune scope (CASCADE); NULL for connection-less sources, which dedupe via the
    # partial unique index above.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE")
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Column-level pairs (#901), union-merged on refresh (incremental sources only re-observe pairs
    # inside their window — never prune).
    columns: Mapped[list[Any] | None] = mapped_column(JSONB(none_as_null=True))


class Incident(Base):
    """A stateful, deduped, evidence-carrying incident (ADR 0034 decision 4, #761), anchored to
    (asset_id, check_id); repeat failures attach as occurrences. Dedup guarantee: at most one
    ACTIVE incident per pair, enforced by the partial unique index the engine's INSERT … ON
    CONFLICT DO NOTHING targets (#420 discipline).
    """

    __tablename__ = "incidents"
    __table_args__ = (
        _in_check("status", INCIDENT_STATUSES, "incident_status_valid"),
        # Single-sourced from INCIDENT_RESOLVED_BY so vocabulary and constraint can't drift.
        CheckConstraint(
            "resolved_by IS NULL OR resolved_by IN ("
            + ", ".join(f"'{v}'" for v in INCIDENT_RESOLVED_BY)
            + ")",
            name="incident_resolved_by_valid",
        ),
        Index("ix_incidents_asset_id", "asset_id"),
        Index("ix_incidents_check_id", "check_id"),
        Index("ix_incidents_suite_id", "suite_id"),
        Index("ix_incidents_status", "status"),
        # The dedup guarantee; the engine's ON CONFLICT index_where mirrors this predicate — keep
        # the two in sync.
        Index(
            "uq_incidents_active_asset_check",
            "asset_id",
            "check_id",
            unique=True,
            postgresql_where=text(
                "status IN (" + ", ".join(f"'{s}'" for s in INCIDENT_ACTIVE_STATUSES) + ")"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checks.id", ondelete="CASCADE"), nullable=False
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    # 'user' | 'auto'; NULL until resolved.
    resolved_by: Mapped[str | None] = mapped_column(String(16))
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Latest failing occurrence; open time = created_at.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledge_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
    # Reopen chain; SET NULL so pruning an old incident never orphans its successor.
    prior_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL")
    )
    # Deterministic evidence card snapshot; never sample_failures content.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class WorkspaceHealth(Base):
    """Workspace-level delivered-alert flags (#1052), one row per signal key — the #843 delivered-
    first discipline for signals with no Connection row.
    """

    __tablename__ = "workspace_health"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated_at()


# ── Audit events (ADR 0041 phase 1, #1318) ───────────────────────────────────── Discriminator on
# `audit_events.action_class`. `config` = a principal changed configuration (phase 1).
AUDIT_ACTION_CLASSES = ("config", "access")

#: Deliberately NO `system` value (ADR 0041 §2.1): machine writes are out of scope, and a `system`
#: actor would invite smuggling them in.
AUDIT_ACTOR_KINDS = ("user", "pat", "webhook")


class AuditEvent(Base):
    """Append-only record of a deliberate act by a principal (ADR 0041) — the record the cascading
    Type-4 snapshot tables structurally cannot keep (the delete). `entity_id` carries NO FK
    deliberately: CASCADE loses the record, RESTRICT blocks deletion.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        _in_check("action_class", AUDIT_ACTION_CLASSES, "action_class_valid"),
        _in_check("actor_kind", AUDIT_ACTOR_KINDS, "actor_kind_valid"),
        # Entity-history read; `entity_type` leads the key.
        Index(
            "ix_audit_events_entity",
            "entity_type",
            "entity_id",
            text("occurred_at DESC"),
        ),
        # Workspace feed; `action_class` leads so a class-scoped sweep never scans the other class
        # (phase-2 volume dwarfs phase 1's).
        Index(
            "ix_audit_events_class_occurred",
            "action_class",
            text("occurred_at DESC"),
        ),
        # "What did this principal do", newest first.
        Index(
            "ix_audit_events_actor",
            "actor_user_id",
            text("occurred_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    occurred_at: Mapped[datetime] = _created_at()
    action_class: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Dotted `entity.verb`.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: NO foreign key — see the class docstring. Nullable for acts with no single row.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: SET NULL: the event must outlive its actor; `actor_label` keeps it legible.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Denormalized identity as at action time.
    actor_label: Mapped[str | None] = mapped_column(String(320))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Correlates with the log line and OTel span carrying the same request id (#525).
    request_id: Mapped[str | None] = mapped_column(String(64))

    actor: Mapped["User | None"] = relationship()

    @property
    def actor_display(self) -> str | None:
        """Live user label if the row survives, else the write-time snapshot."""
        if self.actor is not None:
            return self.actor.display_name or self.actor.email
        return self.actor_label


__all__ = [
    "Asset",
    "AuditEvent",
    "Base",
    "Check",
    "Connection",
    "Incident",
    "LineageEdge",
    "PipelineRun",
    "Result",
    "Run",
    "Schedule",
    "Share",
    "Suite",
    "SuiteNotification",
    "TriggerBinding",
    "User",
    "WorkspaceHealth",
]
