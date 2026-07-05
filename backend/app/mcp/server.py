"""FastMCP server — 8 curated, LLM-facing tools over the DataQ service layer.

Mounted into FastAPI at ``/mcp`` (see ``main.py``). Every tool is a thin wrapper:
open a session → resolve the caller (same Azure AD token as the REST API) →
call the *same* service function with the *same* per-suite authz → return an
LLM-shaped dict. No business logic lives here.

All eight are registered as MCP **tools** (not resources): an LLM client invokes
tools from natural language, whereas resource-templates with required arguments
aren't reliably auto-called — and the acceptance bar is "Claude answers the
canonical NL queries" (ADR 0008). Docstrings are written for natural-language
selection, not REST consumers (CLAUDE.md §10).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.v1._base import contains_nul
from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import Check, Connection, Run, User
from backend.app.db.session import get_session
from backend.app.mcp.auth import (
    McpAuthError,
    build_auth_provider,
    mcp_enabled,
    resolve_current_user,
)
from backend.app.services import (
    check_service,
    dashboard_service,
    orchestration_service,
    profile_service,
    run_dispatch,
    run_service,
    run_target,
    suite_service,
)
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)

_INSTRUCTIONS = (
    "DataQ is a data-quality monitoring platform. These tools read and act on DQ "
    "suites (collections of checks), their runs and results, the overall health "
    "score, and orchestration (ADF/Airflow) pipeline status. Use them to answer "
    "questions like 'what failed today?', 'run the orders suite on DEV', 'why did "
    "the customer pipeline fail?', or 'add a null check on email'."
)

mcp: FastMCP = FastMCP(name="DataQ", instructions=_INSTRUCTIONS, auth=build_auth_provider())


# ─────────────────────────── shared plumbing ───────────────────────────────


@contextmanager
def _ctx() -> Generator[tuple[Session, User]]:
    """Open a worker session and resolve the calling user; always close."""
    session = get_session()
    try:
        try:
            user = resolve_current_user(session)
        except McpAuthError as exc:
            raise ToolError(str(exc)) from exc
        yield session, user
    finally:
        session.close()


def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ToolError(f"{field} must be a UUID, got {value!r}") from exc


def _num(value: Decimal | float | None) -> float | None:
    return float(value) if value is not None else None


def _reject_nul(*, name: str, expectation_type: str, kind: str, config: dict[str, Any]) -> None:
    """NUL (\\x00) can't be stored by Postgres (text or JSONB) — reject it here
    like the REST boundary does (`ApiModel`, #567), instead of surfacing the
    driver's ValueError as an opaque tool failure."""
    if contains_nul({"name": name, "expectation_type": expectation_type, "kind": kind, **config}):
        raise ToolError("NUL (\\x00) characters are not allowed in check fields")


@contextmanager
def _service_errors() -> Generator[None]:
    """Turn a service-layer DataQError (404/403/422) into a clean ToolError so the
    LLM gets actionable text instead of an opaque masked exception."""
    try:
        yield
    except DataQError as exc:
        raise ToolError(exc.message) from exc


# ─────────────────────────────── read tools ────────────────────────────────


@mcp.tool
def list_suites() -> list[dict[str, Any]]:
    """List the data-quality suites the current user can access.

    Use this to discover what suites exist before drilling into results or
    triggering a run. Returns, per suite: its id, name, the datasource it runs
    against (snowflake / adls / s3 / unity_catalog), the environment (dev / qa /
    uat), how many checks it has, and the status + time of its most recent run
    (null if it has never run). Scoped to suites the user owns or has a share on.
    """
    with _ctx() as (session, user):
        suites = suite_service.list_suites(session, user_id=user.id)
        out: list[dict[str, Any]] = []
        for s in suites:
            connection = session.get(Connection, s.connection_id)
            check_count = session.scalar(
                select(func.count()).select_from(Check).where(Check.suite_id == s.id)
            )
            last_run = session.scalars(
                select(Run).where(Run.suite_id == s.id).order_by(Run.created_at.desc()).limit(1)
            ).first()
            out.append(
                {
                    "id": str(s.id),
                    "name": s.name,
                    "datasource": connection.type if connection else None,
                    "env": connection.env if connection else None,
                    "check_count": int(check_count or 0),
                    "last_run": (
                        {
                            "status": last_run.status,
                            "at": (last_run.finished_at or last_run.created_at).isoformat(),
                        }
                        if last_run
                        else None
                    ),
                }
            )
        return out


@mcp.tool
def get_suite_results(suite_id: str) -> dict[str, Any]:
    """Get the latest data-quality run results for one suite.

    Use this to answer 'what failed in <suite> today?'. Returns the most recent
    run's lifecycle status plus, per check: the check name, its pass/warn/fail/
    critical (or skip/error) status, the observed vs expected value, and any
    sample failing rows (PII-redacted). Returns an empty result set if the suite
    has never run. Requires at least view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="view")
        latest = session.scalars(
            select(Run).where(Run.suite_id == sid).order_by(Run.created_at.desc()).limit(1)
        ).first()
        if latest is None:
            return {"suite_id": suite_id, "run": None, "checks": []}
        results = run_service.list_results(session, latest.id)
        checks = {c.id: c for c in session.scalars(select(Check).where(Check.suite_id == sid))}
        policy = suite.column_policy
        return {
            "suite_id": suite_id,
            "run": {
                "id": str(latest.id),
                "status": latest.status,
                "started_at": latest.started_at.isoformat() if latest.started_at else None,
                "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
            },
            "checks": [
                {
                    "name": checks[r.check_id].name if r.check_id in checks else None,
                    "status": r.status,
                    "metric_value": _num(r.metric_value),
                    "observed_value": r.observed_value,
                    "expected_value": r.expected_value,
                    "sample_failures": run_service.redact_sample_failures(
                        r.sample_failures,
                        tested_column=(
                            checks[r.check_id].config.get("column")
                            if r.check_id in checks
                            else None
                        ),
                        policy=policy,
                    ),
                }
                for r in results
            ],
        }


@mcp.tool
def get_health_score(window_days: int = 7) -> dict[str, Any]:
    """Get the workspace data-quality health score and its trend.

    Use this for 'what's the data health this week?'. Returns the overall health
    score (0-100, severity-weighted), the pass rate, total runs and active
    connections over the trailing ``window_days`` (default 7, max 90), plus a
    per-day trend of the score. Scoped to the suites the user can access.
    """
    if window_days < 1 or window_days > 90:
        raise ToolError("window_days must be between 1 and 90")
    with _ctx() as (session, user):
        summary = dashboard_service.dashboard_summary(
            session, user_id=user.id, window_days=window_days
        )
        return {
            "window_days": summary.window_days,
            "health_score": summary.kpis.health_score,
            "pass_rate": summary.kpis.pass_rate,
            "total_runs": summary.kpis.total_runs,
            "active_connections": summary.kpis.active_connections,
            "trend": [
                {"day": p.day.isoformat(), "succeeded": p.succeeded, "failed": p.failed}
                for p in summary.trend
            ],
        }


@mcp.tool
def get_adf_pipeline_status(provider: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Get recent orchestration pipeline/DAG runs with their correlated DQ result.

    Use this for 'did any pipelines fail overnight?' or 'why did the customer
    pipeline fail?'. Returns the most recent ADF / Airflow pipeline runs —
    provider, pipeline/DAG id, run status, start/end times — and, when a DQ suite
    was triggered by that pipeline run (and is visible to the user), the triggered
    run's id and status. Optionally filter by ``provider`` ('adf' or 'airflow').
    """
    if provider is not None and provider not in ("adf", "airflow"):
        raise ToolError("provider must be 'adf' or 'airflow'")
    with _ctx() as (session, user):
        runs = orchestration_service.list_pipeline_runs(session, provider=provider, limit=limit)
        accessible = set(session.scalars(suite_service.accessible_suite_ids(user.id)))
        out: list[dict[str, Any]] = []
        for pr in runs:
            marker = f"{pr.provider}:{pr.pipeline_or_dag_id}:{pr.provider_run_id}"
            dq = session.scalars(
                select(Run).where(Run.triggered_by == marker).order_by(Run.created_at.desc())
            ).first()
            correlated = (
                {"run_id": str(dq.id), "status": dq.status}
                if dq is not None and dq.suite_id in accessible
                else None
            )
            out.append(
                {
                    "provider": pr.provider,
                    "pipeline": pr.pipeline_or_dag_id,
                    "status": pr.status,
                    "started_at": pr.started_at.isoformat() if pr.started_at else None,
                    "finished_at": pr.finished_at.isoformat() if pr.finished_at else None,
                    "dq_run": correlated,
                }
            )
        return out


# ─────────────────────────────── action tools ──────────────────────────────


@mcp.tool
def trigger_suite_run(suite_id: str) -> dict[str, Any]:
    """Trigger an asynchronous run of a suite's checks; returns a run id to poll.

    Use this for 'run the orders suite on DEV'. Queues the suite and dispatches it
    to the worker, returning the new run's id and queued status — poll
    ``get_run_status`` with that id for progress. Requires edit access. Fails
    fast if the suite has no valid run target configured.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        connection = session.get(Connection, suite.connection_id)
        if connection is None:
            raise ToolError("suite has no connection")
        # Raises SuiteTargetInvalidError (→ ToolError) for a targetless/wrong target.
        run_target.resolve_target(connection.type, suite.target)
        run = Run(suite_id=suite.id, status="queued", triggered_by=f"mcp:{user.id}")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = str(run.id)
        if not run_dispatch.dispatch_or_fail(session, run):
            raise ToolError("failed to dispatch run — the task broker is unreachable")
        # Report the queued state at dispatch, not a post-commit reload of
        # `run.status` (expire_on_commit) which a fast worker may already have
        # flipped — poll `get_run_status` for live progress.
        return {"run_id": run_id, "status": "queued"}


@mcp.tool
def get_run_status(run_id: str) -> dict[str, Any]:
    """Poll the live, check-by-check progress of a suite run.

    Use this after ``trigger_suite_run`` ('is the orders run finished yet?').
    Returns the run's lifecycle status (queued / running / succeeded / failed /
    cancelled), how many of its checks have completed, a count per result status,
    and the per-check name + current status. Requires view access to the run's
    suite.
    """
    rid = _parse_uuid(run_id, field="run_id")
    with _ctx() as (session, user), _service_errors():
        run = run_service.get_run(session, rid)
        if run is None:
            raise ToolError("run not found")
        require_permission(session, run.suite_id, user.id, minimum="view")
        progress = run_service.get_run_progress(session, run)
        return {
            "run_id": str(progress.run.id),
            "status": progress.run.status,
            "total_checks": progress.total_checks,
            "completed_checks": progress.completed_checks,
            "counts": progress.counts,
            "checks": [{"name": c.name, "status": c.status} for c in progress.checks],
        }


@mcp.tool
def create_check(
    suite_id: str,
    name: str,
    expectation_type: str,
    config: dict[str, Any] | None = None,
    kind: str = "expectation",
    warn_threshold: float | None = None,
    fail_threshold: float | None = None,
    critical_threshold: float | None = None,
) -> dict[str, Any]:
    """Add a new check (a Great Expectations expectation) to a suite.

    Use this for 'add a null check on email to the customer suite'. ``name`` is a
    human label; ``expectation_type`` is a GX expectation (e.g.
    ``expect_column_values_to_not_be_null``); ``config`` carries its arguments
    (e.g. ``{"column": "email"}``). Optional warn/fail/critical thresholds band
    the result severity. Requires edit access. Returns the created check's id.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    _reject_nul(name=name, expectation_type=expectation_type, kind=kind, config=config or {})
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        check = check_service.create_check(
            session,
            suite_id=sid,
            name=name,
            kind=kind,
            expectation_type=expectation_type,
            config=config or {},
            warn_threshold=Decimal(str(warn_threshold)) if warn_threshold is not None else None,
            fail_threshold=Decimal(str(fail_threshold)) if fail_threshold is not None else None,
            critical_threshold=(
                Decimal(str(critical_threshold)) if critical_threshold is not None else None
            ),
            actor_id=user.id,
        )
        return {
            "id": str(check.id),
            "suite_id": suite_id,
            "name": check.name,
            "expectation_type": check.expectation_type,
        }


