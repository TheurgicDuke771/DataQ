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
# Result statuses. The four severity tiers (ADR 0005) are health-score-bearing —
# the score aggregate sums their weights over N. The two operational statuses
# (#122) are orthogonal: 'skip' = not evaluated, 'error' = evaluation threw
# (distinct from 'fail', a successful evaluation that breached). Operational
# statuses carry NO penalty weight and MUST be excluded from the health-score N
# (i.e. aggregate WHERE status IN the four tiers only).
_RESULT_SEVERITY_TIERS = ("pass", "warn", "fail", "critical")
RESULT_OPERATIONAL_STATUSES = ("skip", "error")
RESULT_STATUSES = _RESULT_SEVERITY_TIERS + RESULT_OPERATIONAL_STATUSES
# Failing severity tiers (the non-`pass` tiers) → rank, worst last. The single
# source for the discrete "which run outcome is worse" ordering shared by alert
# dedup, the RunReport builder, and run-outcome rollups (#655) — derived from the
# tier vocabulary above so it can't drift. Deliberately distinct from the
# health-penalty *weights* in dashboard_service (ADR 0005), which weight `pass`
# too and are a separate concept.
SEVERITY_RANK: dict[str, int] = {
    tier: rank for rank, tier in enumerate((t for t in _RESULT_SEVERITY_TIERS if t != "pass"), 1)
}
# The failing-tier set (keys of SEVERITY_RANK, worst last): the tiers that count as
# "not clean" for alerting. Lives here with the rest of the severity vocabulary so
# the set and the rank order have one source; the alerting layer imports it.
FAILING_TIERS: tuple[str, ...] = tuple(SEVERITY_RANK)


def worst_severity(statuses: Iterable[str]) -> str | None:
    """The highest failing tier present in ``statuses`` (``critical`` > ``fail`` >
    ``warn``), or ``None`` when none breached — `pass`/`skip`/`error` never rank.

    The single place the shared severity order is applied to pick a run's worst
    outcome (#655), used by the RunReport builder and the run-outcome rollups so
    they don't each re-implement the max-by-rank loop.
    """
    present = [s for s in statuses if s in FAILING_TIERS]
    return max(present, key=lambda s: SEVERITY_RANK[s]) if present else None


# Monitor-kind discriminator (ADR 0012; `comparison` reserved by ADR 0014,
# modeled by ADR 0015). v1 wrote only 'expectation'; freshness/volume shipped
# post-v1, `comparison` authors as of ADR 0015 (runner in #794); the rest are
# constraint-valid but unused.
CHECK_KINDS = ("expectation", "freshness", "volume", "schema_drift", "anomaly", "comparison")
COMPARISON_KIND = "comparison"
PIPELINE_RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ORCHESTRATION_PROVIDERS = ("adf", "airflow", "dbt")
PERMISSIONS = ("view", "edit", "admin")
ENVS = ("dev", "qa", "uat", "prod")
# Per-suite alert delivery threshold (suite_notifications.alert_on). 'fail' =
# fail/critical only, 'warn' = warn+, 'always' = every terminal run.
ALERT_ON_POLICIES = ("fail", "warn", "always")

