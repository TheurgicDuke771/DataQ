import uuid
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

# ── Status / type value sets (TEXT + CHECK; not native PG enums for migration ergonomics) ──
CONNECTION_TYPES = ("snowflake", "adls_gen2", "s3", "unity_catalog", "adf", "airflow")
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
# Result statuses. The four severity tiers (ADR 0005) are health-score-bearing —
# the score aggregate sums their weights over N. The two operational statuses
# (#122) are orthogonal: 'skip' = not evaluated, 'error' = evaluation threw
# (distinct from 'fail', a successful evaluation that breached). Operational
# statuses carry NO penalty weight and MUST be excluded from the health-score N
# (i.e. aggregate WHERE status IN the four tiers only).
_RESULT_SEVERITY_TIERS = ("pass", "warn", "fail", "critical")
_RESULT_OPERATIONAL_STATUSES = ("skip", "error")
RESULT_STATUSES = _RESULT_SEVERITY_TIERS + _RESULT_OPERATIONAL_STATUSES
# Monitor-kind discriminator (ADR 0012; `comparison` reserved by ADR 0014). v1
# only ever writes 'expectation'; the rest are constraint-valid but unused.
CHECK_KINDS = ("expectation", "freshness", "volume", "schema_drift", "anomaly", "comparison")
PIPELINE_RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ORCHESTRATION_PROVIDERS = ("adf", "airflow")
PERMISSIONS = ("view", "edit", "admin")
ENVS = ("dev", "qa", "uat", "prod")


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
        Index("ix_checks_suite_id", "suite_id"),
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
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(256))
    # Celery task id of the dispatched run_suite task, captured at dispatch so a
    # cancel can revoke a still-queued task. NULL until dispatched (or if dispatch
    # failed). String(155): Celery ids are UUIDs but keep headroom.
    celery_task_id: Mapped[str | None] = mapped_column(String(155))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checks.id"), nullable=False
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


__all__ = [
    "Base",
    "Check",
    "Connection",
    "PipelineRun",
    "Result",
    "Run",
    "Schedule",
    "Share",
    "Suite",
    "TriggerBinding",
    "User",
]
