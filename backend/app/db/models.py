import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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
RESULT_STATUSES = ("passed", "failed", "skipped")
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
    secret_ref: Mapped[str | None] = mapped_column(String(256))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


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
    __table_args__ = (Index("ix_checks_suite_id", "suite_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suites.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    expectation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    suite: Mapped["Suite"] = relationship(back_populates="checks")


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


__all__ = [
    "Base",
    "Check",
    "Connection",
    "PipelineRun",
    "Result",
    "Run",
    "Share",
    "Suite",
    "TriggerBinding",
    "User",
]