# Incident lifecycle (ADR 0034 decision 4, #761). `open → acknowledged → resolved`;
# a resolved row is never mutated back to open (reopen = a NEW incident linked via
# `prior_incident_id`). The two non-resolved states are the "active" set the dedup
# guarantee keys on: at most one active incident per (asset_id, check_id).
INCIDENT_STATUSES = ("open", "acknowledged", "resolved")
INCIDENT_ACTIVE_STATUSES = ("open", "acknowledged")
# Who resolved an incident — a user (manual ack/resolve) or the engine (first
# passing result for the pair, per-suite configurable). NULL until resolved.
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
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aad_object_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class ApiKey(Base):
    """A DataQ-issued personal access token (PAT) — ADR 0026 phase 1 (#461).

    The credential is a high-entropy random token shown once at creation; only
    its SHA-256 hex digest is stored (a verifier secret — never retrievable, so
    deliberately NOT in the SecretStore). The key authenticates as its owning
    user through the same `get_current_user` seam as Azure AD, inheriting the
    owner's per-suite grants — no separate authz model. `ondelete=CASCADE` ties
    the lifecycle to the owner: deleting the user kills their keys.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # First characters of the token (e.g. `dq_live_ab12`) — safe to list/log.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # SHA-256 hex of the full token; unique doubles as the O(1) auth lookup index.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Asset(Base):
    """A first-class data asset — the browse/reason grain (ADR 0034, gap G-d).

    Today "the table" exists only implicitly inside `Suite.target` JSONB. This
    table promotes it to a shared primitive that lineage edges, incidents, and a
    future catalog sync can all reference. Suites remain the execution/authz grain
    (ADR 0027 untouched); assets are what users reason about. Two axes, like dbt
    models-vs-jobs.

    **Identity = the OpenLineage dataset naming spec** — `(namespace, name)` unique
    together, adopted verbatim (including its quote-strip / engine-case / Snowflake
    account normalization rules) so our identifiers match `openlineage-dbt` / Spark
    emissions byte-for-byte, making future emission/pull interop a join, not a
    mapping layer. Consequence accepted: DEV/QA accounts are *distinct* assets —
    cross-env grouping is a UI concern over `env`, never an identity merge.

    `connection_id` is a **provenance hint, not identity** (SET NULL on connection
    delete — the asset outlives the connection that first surfaced it).
    `owner_user_id` is the later incident-routing hop (§4). `first_seen`/`last_seen`
    bound the accrete-not-delete cleanup posture.
    """

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("namespace", "name", name="uq_assets_namespace_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    namespace: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # From the resolving connection (the OL spec keys namespace on tenant/physical
    # isolation, so env is metadata, not identity — see class docstring).
    env: Mapped[str | None] = mapped_column(String(16))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="SET NULL")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Free-text asset description (ADR 0034 §4, #760). Set only via the
    # workspace-Admin-only `PATCH /assets/{id}` — the cheap, safe row on the 0033
    # matrix; widened to composing-suite `edit` later if it chafes. Nullable.
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
        # Orchestration providers (ADF, Airflow) are singletons per env: at most
        # one connection per (provider, env), the binding `trigger_bindings`
        # assumes (ADR 0004, #72). Datasources are deliberately excluded — e.g.
        # Snowflake DEV can have many connections (different databases) — so this
        # is a *partial* unique index over the orchestration types only.
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
    # Nullable by design (PR #41 review): holds the SecretStore key (`conn-<id>`)
    # once a credential is written, but is NULL for (a) the transient window
    # between row flush and secret write in create_connection, and (b)
    # credential-less auth — managed identity (ADLS) / IAM role (S3), deferred to
    # Week 7 (ADR 0010/0011) — plus any unauthenticated source. v1 connection types
    # are all secret-bearing and enforce presence in the service layer
    # (test_connection → 502 without a stored credential), so the column stays
    # NULL-able for the W7 credential-less modes without a later migration.
    secret_ref: Mapped[str | None] = mapped_column(String(256))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    # ── Poll health (#828) ────────────────────────────────────────────────────
    # An orchestration poll that fails every 10 minutes used to be visible ONLY in the
    # logs: the connection still read as configured, the lineage UI showed its normal
    # empty state, and the beat task reported success with an `errors` count nobody saw.
    # Prod lineage rotted for six days on an expired SAS and the product cheerfully said
    # "nothing to see here". These three columns are what make that state *a fact about
    # the connection* rather than a line in App Insights.
    #
    # NULL on every non-orchestration connection (and on any orchestration connection
    # never yet polled) — "unknown", which the UI must not render as healthy.
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A **classified**, redaction-safe reason — never raw exception text, which can
    # carry a SAS/token/DSN (the #536 traceback-locals leak is the precedent). NULL
    # means the last poll succeeded.
    last_poll_error: Mapped[str | None] = mapped_column(String(512))
    # Consecutive failures; reset to 0 on any success. The counter (not a bare boolean)
    # is what lets the UI say "failing for ~6 days" instead of "failing", and what a
    # future alert threshold rides on.
    consecutive_poll_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # ── Warehouse-native lineage refresh state (#858) ────────────────────────────
    # Per-connection state for the warehouse-lineage beat (snowflake / unity_catalog).
    # All NULL on a connection never refreshed, and on every non-warehouse type.
    #
    # The incremental high-water mark for a LOG source (UC `table_lineage.event_time`).
    # NULL for a snapshot source (Snowflake `OBJECT_DEPENDENCIES` — re-read whole).
    lineage_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the last refresh ran, and which tier answered — so the UI can qualify the
    # graph ("view-level only", "current as of ~2h ago") instead of a bare empty state
    # (#828). `lineage_last_tier` is a `LineageTier` value; `lineage_degraded_reason`
    # is the human note when a richer tier was unavailable (edition-gated, missing grant).
    lineage_last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lineage_last_tier: Mapped[str | None] = mapped_column(String(64))
    lineage_degraded_reason: Mapped[str | None] = mapped_column(String(512))
    # A **classified**, redaction-safe reason the last refresh could not run (mirrors
    # `last_poll_error` — never raw exception text). NULL means the last refresh ran.
    lineage_last_error: Mapped[str | None] = mapped_column(String(512))


class ConnectionVersion(Base):
    """An immutable snapshot of a connection's editable, **non-secret** state,
    written on create and after every successful name/config update — the source
    for the connection "version history" view. Mirrors `check_versions`: per-entity
    config history, not the cross-entity audit log (deferred — see #310).

    Deliberately omits the credential: the secret lives only in the SecretStore
    (referenced by `secret_ref`, which is the constant `conn-<id>` pointer, not the
    value), so it is never copied here. A credential rotation (`reauth`, or an
    update that only changes the secret) therefore records **no** version — that is
    an audit-log concern, not config history.

    `version_no` is a per-connection sequence starting at 1 (unique with
    `connection_id`). A version is cascade-deleted with its connection (history is
    not retained past deletion — accepted), but survives its author (`changed_by`
    is `SET NULL`).
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
    # Snapshot of the editable fields. type/env are immutable on a connection but
    # snapshotted for a self-contained record; `config` is the non-secret
    # datasource config as stored. No credential / secret_ref (see class docstring).
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Who authored this version. NULL for a system/unknown actor or once the user
    # is removed — the snapshot must outlive its author (SET NULL, not CASCADE).
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()

    author: Mapped["User | None"] = relationship()

    @property
    def changed_by_name(self) -> str | None:
        """The author's display name (or email) for the history view, or None for
        a system actor / removed user. Reads the eager-loaded `author` — callers
        that serialize this must `selectinload(ConnectionVersion.author)`."""
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
    # Datasource-shaped run target (#215): the table / flat-file path / Unity
    # Catalog 3-level name the suite's checks run against. Shaped like the column
    # profiler request (`table`/`schema`/`catalog`/`path`/`file_format`) and
    # resolved per connection type to the runner's (table, schema, catalog) by
    # `services.run_target.resolve_target`. NULL = targetless = not yet runnable.
    target: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The asset this suite's target resolves to (ADR 0034, gap G-d). Resolved from
    # `target` + the connection config on save via OpenLineage identity naming.
    # Nullable because resolution is fail-soft: a targetless or unresolvable suite
    # keeps this NULL rather than blocking the save. `connection_id` provenance
    # aside, this is the browse/reason link; SET NULL so an asset sweep never
    # deletes a suite.
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL")
    )
    # Column-redaction policy for failing-row samples (#415): which columns may be
    # shown vs masked when surfacing `Result.sample_failures`. Shape:
    # `{"identifier_column": str, "pii_columns": [str]}` — the identifier is always
    # shown (so a failing row is locatable; must be non-PII) and `pii_columns` are
    # always masked; unclassified columns still default-redact (security can't
    # regress). NULL = no policy → the blanket-mask fallback. Suite-level for v1
    # (a suite targets one table); shaped to promote to a connection/column catalog
    # later. Auto-derivable from datasource classification/tags + name heuristics;
    # this column stores the resolved/overridden policy.
    column_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
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
        # ADR 0015: a comparison check carries its source (baseline) ref; every
        # other kind must not. Presence ⇔ kind, DB-enforced so the run path can
        # trust a comparison row always has a source.
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
    # Monitor-kind discriminator (ADR 0012). v1 = 'expectation' only; the run path
    # dispatches on this, and v1.x auto-monitors slot in as new kinds.
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'expectation'")
    )
    expectation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # Comparison source ref (ADR 0015): the baseline connection this check diffs
    # the suite's dataset (the target under test) against. Non-NULL exactly for
    # kind='comparison' (table CHECK above). RESTRICT: deleting a referenced
    # connection is blocked — the service pre-checks and 409s with the dependent
    # checks rather than letting the FK error surface raw.
    source_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="RESTRICT")
    )
    # Optional severity thresholds (ADR 0005). NULL → the check is plain pass/fail.
    warn_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    fail_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    critical_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Alert snooze (suppression): mute this check's alerts until this moment (UTC).
    # NULL or in the past = active. Operational state set via the snooze endpoint —
    # NOT an editable config field, so it's excluded from the check PATCH and from
    # `check_versions` snapshots (config history shouldn't churn on a snooze).
    alert_snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    suite: Mapped["Suite"] = relationship(back_populates="checks")