def _profile_target_defaults(
    suite: Any,
    connection: Connection,
    *,
    schema: str | None,
    catalog: str | None,
    file_format: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Default an unspecified profile location to the suite's run target (#583).

    Same resolver the run path uses, so 'profile the AMOUNT column on the orders
    suite' just works. Explicitly passed ``schema``/``catalog``/``file_format``
    still win over the target's values. Returns the (table, schema, catalog,
    path, file_format) quintuple for `profile_service.profile_connection`.
    """
    if not suite.target:
        raise ToolError(
            "no 'table' or 'path' was given and the suite has no run target — pass "
            "'table' (+ optional 'schema'/'catalog') for a SQL datasource or 'path' "
            "for a flat file, or set the suite's run target first"
        )
    resolved = run_target.resolve_target(connection.type, suite.target)
    if resolved.batch is not None:
        # A flat-file batch target: list the store and resolve the concrete file,
        # exactly like a run does.
        from backend.app.datasources import flatfile

        try:
            concrete = run_target.materialize_path(
                connection.type,
                dict(connection.config),
                resolved,
                secret_ref=connection.secret_ref,
                secret_store=get_secret_store(),
            )
        except flatfile.BatchNotFoundError as exc:
            raise ToolError(
                f"the suite's batch target matched no file in the store yet: {exc}"
            ) from exc
    else:
        concrete = resolved.table
    if connection.type in ("adls_gen2", "s3"):
        return None, schema, catalog, concrete, file_format or suite.target.get("file_format")
    return concrete, schema or resolved.schema, catalog or resolved.catalog, None, file_format


@mcp.tool
def profile_column(
    suite_id: str,
    columns: list[str],
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Profile one or more columns of a table or file on a suite's connection.

    Use this for 'profile the revenue column in FACT_ORDERS'. Runs the column
    profiler (no persistence) and returns, per column: null count + fraction,
    distinct count, min/max, and the top ``top_n`` values. ``table`` (+
    optional ``schema``/``catalog``) / ``path`` (+ ``file_format``) default to
    the suite's own run target, so they only need passing to profile something
    *other* than what the suite runs against. Requires edit access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        connection = session.get(Connection, suite.connection_id)
        if connection is None:
            raise ToolError("suite has no connection")
        if table is None and path is None:
            table, schema, catalog, path, file_format = _profile_target_defaults(
                suite, connection, schema=schema, catalog=catalog, file_format=file_format
            )
        result = profile_service.profile_connection(
            connection,
            columns=columns,
            top_n=top_n,
            table=table,
            schema=schema,
            catalog=catalog,
            path=path,
            file_format=file_format,
            secret_store=get_secret_store(),
        )
        return {
            "row_count": result.row_count,
            "table": result.table,
            "path": result.path,
            "columns": [
                {
                    "column": c.column,
                    "null_count": c.null_count,
                    "null_fraction": c.null_fraction,
                    "distinct_count": c.distinct_count,
                    "min_value": c.min_value,
                    "max_value": c.max_value,
                    "top_values": c.top_values,
                }
                for c in result.columns
            ],
        }


def build_mcp_app() -> Any:
    """Build the MCP ASGI app to mount at ``/mcp`` (``path='/'`` since we mount).

    Returns ``None`` when MCP must not be exposed (no resolvable auth — see
    ``auth.mcp_enabled``), so ``main.py`` skips the mount and the endpoint never
    goes live unauthenticated.
    """
    if not mcp_enabled():
        log.warning("mcp_disabled_no_auth", note="/mcp not mounted — no Azure auth or dev bypass")
        return None
    log.info(
        "mcp_enabled", auth="azure_ad" if get_settings().azure_auth_configured else "dev_bypass"
    )
    return mcp.http_app(path="/")