class MonitorBaseline(Base):
    """The persisted reference state a *stateful* monitor kind diffs against
    (#592, ADR 0012) — one CURRENT baseline per check.

    ``schema_drift`` stores the column-name/type snapshot it compares the live
    schema to; the W5 ``anomaly`` kind (#593) reuses this exact shape for its
    metric-baseline parameters — one persistence shape, two consumers, which is
    why the payload is a kind-shaped JSONB rather than schema-drift columns.

    Semantics: UNIQUE per check (the current baseline — re-baseline REPLACES the
    row, it doesn't append; history lives in `results`, not here). Cascade with
    the check. ``captured_by`` records a manual re-baseline actor; NULL means the
    run path captured it automatically (first run of the check). The baseline is
    metadata about the target's *shape*, never row data — no PII concerns, no
    retention sweep involvement.
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
    # Denormalized from the check for queryability/debugging (which kinds hold
    # baselines); the check's kind is the authority.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Kind-shaped payload. schema_drift: {"columns": [{"name": ..., "type": ...}, ...]}.
    baseline: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    captured_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CheckVersion(Base):
    """An immutable snapshot of a check's editable state, written on create and
    after every successful update — the source for the "version history" drawer
    ("see previous config before overwriting"). v1 is view-only; restore is a
    deferred follow-up. This is per-check config history, not the cross-entity
    audit log (deferred to v1.1).

    `version_no` is a per-check sequence starting at 1 (unique with `check_id`).
    Rows survive the check (`ondelete=SET NULL` on `changed_by` for a deleted
    author) but are cascade-deleted with the check itself.
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
    # Snapshot of the editable check fields (kind is immutable but snapshotted for
    # a self-contained record). `config` is the GX expectation kwargs, as stored.
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    expectation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # Comparison source ref as a plain UUID — deliberately NO FK (ADR 0015/0020):
    # a snapshot must outlive a later repoint + delete of the old source
    # connection, so history never blocks a connection delete.
    source_connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warn_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    fail_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    critical_threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    # Who authored this version. NULL for a system/unknown actor or once the user
    # is removed — the snapshot must outlive its author (SET NULL, not CASCADE).
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()

    author: Mapped["User | None"] = relationship()

    @property
    def changed_by_name(self) -> str | None:
        """The author's display name (or email) for the history drawer, or None
        for a system actor / removed user. Reads the eager-loaded `author` —
        callers that serialize this must `selectinload(CheckVersion.author)`."""
        return (self.author.display_name or self.author.email) if self.author else None


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        _in_check("status", RUN_STATUSES, "status_valid"),
        Index("ix_runs_suite_id", "suite_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_asset_id", "asset_id"),
        # Trigger-dedup race guard (#308): one suite run per orchestration
        # pipeline-run event. Partial — orchestration markers only
        # (`<provider>:<pipeline>:<run_id>`); manual/probe/schedule markers
        # legitimately repeat. Predicate mirrors the migration + the service's
        # ON CONFLICT (orchestration_service._ORCH_TRIGGER_PREDICATE).
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
    # CASCADE (#540): runs (and their results, via the run_id FK) die with the
    # suite — ADR 0020's accepted cascade posture. Without it a suite that had
    # ever run 500'd on delete.
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    # The asset resolved from the suite's target, **stamped at dispatch** (ADR
    # 0034): run history records the asset a run actually ran against, so it never
    # rewrites when a suite's target later changes. Nullable — fail-soft, mirrors
    # `Suite.asset_id`, and SET NULL so an asset sweep never deletes a run.
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(256))
    # Celery task id of the dispatched run_suite task, captured at dispatch so a
    # cancel can revoke a still-queued task. NULL until dispatched (or if dispatch
    # failed). String(155): Celery ids are UUIDs but keep headroom.
    celery_task_id: Mapped[str | None] = mapped_column(String(155))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A redaction-safe, user-facing reason for a `failed` run (#605) — a fixed
    # category message from `failure_classifier`, never raw adapter text (which can
    # carry DSN/credential fragments). NULL for non-failed runs and for older rows.
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created_at()


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        _in_check("status", RESULT_STATUSES, "status_valid"),
        Index("ix_results_run_id", "run_id"),
        Index("ix_results_check_id", "check_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    # CASCADE (#540): was the schema's only FK without an ondelete — a suite
    # delete cascaded checks while runs→results rows still referenced them, so
    # any suite that had ever run 500'd on delete (ADR 0020 accepts cascade).
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checks.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # SQL-aggregatable scalar the check measured + per-check runtime (ADR 0012).
    # metric_value is the trend/anomaly-friendly mirror of the JSONB observed_value;
    # NULL where a check yields no meaningful scalar.
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    observed_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expected_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sample_failures: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sample_failures_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    # Read-only convenience for enriching a share with the grantee's directory
    # identity (email / display_name) so the sharing UI can name collaborators.
    # ORM-only — no schema change. Default-lazy: the hot authz path
    # (`effective_permission`) selects shares without ever reading `.user`, so
    # the eager load is scoped to `list_shares` (selectinload) instead of taxing
    # every permission check with a `users` join.
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
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id"), nullable=False
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
    """A cron schedule that fires a suite run automatically (A7).

    The beat dispatcher (`worker.tasks.dispatch_due_schedules`) ticks every
    minute and queries `enabled AND next_run_at <= now()` — an indexed scan, so
    the cron is parsed only when a schedule actually fires, never per tick.

    `next_run_at` is precomputed (`services.cron.next_fire`) on create and after
    each fire / cron change. **No-backfill semantics**: a fire advances it to the
    next *future* occurrence, so a downtime gap fires at most once on recovery
    rather than backfilling every missed slot (correct for monitoring). `cron` is
    a standard 5-field expression evaluated in `timezone` (IANA, DST-aware,
    default UTC). Cascade-deleted with the suite.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_suite_id", "suite_id"),
        # The dispatcher's hot path: due + enabled schedules, oldest-due first.
        Index("ix_schedules_enabled_next_run_at", "enabled", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    cron: Mapped[str] = mapped_column(String(128), nullable=False)
    # IANA tz name the cron is evaluated in (e.g. 'America/New_York'); 'UTC' default.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Precomputed next fire (UTC). The dispatcher scans on this; never parses cron.
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Last time the dispatcher fired this schedule (NULL until first fire).
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class SuiteNotification(Base):
    """Per-suite alert delivery config (one row per suite).

    Decides *whether* a suite's run outcomes are delivered (``enabled``), at what
    threshold (``alert_on``), and *where* — per-channel overrides that each fall
    back to the workspace-level config when NULL:

    * ``webhook_secret_ref`` — the per-suite **Teams** webhook (URL is
      token-bearing, so only the SecretStore ref is stored, never the DB);
    * ``slack_webhook_secret_ref`` — the per-suite **Slack** webhook, same shape (#633);
    * ``email_recipients`` — the per-suite **email** recipients (comma-separated
      addresses; not a secret, so stored inline), NULL → workspace ``EMAIL_TO`` (#633).

    Suites with no row use the default policy (alert on warn+). The Teams / Slack /
    email publishers read this when delivering (``alerting.*``). Cascade-deleted
    with the suite.
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
    # Delivery threshold (ADR 0005 tiers): 'fail' = fail/critical only, 'warn' =
    # warn+, 'always' = every terminal run. Default 'warn' matches the no-config
    # fallback (`notification_service.DEFAULT_ALERT_ON`) so saving a config
    # doesn't silently change the threshold. See `alerting.routing.route_for`.
    alert_on: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'warn'"))
    # SecretStore key for the per-suite Teams webhook URL (NULL → workspace webhook).
    # The URL is a secret; only the ref is stored here.
    webhook_secret_ref: Mapped[str | None] = mapped_column(String(256))
    # SecretStore key for the per-suite Slack webhook URL (NULL → workspace Slack
    # webhook). Same token-bearing shape as the Teams ref (#633).
    slack_webhook_secret_ref: Mapped[str | None] = mapped_column(String(256))
    # Per-suite email recipients — comma-separated addresses (NULL → workspace
    # EMAIL_TO). Not a secret (addresses, not credentials), so stored inline (#633).
    email_recipients: Mapped[str | None] = mapped_column(String(1024))
    # Auto-resolve an active incident on the first passing result for its
    # (asset, check) pair (ADR 0034 decision 4, #761). On by default; a suite opts
    # out to keep incidents open until a human resolves them. A suite with no
    # notification row uses the default (auto-resolve on) —
    # `incident_service.auto_resolve_enabled`.
    auto_resolve_incidents: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class LineageEdge(Base):
    """A directed upstream→downstream lineage edge between two assets (ADR 0034).

    A refreshed **cache of external truth**, not a graph DataQ authors: each edge
    is (re)discovered from a lineage `source` — ``'dbt'`` first (the parsed
    `manifest.json` dependency graph, #759), ``'marquez'`` for catalog pull (#762) —
    and keyed by provenance so the same edge from two sources — or two dbt projects
    sharing tables — is distinct rows (no cross-source or cross-project merge).
    ``last_seen`` bumps on every refresh that still observes the edge; a stale edge
    (not re-seen in the latest refresh of its source) is pruned. Blast radius = walk
    these edges downstream from a failing asset (`lineage.edges.downstream_assets`).

    **Two provenance regimes, two dedup keys:**

    - **Connection-scoped sources** (dbt) always carry a ``connection_id`` and key on
      the full unique constraint ``(upstream, downstream, source, connection_id)`` —
      so pruning one project's refresh never touches another's edges (the review's
      cross-project-corruption fix).
    - **Connection-less sources** (a catalog pull — a Marquez query has no DataQ
      connection, #762) carry ``connection_id = NULL`` and key on the **partial**
      unique index ``(upstream, downstream, source) WHERE connection_id IS NULL``
      (Postgres treats NULLs as distinct in a plain unique constraint, so the full
      constraint would never dedupe a NULL-connection row). Their prune is scoped to
      ``(source, connection_id IS NULL)`` — it can never touch a dbt row.

    Both endpoints CASCADE-delete: an edge is meaningless without either asset.
    ``connection_id`` CASCADE-deletes and is **nullable** (nullable since #762 for the
    connection-less pull sources above). ``source`` is un-CHECKed on purpose — lineage
    sources will grow (catalog pull, OpenLineage receipt) and each new one must not
    need a migration.
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
        # Dedup key for connection-less sources (catalog pull, #762): a plain unique
        # constraint treats each NULL connection_id as distinct, so pulled edges need a
        # partial unique index on the (up, down, source) triple where connection_id is
        # NULL — see migration 1a2b3c4d5e6f.
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
    # Lineage source that surfaced this edge (e.g. 'dbt'). No CHECK — sources grow.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # The connection whose refresh discovered this edge — provenance + prune scope
    # (CASCADE: an edge is meaningless once its refreshing connection is gone).
    # NULL for connection-less sources (a catalog pull — Marquez, #762 — has no DataQ
    # connection); those dedupe via the partial unique index above.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE")
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Column-level refinement of this edge (#901): a JSONB list of
    # ``[upstream_column, downstream_column]`` pairs from sources that offer the
    # grain (UC ``system.access.column_lineage``). NULL = no pairs ever observed
    # (the write path only records observed pairs — never ``[]``). Merged (union)
    # on refresh for incremental sources — a log window only re-observes pairs
    # whose queries ran inside it, and forgetting the rest would be a prune the
    # never-prune regime forbids.
    columns: Mapped[list[Any] | None] = mapped_column(JSONB)


class Incident(Base):
    """A stateful, deduped, evidence-carrying incident (ADR 0034 decision 4, #761).

    An **alert** is a per-result notification (fire-and-forget — severity routing,
    dedup, snooze; already shipped). An **incident** is the durable object those
    signals roll up into, anchored to ``(asset_id, check_id)``: a failing result
    opens one, repeat failures attach as *occurrences* (``occurrence_count`` +
    ``last_seen_at``) rather than piling up new rows, and the first passing result
    for the pair auto-resolves it (per-suite configurable). Alerts keep firing per
    their own rules and reference the open incident.

    **Dedup guarantee — at most one *active* incident per ``(asset_id, check_id)``.**
    "Active" = ``status IN ('open', 'acknowledged')``; enforced by the partial
    unique index ``uq_incidents_active_asset_check``, which the lifecycle engine's
    ``INSERT … ON CONFLICT DO NOTHING`` targets so a concurrent second failing
    result attaches an occurrence instead of racing in a duplicate (the #420
    upsert-race discipline, one level up from alert dedup).

    **Lifecycle** ``open → acknowledged → resolved`` with actor + timestamp per
    transition (open = ``created_at``; ack = ``acknowledged_at``/``acknowledged_by``;
    resolve = ``resolved_at``/``resolved_by``/``resolved_by_user_id``). A resolved
    row is **never** mutated back to open — a resolved pair's next failure opens a
    NEW incident linked to the prior one via ``prior_incident_id`` (the reopen
    chain). ``suite_id`` is denormalized from the check's suite so visibility can
    derive from suite grants (ADR 0027, same rule as the asset view #760) and
    routing can reach the suite owner without a join.

    ``evidence`` is the Theme-2 deterministic evidence card (layer 1, no LLM),
    snapshotted at open and refreshed per occurrence — assembled from existing data
    only and **never** carrying ``sample_failures`` content (PII rule).

    Both ``asset_id`` and ``check_id`` CASCADE-delete (an incident is meaningless
    without its anchor — the same posture as ``results``); ``suite_id`` CASCADEs
    too. Actor FKs (``acknowledged_by``/``resolved_by_user_id``) SET NULL so an
    incident outlives the user who acted on it; ``prior_incident_id`` SET NULLs so
    pruning an old resolved incident never deletes its successor.
    """

    __tablename__ = "incidents"
    __table_args__ = (
        _in_check("status", INCIDENT_STATUSES, "incident_status_valid"),
        # Single-sourced from INCIDENT_RESOLVED_BY so the vocabulary and the
        # constraint can't drift (and CodeQL sees the constant used).
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
        # At most one ACTIVE (open|acknowledged) incident per (asset, check) — the
        # dedup guarantee. Partial unique index; the engine's ON CONFLICT DO NOTHING
        # targets it (index_where mirrors this predicate — keep the two in sync).
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
    # Who resolved it ('user' | 'auto'); NULL until resolved.
    resolved_by: Mapped[str | None] = mapped_column(String(16))
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Latest failing occurrence (bumps on every attach); open time = created_at.
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
    # Reopen chain: the prior (resolved) incident this one succeeds, or NULL for a
    # first-ever incident on the pair. SET NULL so pruning an old one never orphans.
    prior_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL")
    )
    # Deterministic evidence card (layer 1) snapshot; never sample_failures content.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


__all__ = [
    "Asset",
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
]
