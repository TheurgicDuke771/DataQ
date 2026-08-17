"""FastMCP server — curated, LLM-facing tools over the DataQ service layer.

Mounted into FastAPI at ``/mcp`` (see ``main.py``). Every tool is a thin wrapper:
open a session → resolve the caller (same Azure AD token as the REST API) →
call the *same* service function with the *same* per-suite authz → return an
LLM-shaped dict. No business logic lives here.

All are registered as MCP **tools** (not resources): an LLM client invokes
tools from natural language, whereas resource-templates with required arguments
aren't reliably auto-called — and the acceptance bar is "Claude answers the
canonical NL queries" (ADR 0008). Docstrings are written for natural-language
selection, not REST consumers (CLAUDE.md §10).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.v1._base import contains_nul
from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.roles import (
    DEFAULT_WORKSPACE_ROLE,
    ROLE_RANK,
    is_workspace_admin,
    resolve_role,
)
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import (
    CONNECTION_TYPES,
    ENVS,
    ORCHESTRATION_PROVIDERS,
    Check,
    Connection,
    Run,
    Suite,
    User,
)
from backend.app.db.session import get_session
from backend.app.mcp.auth import (
    McpAuthError,
    build_auth_provider,
    mcp_auth_mode,
    mcp_enabled,
    resolve_current_user,
)
from backend.app.services import (
    check_service,
    connection_service,
    dashboard_service,
    dryrun_service,
    notification_service,
    orchestration_service,
    profile_service,
    rollup,
    run_dispatch,
    run_service,
    run_target,
    schedule_service,
    suite_io_service,
    suite_service,
    trigger_binding_service,
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


def _dec(value: float | None) -> Decimal | None:
    """float → Decimal for the NUMERIC threshold columns, `None` passed through.

    Via `str()`, matching `create_check`: `Decimal(0.05)` binds the binary
    float's full expansion, so the stored threshold would not be the number the
    caller asked for.
    """
    return Decimal(str(value)) if value is not None else None


def _reject_nul(
    *,
    name: str,
    expectation_type: str,
    kind: str,
    config: dict[str, Any],
    dimension: str = "",
) -> None:
    """NUL (\\x00) can't be stored by Postgres (text or JSONB) — reject it here
    like the REST boundary does (`ApiModel`, #567), instead of surfacing the
    driver's ValueError as an opaque tool failure."""
    if contains_nul(
        {
            "name": name,
            "expectation_type": expectation_type,
            "kind": kind,
            "dimension": dimension,
            **config,
        }
    ):
        raise ToolError("NUL (\\x00) characters are not allowed in check fields")


def _require_role(user: User, minimum: str) -> None:
    """Assert the caller holds at least `minimum` workspace role (ADR 0033).

    The MCP-side twin of `core.auth.require_role`. It cannot BE that function:
    `require_role` is a FastAPI dependency factory built on `Depends`, and there
    is no request/dependency graph here. What matters is that both resolve the
    role through the same `core.roles.resolve_role` and rank it with the same
    `ROLE_RANK`, so the two axes cannot disagree about who is an admin — the
    module-level policy split exists precisely so a non-FastAPI caller can reach
    the rule without reimplementing it.

    This is the **coarse** axis. `suite_authz.require_permission` gates a
    *resource*; this gates *a capability the workspace grants at all*, and the
    two compose rather than substitute. Most MCP tools need only the resource
    gate — `require_permission` already caps a Viewer at `view`
    (`_cap_for_viewer`), so every `minimum="edit"` tool refuses a Viewer without
    a role check. This function is for the capabilities that are **not**
    suite-scoped and therefore have no resource ladder to ride: probing a
    connection, and creating a suite from an imported document.

    Raises `ToolError`, never a 404-shaped denial: unlike the suite ladder there
    is no existence to hide — the capability is workspace-wide.
    """
    if minimum not in ROLE_RANK:
        # The guard `core.auth.require_role` performs, for the same reason and one
        # step later. There it is a ValueError at import time (a dependency
        # factory runs once); here the check happens per call, so an unknown
        # `minimum` would otherwise raise a raw KeyError *inside* a tool —
        # escaping `_service_errors`, which maps only `DataQError`, and surfacing
        # to the client as an internal error rather than a denial.
        raise ToolError(  # pragma: no cover — programmer error
            f"unknown workspace role: {minimum!r}"
        )
    role = resolve_role(user)
    if ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise ToolError(
            f"This action requires the '{minimum}' workspace role or higher (you are '{role}')."
        )


#: Keys `suite_io_service.import_suite` indexes DIRECTLY (`c["kind"]`,
#: `c["warn_threshold"]`, …) rather than `.get()`-ing. The REST import route is
#: immune to a missing one only because its `CheckDocument` Pydantic model always
#: emits every key, defaulted — MCP has no such model in front of it, so a
#: hand-composed check that simply omits `warn_threshold` would raise `KeyError`,
#: which is not a `DataQError` and escapes `_service_errors` as an opaque
#: internal failure instead of "your check is missing a field".
_IMPORT_CHECK_KEYS = (
    "name",
    "kind",
    "expectation_type",
    "config",
    "warn_threshold",
    "fail_threshold",
    "critical_threshold",
)


def _validate_import_document(
    *, name: str, description: str | None, checks: list[dict[str, Any]]
) -> None:
    """Boundary validation for `import_suite` — the shape checks the REST route
    gets from Pydantic and MCP would otherwise get from nothing.

    Two failure modes, both of which would surface as internal errors rather than
    actionable ones: a check missing a key the service indexes directly, and a NUL
    anywhere in the document (Postgres cannot store it in text or JSONB, and the
    driver raises `ValueError`).

    The NUL screen covers the CHECKS too, not just the suite name. `create_check`
    screens exactly these fields; import is the second door to the same rows, and
    a guard applied at one door and not its sibling is the shape this track has
    now hit three times.
    """
    if contains_nul({"name": name, "description": description or ""}):
        raise ToolError("NUL (\\x00) characters are not allowed in the suite name or description")
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ToolError(f"checks[{index}] must be an object, got {type(check).__name__}")
        missing = [key for key in _IMPORT_CHECK_KEYS if key not in check]
        if missing:
            raise ToolError(
                f"checks[{index}] is missing required field(s): {', '.join(missing)}. "
                "Pass the check objects from `export_suite` unchanged — thresholds "
                "must be present even when null."
            )
        _reject_nul(
            name=str(check["name"]),
            expectation_type=str(check["expectation_type"]),
            kind=str(check["kind"]),
            config=check["config"] if isinstance(check["config"], dict) else {},
            dimension=str(check.get("dimension") or ""),
        )


@contextmanager
def _service_errors() -> Generator[None]:
    """Turn a service-layer DataQError (404/403/422) into a clean ToolError so the
    LLM gets actionable text instead of an opaque masked exception."""
    try:
        yield
    except DataQError as exc:
        raise ToolError(exc.message) from exc


def _run_outcome_fields(run: Run, outcome: tuple[int, int, str | None] | None) -> dict[str, Any]:
    """A run's data-quality verdict for `list_runs`, or nulls when it has none yet.

    The same #318 rule `_run_results_payload` enforces, applied to the aggregate
    instead of the result rows — because the aggregate overclaims in exactly the
    way the row list does. Results are committed per phase, so a suite 3 checks
    into 30 has a real, all-passing partial set, which `check_outcome_counts`
    faithfully reports as `3 / 3, worst_severity: null`. That is the tool's own
    definition of "nothing failed", asserted about a run that has barely started.

    The REST runs table gets away with the raw numbers because a "running" badge
    sits beside them in a UI. An LLM has no badge — it has whatever fields it is
    handed — so a non-final run reports `results_final: false` and nothing else.
    """
    final = run.status in rollup.AGGREGATABLE_RUN_STATUSES
    if not final or outcome is None:
        return {
            "results_final": final,
            "checks_total": None,
            "checks_passed": None,
            "worst_severity": None,
        }
    total, passed, worst = outcome
    return {
        "results_final": True,
        "checks_total": total,
        "checks_passed": passed,
        "worst_severity": worst,
    }


def _run_results_payload(session: Session, suite: Suite, run: Run) -> dict[str, Any]:
    """One run's `{run, checks}` payload — shared by `get_suite_results` (latest
    run of a suite) and `get_run_results` (a named run).

    Shared rather than duplicated because the two rules encoded here are exactly
    the ones a second copy would get subtly wrong: the #318 finality gate and the
    column-aware sample redaction (#415). Both are safety properties, and a
    divergence between two tools returning "a run's results" would be invisible
    until it leaked.

    Result rows are committed per phase (#318), so a `running` run has a genuine
    partial set and a failed one can carry stragglers. An LLM asked "what failed
    in orders today?" will summarise whatever list it is given as the answer — it
    has no way to know the list is one phase of thirty — so an incomplete run
    yields no checks at all and says why, rather than a confident report built
    from a fraction of the suite.
    """
    final = run.status in rollup.AGGREGATABLE_RUN_STATUSES
    results = run_service.list_results(session, run.id) if final else []
    checks = {c.id: c for c in session.scalars(select(Check).where(Check.suite_id == suite.id))}
    policy = suite.column_policy

    def _tested_column(check_id: uuid.UUID | None) -> str | None:
        check = checks.get(check_id) if check_id is not None else None
        return check.config.get("column") if check is not None else None

    return {
        "run": {
            "id": str(run.id),
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            # Explicit, so a client branches on a field rather than inferring
            # "no checks" means "nothing failed" (#318).
            "results_final": final,
        },
        "checks": [
            {
                "name": checks[r.check_id].name if r.check_id in checks else None,
                "status": r.status,
                "metric_value": _num(r.metric_value),
                # How much of the dataset this check actually saw (#595).
                # `None` = a complete read. Without it an AI client reads a
                # green board from a 2% sample and reports full-dataset
                # quality with total confidence — the #424/#1115 overclaim
                # class, reintroduced for every MCP consumer at once. Carries
                # counts and a strategy name, never cell values, so it needs
                # none of the redaction its neighbours do.
                "sampling": r.sampling,
                "observed_value": run_service.redact_observed_value(
                    r.observed_value, tested_column=_tested_column(r.check_id), policy=policy
                ),
                "expected_value": r.expected_value,
                "sample_failures": run_service.redact_sample_failures(
                    r.sample_failures, tested_column=_tested_column(r.check_id), policy=policy
                ),
            }
            for r in results
        ],
    }


# ─────────────────────────────── read tools ────────────────────────────────


@mcp.tool
def list_suites() -> list[dict[str, Any]]:
    """List the data-quality suites the current user can access.

    Use this to discover what suites exist before drilling into results or
    triggering a run. Returns, per suite: its id, name, the datasource it runs
    against (snowflake / adls / s3 / unity_catalog), the environment (dev / qa /
    uat), how many checks it has, and the status + time of its most recent run
    (null if it has never run). Scoped to suites the user owns or has a share on
    (a workspace-admin sees every suite).
    """
    with _ctx() as (session, user):
        suites = suite_service.list_suites(
            session, user_id=user.id, include_all=is_workspace_admin(user)
        )
        # Three per-suite queries used to run inside this loop — connection,
        # check count, and latest run — so a 30-suite workspace issued 90 round
        # trips for one tool call (#947). An LLM calls this tool constantly and
        # cannot see the cost, so it is the worst place in the app to hide an N+1.
        # All three are now one batched query each.
        suite_ids = [s.id for s in suites]
        connections = {
            c.id: c
            for c in session.scalars(
                select(Connection).where(Connection.id.in_({s.connection_id for s in suites}))
            )
        }
        check_counts: dict[uuid.UUID, int] = dict(
            session.execute(
                select(Check.suite_id, func.count())
                .where(Check.suite_id.in_(suite_ids))
                .group_by(Check.suite_id)
            )
            .tuples()
            .all()
        )
        # The SHARED latest-run statement (#889), not a fourth hand-rolled copy.
        # It also carries the `id` tie-break, so two runs sharing a `created_at`
        # resolve deterministically here as they do everywhere else — the same
        # nondeterminism #928 fixed for the pipeline-run feed.
        last_runs = {
            run.suite_id: run
            for run in session.scalars(rollup.latest_runs_per_suite_stmt(suite_ids))
        }

        out: list[dict[str, Any]] = []
        for s in suites:
            connection = connections.get(s.connection_id)
            check_count = check_counts.get(s.id, 0)
            last_run = last_runs.get(s.id)
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

    If the latest run has not finished successfully, `checks` is empty and
    `run.results_final` is false — the run is still executing, or it failed and
    never produced a complete account. Report the run's status in that case; do
    not describe the suite's quality from it.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="view")
        # The shared statement (#889/#947), not a second spelling of "latest run".
        # Carries the `id` tie-break, so this agrees with `list_suites` and the
        # dashboard about which run is newest when two share a timestamp.
        latest = session.scalars(rollup.latest_runs_per_suite_stmt([sid])).first()
        if latest is None:
            return {"suite_id": suite_id, "run": None, "checks": []}
        return {"suite_id": suite_id, **_run_results_payload(session, suite, latest)}


@mcp.tool
def get_health_score(window_days: int = 7) -> dict[str, Any]:
    """Get the workspace data-quality health score and its trend.

    Use this for 'what's the data health this week?'. Returns the overall health
    score (0-100, severity-weighted), the pass rate, total runs and active
    connections over the trailing ``window_days`` (default 7, max 90), plus a
    per-day trend of the score. Scoped to the suites the user can access
    (a workspace-admin sees the whole workspace).
    """
    if window_days < 1 or window_days > 90:
        raise ToolError("window_days must be between 1 and 90")
    with _ctx() as (session, user):
        summary = dashboard_service.dashboard_summary(
            session,
            user_id=user.id,
            window_days=window_days,
            include_all=is_workspace_admin(user),
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
    pipeline fail?'. Returns the most recent ADF / Airflow / dbt pipeline runs —
    provider, pipeline/DAG id, run status, start/end times — and, when a DQ suite
    was triggered by that pipeline run (and is visible to the user), the triggered
    run's id and status. Optionally filter by ``provider`` ('adf', 'airflow' or
    'dbt').
    """
    # All three orchestration providers (ADR 0029 added dbt), from the shared
    # vocabulary. The old two-name literal rejected `dbt` — the obvious next call
    # after `list_trigger_bindings(provider="dbt")` returns a dbt binding.
    if provider is not None and provider not in ORCHESTRATION_PROVIDERS:
        raise ToolError(f"provider must be one of {list(ORCHESTRATION_PROVIDERS)}")
    with _ctx() as (session, user):
        runs = orchestration_service.list_pipeline_runs(session, provider=provider, limit=limit)
        accessible = set(
            session.scalars(
                suite_service.accessible_suite_ids(user.id, include_all=is_workspace_admin(user))
            )
        )
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


def _check_summary(check: Check) -> dict[str, Any]:
    """The LLM-shaped view of one check — shared by `list_checks`/`get_check` so
    the two can't describe the same row differently.

    `config` is included deliberately: without it an LLM cannot answer "which
    column does this check?" and has to guess from the name, which is a free-text
    label. Per-check size is bounded at author time
    (`check_service._reject_oversized_config` caps each config string) — the
    *response* is bounded by `list_checks`' own `limit`, since that cap says
    nothing about how many checks a suite has.
    """
    return {
        "id": str(check.id),
        "name": check.name,
        "kind": check.kind,
        "expectation_type": check.expectation_type,
        "dimension": check.dimension,
        # Non-NULL exactly for `kind='comparison'` (a table CHECK enforces the
        # equivalence): the baseline connection the suite's dataset is diffed
        # against (ADR 0015). Without it an LLM can describe a comparison check's
        # rule but not what it compares against — half the answer.
        "source_connection_id": (
            str(check.source_connection_id) if check.source_connection_id else None
        ),
        "config": check.config,
        "warn_threshold": _num(check.warn_threshold),
        "fail_threshold": _num(check.fail_threshold),
        "critical_threshold": _num(check.critical_threshold),
        # Operational, not config: an LLM asked "why did nobody get alerted?"
        # needs to see a live snooze (#370). Null = alerts are active.
        #
        # An EXPIRED snooze reads as null, matching the field's advertised meaning
        # ("currently snoozed") and `suppression`'s own rule (`until > now`).
        # `snooze_check` never clears the column, so passing the raw value through
        # would show a month-old timestamp to a client that has no way to know the
        # comparison is against wall-clock now — i.e. it would report suppression
        # that is not in force, on exactly the question ("why no alert?") where a
        # wrong answer is most confidently wrong.
        "alert_snoozed_until": (
            check.alert_snoozed_until.isoformat()
            if check.alert_snoozed_until is not None
            and check.alert_snoozed_until > datetime.now(UTC)
            else None
        ),
    }


@mcp.tool
def list_checks(
    suite_id: str,
    limit: Annotated[int, Field(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    """List the checks (rules and monitors) configured on one suite.

    Use this for 'what does the orders suite actually check?' or before editing a
    check, to find its id. Returns, per check: its id, human name, kind
    (``expectation`` for a Great Expectations rule, or a monitor kind —
    ``freshness`` / ``volume`` / ``schema_drift`` / ``anomaly`` / ``comparison``),
    the expectation type, its DQ dimension (accuracy / completeness / consistency
    / integrity / timeliness / uniqueness / validity, or null when unclassified),
    its configuration, the baseline connection for a comparison check, any
    warn/fail/critical severity thresholds, and whether its alerts are currently
    snoozed. This is the suite's *definition* — for how those checks last
    performed, use ``get_suite_results``.

    At most ``limit`` checks are returned (default 200); ``total`` reports how
    many the suite actually has, so a truncated list is visible rather than
    silently mistaken for the whole suite. Requires view access.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="view")
        checks = check_service.list_checks(session, sid)
        # Truncated in Python, not SQL: `list_checks` is the shared service read
        # and a `limit` argument on it would be a new service behaviour for one
        # caller. The row count here is bounded by what a suite can hold, and the
        # cost this cap actually targets is the CONFIG payload crossing the wire
        # into a context window, not the fetch. `total` keeps the truncation
        # honest — a bare list would let an LLM report "the suite has 200 checks".
        return {
            "suite_id": suite_id,
            "total": len(checks),
            "truncated": len(checks) > limit,
            "checks": [_check_summary(c) for c in checks[:limit]],
        }


@mcp.tool
def get_check(suite_id: str, check_id: str) -> dict[str, Any]:
    """Get one check's full definition by id.

    Use this after ``list_checks`` when you need a single check's exact
    configuration and thresholds — for example before proposing an edit, or to
    explain what a failing check was actually asserting. Returns the same fields
    as ``list_checks`` (including ``source_connection_id`` — the baseline a
    comparison check diffs against) plus when the check was created and last
    modified.
    Requires view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="view")
        check = check_service.get_check(session, sid, cid)
        return {
            **_check_summary(check),
            "suite_id": suite_id,
            "created_at": check.created_at.isoformat(),
            "updated_at": check.updated_at.isoformat(),
        }


@mcp.tool
def get_check_history(
    suite_id: str,
    check_id: str,
    limit: Annotated[int, Field(ge=1, le=200)] = 30,
) -> dict[str, Any]:
    """Get one check's recent result history — how it has behaved run over run.

    Use this for 'has the row-count check been flaky?', 'when did freshness start
    failing?', or 'is this a one-off or a trend?'. Returns up to ``limit`` of the
    check's most recent results in chronological order (oldest first), each with
    the run id, the run's timestamp, the check's status on that run
    (pass / warn / fail / critical / skip / error) and its ``metric_value`` — the
    number the check measured, when it measures one (row count, hours since the
    last row, z-score…), or null for a check that records no metric.

    This is *result* history, not edit history: it says how the check performed,
    not how its definition changed. Requires view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="view")
        points = check_service.list_check_result_history(session, sid, cid, limit=limit)
        return {
            "suite_id": suite_id,
            "check_id": check_id,
            "points": [
                {
                    "run_id": str(p.run_id),
                    "at": p.created_at.isoformat(),
                    "status": p.status,
                    "metric_value": p.metric_value,
                }
                for p in points
            ],
        }


@mcp.tool
def list_runs(
    suite_id: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """List recent suite runs, newest first, with each run's data-quality outcome.

    Use this for 'what has run today?', 'show me the failed runs', or to find a
    run id to drill into with ``get_run_results``. Returns, per run: its id, the
    suite it belongs to, the **execution** status (queued / running / succeeded /
    failed / cancelled), when it started and finished, what triggered it, a
    user-safe failure reason when it failed, and its **data-quality** outcome —
    ``checks_total`` / ``checks_passed`` over the evaluated checks plus
    ``worst_severity`` (warn / fail / critical, or null when nothing failed).

    Those two statuses answer different questions and should not be merged: a run
    is ``succeeded`` when DataQ executed it, even if every check inside it failed.

    A run that has not finished successfully reports ``results_final: false`` and
    a **null** outcome (no counts, no severity) — it is still executing, or it
    failed without producing a complete account. Describe such a run by its
    status; it has no data-quality verdict yet.

    Optionally narrow to one ``suite_id`` (an error if you can't see it) and/or a
    run ``status``. ``total`` reports how many runs match regardless of ``limit``
    / ``offset``, so a short page is not mistaken for the end of the list. Scoped
    to suites the user can access (a workspace-admin sees every suite).
    """
    with _ctx() as (session, user), _service_errors():
        sid = _parse_uuid(suite_id, field="suite_id") if suite_id is not None else None
        # Gate on a named suite up front so an inaccessible or unknown one is a
        # clean "not found" rather than a confident empty list — the same
        # existence-hiding the REST route does, and the #828 rule that an empty
        # answer must never stand in for "you may not ask".
        if sid is not None:
            require_permission(session, sid, user.id, minimum="view")
        # A status outside the closed vocabulary raises rather than returning an
        # empty page that reads as "no runs in that status" (#828).
        run_service.validate_read_filters(status=status)
        include_all = is_workspace_admin(user)
        runs = run_service.list_runs(
            session,
            user_id=user.id,
            suite_id=sid,
            status=status,
            limit=limit,
            offset=offset,
            include_all=include_all,
        )
        # One grouped query for the whole page's outcomes, not one per run — the
        # N+1 an LLM caller cannot see the cost of (#947).
        outcomes = run_service.check_outcome_counts(session, [r.id for r in runs])
        return {
            "total": run_service.count_runs(
                session, user_id=user.id, suite_id=sid, status=status, include_all=include_all
            ),
            "runs": [
                {
                    "id": str(r.id),
                    "suite_id": str(r.suite_id),
                    "status": r.status,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    "triggered_by": r.triggered_by,
                    # A fixed category message from `failure_classifier`, never raw
                    # adapter text (which can carry DSN/credential fragments, #605).
                    "failure_reason": r.failure_reason,
                    **_run_outcome_fields(r, outcomes.get(r.id)),
                }
                for r in runs
            ],
        }


@mcp.tool
def get_run_results(run_id: str) -> dict[str, Any]:
    """Get the per-check results of one specific run, by run id.

    Use this to drill into a run found via ``list_runs`` — 'why did last night's
    orders run fail?' — or to read a historical run rather than the latest one
    (which is what ``get_suite_results`` returns). Returns the run's lifecycle
    status plus, per check: the check name, its pass/warn/fail/critical (or
    skip/error) status, the observed vs expected value, how much of the dataset
    the check saw, and any sample failing rows (PII-redacted).

    If the run has not finished successfully, ``checks`` is empty and
    ``run.results_final`` is false — it is still executing, or it failed and
    never produced a complete account. Report the run's status in that case; do
    not describe the data's quality from it. Requires view access to the run's
    suite.
    """
    rid = _parse_uuid(run_id, field="run_id")
    with _ctx() as (session, user), _service_errors():
        run = run_service.get_run(session, rid)
        if run is None:
            raise ToolError("run not found")
        # Gate on the run's SUITE: a caller who can't see the suite can't see its
        # runs, and the denial hides the run id too (the same rule the REST route
        # holds — ADR 0027 existence-hiding).
        suite = require_permission(session, run.suite_id, user.id, minimum="view")
        return {"suite_id": str(run.suite_id), **_run_results_payload(session, suite, run)}


@mcp.tool
def list_connections(type: str | None = None, env: str | None = None) -> list[dict[str, Any]]:
    """List the configured datasource and orchestration connections, with health.

    Use this for 'what are we connected to?', 'which connections are broken?', or
    to find the connection a suite should run against. Returns, per connection:
    its id, name, type (``snowflake`` / ``adls_gen2`` / ``s3`` / ``unity_catalog``
    / ``iceberg`` for datasources; ``adf`` / ``airflow`` / ``dbt`` for
    orchestration providers), environment, whether a credential is stored, and
    its health — when it was last polled or last ran, a classified error reason
    when it is failing, how many consecutive failures it has had, and when its
    credential expires if the credential states a lifetime. Optionally filter by
    ``type`` or ``env``.

    **Deliberately excludes every connection's configuration.** Names, types and
    health are what a question about connections needs; account identifiers,
    hosts, paths and secret references are not, and this is the surface where
    they would be handed to a model verbatim. Read a connection's config in the
    app if you need it.

    A null health timestamp means *unknown* — nothing has polled or run yet —
    never "healthy", and ``consecutive_run_failures`` is likewise null rather
    than 0 for a connection that has never run. A null ``credential_expires_at``
    means either that this credential type states no readable lifetime **or**
    that its expiry has never been read — ``credential_expiry_checked_at`` tells
    the two apart, and null there means we have never looked. Report all of these
    as silence rather than reassurance.
    """
    if type is not None and type not in CONNECTION_TYPES:
        raise ToolError(f"type must be one of {list(CONNECTION_TYPES)}")
    if env is not None and env not in ENVS:
        raise ToolError(f"env must be one of {list(ENVS)}")
    with _ctx() as (session, _user), _service_errors():
        # Connections are workspace-scoped, not suite-scoped: every authenticated
        # caller can list them, exactly as the REST route does. The role axis
        # (ADR 0033) gates *mutations*, which this tool deliberately has none of —
        # and MCP has no connection-write tool at all, since a credential must
        # never transit an LLM (#529's standing exclusion).
        conns = connection_service.list_connections(session, conn_type=type, env=env)
        # One batched query for the whole list, not one per connection (#947).
        health = connection_service.datasource_health(session, [c.id for c in conns])
        out: list[dict[str, Any]] = []
        for c in conns:
            h = health.get(c.id)
            out.append(
                {
                    "id": str(c.id),
                    "name": c.name,
                    "type": c.type,
                    "env": c.env,
                    "has_secret": c.secret_ref is not None,
                    # Orchestration connections: poll health (#828).
                    "last_polled_at": c.last_polled_at.isoformat() if c.last_polled_at else None,
                    # A CLASSIFIED reason, never raw exception text (which can
                    # carry a SAS/DSN/token) — #605/#1285.
                    "last_poll_error": c.last_poll_error,
                    "consecutive_poll_failures": c.consecutive_poll_failures,
                    # Datasource connections: run-derived health (#954). Absent
                    # from the mapping = no runs yet = unknown, not healthy.
                    "last_run_at": (h.last_run_at.isoformat() if h and h.last_run_at else None),
                    "last_run_error": h.reason if h else None,
                    # `None`, not 0: a connection absent from the health mapping
                    # has never run, and rendering that unknown as a concrete zero
                    # is the reassurance this tool's own docstring forbids.
                    "consecutive_run_failures": h.consecutive_failures if h else None,
                    "credential_expires_at": (
                        c.credential_expires_at.isoformat() if c.credential_expires_at else None
                    ),
                    # Disambiguates the null above (#1024): "this credential type
                    # has no readable lifetime" and "we have never looked" are
                    # different facts, and only one of them is reassuring.
                    "credential_expiry_checked_at": (
                        c.credential_expiry_checked_at.isoformat()
                        if c.credential_expiry_checked_at
                        else None
                    ),
                }
            )
        return out


@mcp.tool
def list_schedules(
    suite_id: str | None = None, enabled: bool | None = None
) -> list[dict[str, Any]]:
    """List the cron schedules that run suites automatically.

    Use this for 'when does the orders suite run?', 'what runs overnight?', or
    'is anything scheduled on this suite?'. Returns, per schedule: its id, the
    suite it runs, the cron expression, the timezone that expression is
    interpreted in, whether it is enabled, when it last ran, and — as
    ``next_run_at`` — exactly when it fires next, or ``null`` when it is disabled
    and therefore will not fire at all. Optionally narrow to one ``suite_id`` or
    to ``enabled`` true/false.

    Use ``next_run_at`` rather than deriving the next fire from the cron
    expression yourself: DataQ computes it in the schedule's own timezone and it
    is therefore already correct across a DST transition, which hand-evaluating
    a cron string against an IANA zone is not.

    A disabled schedule still exists and still reads back here — it simply does
    not fire, so do not describe a suite as unscheduled on the strength of a row
    being present. Scoped to suites the user can access (a workspace-admin sees
    every suite).
    """
    with _ctx() as (session, user), _service_errors():
        sid = _parse_uuid(suite_id, field="suite_id") if suite_id is not None else None
        if sid is not None:
            require_permission(session, sid, user.id, minimum="view")
        schedules = schedule_service.list_schedules(
            session,
            user_id=user.id,
            suite_id=sid,
            enabled=enabled,
            include_all=is_workspace_admin(user),
        )
        return [
            {
                "id": str(s.id),
                "suite_id": str(s.suite_id),
                "cron": s.cron,
                "timezone": s.timezone,
                "enabled": s.enabled,
                "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
                # Precomputed by the scheduler in the schedule's own timezone, so
                # it is already DST-correct — which re-deriving it from `cron` +
                # `timezone` downstream is not.
                #
                # `null` when the schedule is disabled: the column still holds a
                # computed timestamp, but the dispatcher filters on `enabled` and
                # never reaches it, so reporting the raw value would name a fire
                # time that will not happen (found reviewing #1421).
                "next_run_at": (s.next_run_at.isoformat() if s.enabled else None),
            }
            for s in schedules
        ]


@mcp.tool
def list_trigger_bindings(
    provider: str | None = None,
    env: str | None = None,
    suite_id: str | None = None,
) -> list[dict[str, Any]]:
    """List the orchestration triggers that run a suite when a pipeline succeeds.

    Use this for 'what runs after the nightly load?' or 'is this suite wired to
    the ADF pipeline?'. A binding says: when pipeline/DAG ``pipeline_or_dag_id``
    on ``provider`` (adf / airflow / dbt) completes successfully in environment
    ``env``, run this suite. Returns each binding's id, provider, pipeline/DAG
    id, environment, target suite and whether it is enabled. Optionally filter by
    ``provider``, ``env`` or ``suite_id``.

    Only *successful* pipeline completions trigger a suite run; a failure alerts
    but never triggers. Scoped to suites the user can access.
    """
    # The shared vocabulary, not a third hand-written copy of it: dbt joined the
    # set in ADR 0029 and a literal here would silently exclude the next provider.
    if provider is not None and provider not in ORCHESTRATION_PROVIDERS:
        raise ToolError(f"provider must be one of {list(ORCHESTRATION_PROVIDERS)}")
    # `env` gets the same guard as `provider`, for the same reason: a typo'd value
    # would otherwise return `[]`, which reads as "nothing is wired up" (#828).
    if env is not None and env not in ENVS:
        raise ToolError(f"env must be one of {list(ENVS)}")
    with _ctx() as (session, user), _service_errors():
        sid = _parse_uuid(suite_id, field="suite_id") if suite_id is not None else None
        if sid is not None:
            require_permission(session, sid, user.id, minimum="view")
        bindings = trigger_binding_service.list_bindings(
            session,
            user_id=user.id,
            provider=provider,
            env=env,
            suite_id=sid,
            include_all=is_workspace_admin(user),
        )
        return [
            {
                "id": str(b.id),
                "provider": b.provider,
                "pipeline_or_dag_id": b.pipeline_or_dag_id,
                "env": b.env,
                "suite_id": str(b.suite_id),
                "enabled": b.enabled,
            }
            for b in bindings
        ]


@mcp.tool
def get_notification_config(suite_id: str) -> dict[str, Any]:
    """Get a suite's alert notification settings.

    Use this for 'who gets told when orders fails?' or 'are alerts even on for
    this suite?'. Returns whether the suite has its own configuration at all
    (``configured``), whether alerting is enabled, which result severities alert
    (``alert_on``), and — per channel — whether a Teams webhook, a Slack webhook
    and email recipients are in effect, each with whether that comes from the
    suite's **own** override or from the **workspace** default.

    Read the ``*_source`` fields, not just the booleans, when the question is
    "who gets told": a suite with no override of its own still alerts through the
    workspace channels, so per-suite configuration being absent never means
    nobody is notified.

    Webhook **URLs are never returned** — only whether one is set. A webhook URL
    is a bearer credential: anyone holding it can post into that channel, so it
    is stored as a secret reference and this tool reports its presence, not its
    value. Requires view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="view")
        config = notification_service.get_config(session, sid)
        settings = get_settings()

        # Each channel falls back to the workspace-level setting when the suite
        # has no override (#633, `notification_service.resolve_*`). Reporting only
        # the per-suite half — which is all the REST read carries, because the UI
        # sits inside a per-suite settings panel that says so — would answer "who
        # gets told when orders fails?" with "nobody" for the commonest
        # deployment shape: one workspace webhook and no per-suite overrides.
        #
        # Presence is derived from the same inputs `resolve_webhook` /
        # `resolve_slack_webhook` / `resolve_email_recipients` use, and the secret
        # store is never read here: whether a reference is CONFIGURED is the
        # question, and resolving it would fetch the credential itself.
        def _channel(suite_value: Any, workspace_value: Any) -> tuple[bool, str | None]:
            if suite_value:
                return True, "suite"
            if workspace_value:
                return True, "workspace"
            return False, None

        has_webhook, webhook_source = _channel(
            config.webhook_secret_ref if config else None, settings.teams_webhook_secret_name
        )
        has_slack, slack_source = _channel(
            config.slack_webhook_secret_ref if config else None, settings.slack_webhook_secret_name
        )
        # Recipients alone do not mean email is delivered: `EmailPublisher.publish`
        # no-ops unless the workspace SMTP transport (username + password secret +
        # sender) is configured, and that gate applies to a per-suite recipient
        # list exactly as it does to `EMAIL_TO`. Reporting recipients as "email is
        # on" would overclaim on any deployment that names recipients but never
        # wired a mailer.
        smtp_ready = bool(
            settings.email_username and settings.email_password_secret_name and settings.email_from
        )
        has_email, email_source = (
            _channel(config.email_recipients if config else None, settings.email_to)
            if smtp_ready
            else (False, None)
        )
        return {
            "suite_id": suite_id,
            # False = "this suite has no override of its own", NOT "no alerting".
            "configured": config is not None,
            "enabled": config.enabled if config else True,
            "alert_on": config.alert_on if config else notification_service.DEFAULT_ALERT_ON,
            "has_webhook": has_webhook,
            "webhook_source": webhook_source,
            "has_slack_webhook": has_slack,
            "slack_webhook_source": slack_source,
            "has_email_recipients": has_email,
            "email_recipients_source": email_source,
            # The per-suite override only. Workspace recipients are deployment
            # config, not this suite's setting, so they are reported as a source
            # rather than enumerated here.
            "email_recipients": config.email_recipients if config else None,
        }


@mcp.tool
def get_suite_performance() -> list[dict[str, Any]]:
    """Rank the suites by data-quality health, worst first.

    Use this for 'which suites are in the worst shape?' or 'where should I look
    first?'. Returns, per suite: its id, name, a severity-weighted health score
    (0-100, or null when its latest run recorded no scoreable outcome — every
    check skipped or errored), and a state label: ``optimal``, ``stable``,
    ``critical``, or ``unknown`` for a null score. Ordered worst-first, so the
    top of the list is where attention belongs.

    Each suite is scored from its **latest completed run** only — this is a
    current-state ranking, not a trend, and it takes no time window. For health
    over time use ``get_health_score``.

    A suite is **absent** from this list when its latest run produced nothing
    countable — it has never run, is running now, or its last run failed without
    a complete account. Absence is therefore "no health to report", not "healthy";
    use ``list_suites`` to see the full set and which of them are missing here.
    Scoped to the suites the user can access (a workspace-admin sees the whole
    workspace).
    """
    with _ctx() as (session, user), _service_errors():
        # `window_days` is deliberately ABSENT rather than defaulted:
        # `_suite_performance` scores each suite's LATEST run and takes no window,
        # so the argument was inert — identical rankings for 1 day and 90 — while
        # computing and discarding the summary's ~8 windowed aggregates. An LLM
        # given a knob that does nothing will use it and then explain a difference
        # that is not there. The value below only satisfies the shared entry
        # point; nothing in the returned ranking depends on it.
        summary = dashboard_service.dashboard_summary(
            session,
            user_id=user.id,
            window_days=1,
            include_all=is_workspace_admin(user),
        )
        return [
            {
                "suite_id": str(p.suite_id),
                "name": p.name,
                "score": p.score,
                "state": p.state,
            }
            for p in summary.suite_performance
        ]


@mcp.tool
def export_suite(suite_id: str) -> dict[str, Any]:
    """Export a suite as a portable document you can review or re-create elsewhere.

    Use this for 'show me the whole orders suite', 'copy these checks to the QA
    suite', or to diff two suites' definitions against each other. Returns the
    suite's name and description plus every check in stable creation order, each
    with its kind, expectation type, DQ dimension, configuration and severity
    thresholds — the same document the app's export produces, so it can be handed
    back to DataQ's import.

    It carries **definitions only**: no results, no run history, and no
    credentials — a comparison check's baseline connection appears as its
    ``(name, env)`` pair, never as an id or anything resolvable to a secret.
    Requires view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="view")
        doc = suite_io_service.export_suite(session, suite)
        # Thresholds come back as `Decimal` (NUMERIC columns). The REST route has
        # Pydantic to serialize them; MCP hands this dict straight to a JSON
        # encoder, which raises on Decimal — the #1273 class, where a value that
        # crosses a driver boundary reaches the serializer untouched and takes the
        # whole response down. Coerced here rather than in `suite_io_service`,
        # whose document shape is also the import contract.
        return {
            **doc,
            "checks": [
                {
                    **check,
                    "warn_threshold": _num(check.get("warn_threshold")),
                    "fail_threshold": _num(check.get("fail_threshold")),
                    "critical_threshold": _num(check.get("critical_threshold")),
                }
                for check in doc["checks"]
            ],
        }


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
        run = run_dispatch.new_queued_run(suite, triggered_by=f"mcp:{user.id}")
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
    the per-check name + current status, and ``elapsed_ms`` — how long the run has
    been going. Requires view access to the run's suite.

    ``completed_checks`` can legitimately sit at 0 on a healthy, busy run: a suite
    of ordinary expectations is validated as one atomic batch, so its checks all
    resolve at once at the end (#318). Read a rising ``elapsed_ms`` with
    ``status: running`` as "still working", not as "stuck".
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
            "elapsed_ms": progress.elapsed_ms,
            "batched_pending": progress.batched_pending,
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
    source_connection_id: str | None = None,
    dimension: str | None = None,
) -> dict[str, Any]:
    """Add a new check (a Great Expectations expectation) to a suite.

    Use this for 'add a null check on email to the customer suite'. ``name`` is a
    human label; ``expectation_type`` is a GX expectation (e.g.
    ``expect_column_values_to_not_be_null``); ``config`` carries its arguments
    (e.g. ``{"column": "email"}``). Optional warn/fail/critical thresholds band
    the result severity. For a cross-dataset reconciliation check use
    ``kind="comparison"`` with ``expectation_type="comparison:records"``,
    ``source_connection_id`` (the baseline connection to compare against) and a
    config carrying ``source`` (the baseline dataset spec) + ``keys`` (join key
    columns). For a monitor rather than a rule, set ``kind`` and pair it with
    ``expectation_type="monitor:<kind>"``: ``freshness`` (hours since
    ``MAX(column)``), ``volume`` (row count vs ``min_rows``/``max_rows``),
    ``schema_drift`` (columns added/removed/retyped vs a learned baseline), or
    ``anomaly`` — "tell me when this looks unusual compared to normal", which
    learns a rolling mean/stddev of the table's own ``row_count`` or
    ``freshness_age_hours`` and scores each run's z-score; its config takes
    ``target_metric`` (required), plus optional ``column`` (for
    ``freshness_age_hours``), ``window``, ``min_points`` and ``seasonality``
    (day-of-week), and it needs a positive fail/critical threshold, which is the
    z-score sensitivity (3 is a common starting point). ``dimension``
    optionally overrides the DQ dimension (one of
    accuracy, completeness, consistency, integrity, timeliness, uniqueness,
    validity); leave it unset and DataQ derives it from the check type — only set
    it when the user names a dimension explicitly. Requires edit access. Returns
    the created check's id.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    _reject_nul(
        name=name,
        expectation_type=expectation_type,
        kind=kind,
        config=config or {},
        dimension=dimension or "",
    )
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        check = check_service.create_check(
            session,
            suite_id=sid,
            name=name,
            kind=kind,
            expectation_type=expectation_type,
            config=config or {},
            source_connection_id=(
                _parse_uuid(source_connection_id, field="source_connection_id")
                if source_connection_id is not None
                else None
            ),
            warn_threshold=_dec(warn_threshold),
            fail_threshold=_dec(fail_threshold),
            critical_threshold=_dec(critical_threshold),
            dimension=dimension,
            actor_id=user.id,
        )
        return {
            "id": str(check.id),
            "suite_id": suite_id,
            "name": check.name,
            "expectation_type": check.expectation_type,
            "dimension": check.dimension,
        }


@mcp.tool
def update_check(
    suite_id: str,
    check_id: str,
    name: str | None = None,
    expectation_type: str | None = None,
    config: dict[str, Any] | None = None,
    warn_threshold: float | None = None,
    fail_threshold: float | None = None,
    critical_threshold: float | None = None,
    dimension: str | None = None,
) -> dict[str, Any]:
    """Change an existing check's definition — a partial update.

    Use this for 'loosen the null check on email to warn at 2%' or 'rename that
    check'. Omitted **arguments** are left as they were.

    ``config`` is the exception, and it matters: passing it **replaces the whole
    configuration**, it does not merge into it. So to change one setting you must
    send the complete config with that one setting altered — read the check first
    with ``get_check`` and edit what you get back. Sending only the key you want
    to change silently drops every other key: raising ``max_value`` on a
    between-check by sending ``{"max_value": 100}`` deletes its ``min_value``, and
    because the result is still a valid check it saves and reports success.

    Because omission means "leave alone", there is **no way to clear a field back
    to empty** through this tool — a threshold or dimension you want removed
    needs the check recreated. Say so rather than passing 0 or an empty string,
    which would set that value, not clear it. ``kind`` cannot be changed at all;
    recreate the check as the other kind.

    Every update snapshots the new state as a check version, so the change is
    reviewable and reversible in the app. Requires edit access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    _reject_nul(
        name=name or "",
        expectation_type=expectation_type or "",
        kind="",
        config=config or {},
        dimension=dimension or "",
    )
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        check = check_service.update_check(
            session,
            sid,
            cid,
            name=name,
            expectation_type=expectation_type,
            config=config,
            warn_threshold=_dec(warn_threshold),
            fail_threshold=_dec(fail_threshold),
            critical_threshold=_dec(critical_threshold),
            dimension=dimension,
            actor_id=user.id,
        )
        return _check_summary(check)


@mcp.tool
def delete_check(suite_id: str, check_id: str) -> dict[str, Any]:
    """Permanently delete a check from a suite — **and every result it ever
    recorded**.

    Use this for 'remove the row-count check from the orders suite', but read the
    scope first: the delete cascades. The check, its version history, its stored
    monitor baseline **and all of its historical results** go with it, so past
    runs lose that check from their record and any trend built on its
    ``metric_value`` disappears. It is not "stop running this check" — it is
    "erase that this check ever existed".

    This cannot be undone. Confirm with the user before calling it, say plainly
    that result history is included, and prefer ``snooze_check`` when the intent
    is only to stop the alerting. Requires edit access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        # Read the name BEFORE deleting, so the confirmation can say what went.
        name = check_service.get_check(session, sid, cid).name
        check_service.delete_check(session, sid, cid)
        return {"deleted": True, "check_id": check_id, "name": name}


@mcp.tool
def snooze_check(
    suite_id: str,
    check_id: str,
    hours: Annotated[float, Field(gt=0, le=8760)] | None = None,
) -> dict[str, Any]:
    """Mute a check's alerts for a while — or un-mute it now.

    Use this for 'stop alerting on the freshness check until tomorrow' or, with
    ``hours`` omitted, 'turn alerts back on for that check'. Pass ``hours`` to
    snooze for that many hours from now; omit it to clear any snooze immediately.

    A snoozed check **still runs and still fails** — only the alert is
    suppressed. Do not describe a snoozed check as disabled, and do not reach for
    this when the user wants the check to stop evaluating. Requires edit access.

    One tool rather than a snooze/unsnooze pair: it is one piece of state with
    two values, and splitting it would ask an LLM to pick between two names for
    the same field.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        check = (
            check_service.snooze_check(session, sid, cid, hours=hours)
            if hours is not None
            else check_service.clear_check_snooze(session, sid, cid)
        )
        return {
            "check_id": check_id,
            "name": check.name,
            "snoozed": check.alert_snoozed_until is not None,
            "alert_snoozed_until": (
                check.alert_snoozed_until.isoformat() if check.alert_snoozed_until else None
            ),
        }


@mcp.tool
def dryrun_check(
    suite_id: str,
    expectation_type: str,
    config: dict[str, Any] | None = None,
    kind: str = "expectation",
    warn_threshold: float | None = None,
    fail_threshold: float | None = None,
    critical_threshold: float | None = None,
) -> dict[str, Any]:
    """Preview a check against live data WITHOUT saving it.

    Use this before ``create_check`` — 'would a not-null check on email pass
    right now?' — to see what a rule would actually do against the suite's real
    target. Nothing is persisted: no check row, no run, no result. Takes the same
    arguments as ``create_check`` and runs against the suite's configured run
    target.

    Returns the status the check would have recorded (pass / warn / fail /
    critical, or ``error`` when it could not be evaluated and ``skip`` when a
    precondition was not met), the metric it measured, and the observed vs
    expected values.

    This is the authoring loop: dry-run, adjust the threshold, dry-run again, and
    only then create. Requires edit access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    _reject_nul(name="", expectation_type=expectation_type, kind=kind, config=config or {})
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        connection = session.get(Connection, suite.connection_id)
        if connection is None:
            raise ToolError("suite has no connection")
        outcome = dryrun_service.dry_run_check(
            connection,
            kind=kind,
            expectation_type=expectation_type,
            config=config or {},
            warn_threshold=_dec(warn_threshold),
            fail_threshold=_dec(fail_threshold),
            critical_threshold=_dec(critical_threshold),
            target=suite.target,
            secret_store=get_secret_store(),
        )
        return {
            "status": outcome.status,
            "metric_value": _num(outcome.metric_value),
            # Redacted against the suite's column policy — which the REST dry-run
            # route does NOT do (#1419). That is defensible there and not here:
            # the REST consumer is the author's own check-editor panel, looking at
            # a suite they can edit; this consumer is a model that will quote the
            # value into a conversation and may carry it further. Redacting makes
            # the preview agree with `get_suite_results`, which would redact the
            # same column on the same suite — an LLM seeing a value here and a
            # mask there has no way to tell which is the truth.
            "observed_value": run_service.redact_observed_value(
                outcome.observed_value,
                tested_column=(config or {}).get("column"),
                policy=suite.column_policy,
            ),
            "expected_value": outcome.expected_value,
        }


@mcp.tool
def cancel_run(run_id: str) -> dict[str, Any]:
    """Cancel a queued or still-running suite run.

    Use this for 'stop the orders run' or 'cancel that, I triggered the wrong
    suite'. Marks the run cancelled and drops its queued task.

    An already-finished run (succeeded / failed / cancelled) cannot be cancelled
    and reports so rather than pretending. A run already executing is stopped
    **cooperatively** — the worker notices and stops writing results — so a fast
    run can finish before the cancel reaches it; check ``get_run_status``
    afterwards rather than assuming. Requires edit access to the run's suite.
    """
    rid = _parse_uuid(run_id, field="run_id")
    with _ctx() as (session, user), _service_errors():
        run = run_service.get_run(session, rid)
        if run is None:
            raise ToolError("run not found")
        # Gate on the run's suite, like every other run tool — the denial hides
        # the run id from a caller who cannot see its suite.
        require_permission(session, run.suite_id, user.id, minimum="edit")
        if not run_service.cancel_run(session, run):
            raise ToolError(f"run is already finished (status: {run.status})")
        # The other half the REST route does: without this a queued task still
        # runs, and the row says cancelled while the work happens anyway.
        run_dispatch.revoke_run(run.celery_task_id)
        return {"run_id": run_id, "status": run.status}


@mcp.tool
def create_schedule(
    suite_id: str,
    # Bounded to the column (`schedules.cron` is String(128)) like the REST twin.
    # An unbounded LLM-generated string reaches Postgres and raises
    # StringDataRightTruncation, which is a psycopg error, not a `DataQError` —
    # so it escapes `_service_errors` and surfaces as an opaque internal failure
    # instead of an actionable one (#567's class, in a new column).
    cron: Annotated[str, Field(min_length=1, max_length=128)],
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "UTC",
    enabled: bool = True,
) -> dict[str, Any]:
    """Schedule a suite to run automatically on a cron expression.

    Use this for 'run the orders suite every night at 2am'. ``cron`` is a
    standard five-field expression (``0 2 * * *``) and ``timezone`` is an IANA
    name (``America/Toronto``), **not** a UTC offset — the expression is
    interpreted in that zone, so a schedule written this way keeps its local time
    across daylight-saving changes. Both are validated; an invalid one is an error
    rather than a schedule that never fires.

    Returns the schedule's id and ``next_run_at`` — the next time it will
    actually fire — so you can confirm the *interpretation* to the user rather
    than restating the cron back to them. Creating one with ``enabled=false``
    reports ``next_run_at: null``, because a disabled schedule does not fire at
    all; the stored expression is still there and starts firing when it is
    enabled in the app.

    A suite may be scheduled before its run target is configured; the dispatcher
    re-checks at fire time and skips if it is still unset. Requires edit access.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        # `create_schedule` gates internally, but gating here too keeps the
        # authz visible at the tool — and is what the RBAC sweep enters.
        require_permission(session, sid, user.id, minimum="edit")
        schedule = schedule_service.create_schedule(
            session,
            suite_id=sid,
            cron_expr=cron,
            user_id=user.id,
            timezone=timezone,
            enabled=enabled,
        )
        return {
            "id": str(schedule.id),
            "suite_id": suite_id,
            "cron": schedule.cron,
            "timezone": schedule.timezone,
            "enabled": schedule.enabled,
            # `null` when disabled, not the stored timestamp. The column always
            # holds a computed next fire, but a disabled schedule is filtered out
            # by the dispatcher and never reaches it — reporting the raw value
            # would have an assistant confirm a fire time for a paused schedule.
            "next_run_at": (schedule.next_run_at.isoformat() if schedule.enabled else None),
        }


@mcp.tool
def delete_schedule(schedule_id: str) -> dict[str, Any]:
    """Delete a suite's cron schedule so it stops running automatically.

    Use this for 'stop the nightly orders run'. The suite and its checks are
    untouched — only the automatic trigger goes, and the suite can still be run
    on demand.

    If the intent is a pause rather than a removal, say so: a schedule can be
    disabled in the app and re-enabled later, which this tool cannot do.
    Requires edit access to the schedule's suite.
    """
    schid = _parse_uuid(schedule_id, field="schedule_id")
    with _ctx() as (session, user), _service_errors():
        # `delete_schedule` resolves the schedule and gates on ITS suite (404 for
        # a caller who cannot see that suite), so the id alone is not a way in.
        schedule_service.delete_schedule(session, schid, user_id=user.id)
        return {"deleted": True, "schedule_id": schedule_id}


@mcp.tool
def create_trigger_binding(
    provider: str,
    # Same bound as the REST twin: the column is String(256) NOT NULL, and an
    # empty value would create a binding that can never match a pipeline.
    pipeline_or_dag_id: Annotated[str, Field(min_length=1, max_length=256)],
    env: str,
    suite_id: str,
    enabled: bool = True,
) -> dict[str, Any]:
    """Run a suite automatically whenever an orchestrator pipeline succeeds.

    Use this for 'run the orders checks after the nightly load finishes'. Binds
    pipeline/DAG ``pipeline_or_dag_id`` on ``provider`` (adf / airflow / dbt) in
    environment ``env`` to this suite: when that pipeline **completes
    successfully** in that environment, the suite runs.

    Only success triggers a run. A pipeline *failure* alerts but never triggers —
    do not offer this as a way to react to failures.

    ``env`` is part of the key and is the commonest thing to get wrong: a binding
    on ``dev`` never fires for a pipeline whose runs are reported against ``qa``.
    Any returned ``warnings`` are advisory, not errors — read them out, because
    they name exactly that class of silent no-fire. Requires edit access.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    # NUL can't be stored by Postgres and the driver's ValueError would escape
    # `_service_errors` — the same boundary rejection every other free-text tool
    # argument gets (#567).
    if contains_nul({"pipeline_or_dag_id": pipeline_or_dag_id, "provider": provider, "env": env}):
        raise ToolError("NUL (\\x00) characters are not allowed in a trigger binding")
    with _ctx() as (session, user), _service_errors():
        result = trigger_binding_service.create_binding(
            session,
            provider=provider,
            pipeline_or_dag_id=pipeline_or_dag_id,
            env=env,
            suite_id=sid,
            user_id=user.id,
            enabled=enabled,
        )
        return {
            "id": str(result.binding.id),
            "provider": result.binding.provider,
            "pipeline_or_dag_id": result.binding.pipeline_or_dag_id,
            "env": result.binding.env,
            "suite_id": suite_id,
            "enabled": result.binding.enabled,
            # Advisory (#1186), never raised: the ambiguity may be intentional.
            # Dropping them here would recreate the silent-trigger-loss incident
            # they were built to surface.
            "warnings": [
                {"code": w.code, "message": w.message, "other_envs": w.other_envs}
                for w in result.warnings
            ],
        }


@mcp.tool
def suggest_column_policy(suite_id: str) -> dict[str, Any]:
    """Suggest which of a table's columns hold PII, by profiling it live.

    Use this for 'which columns here are sensitive?' or before setting a suite's
    redaction policy. Lists the suite target's columns, profiles them, and
    classifies name **and** sample values into a suggested
    ``{identifier_column, pii_columns}`` policy — which controls whether failing
    sample rows show real values or masks.

    **This only suggests. Nothing is saved**, and the suggestion is a heuristic
    over column names and observed values, not a governance source of truth.
    Present it as a proposal for the user to confirm and apply in the app; do not
    describe a column as safe because it is absent from the list. Requires edit
    access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        connection = session.get(Connection, suite.connection_id)
        if connection is None:
            raise ToolError("suite has no connection")
        # The suite's own run target, resolved exactly as `profile_column` does —
        # so "classify the orders suite" needs no location arguments at all.
        table, schema, catalog, path, file_format = _profile_target_defaults(
            suite, connection, schema=None, catalog=None, file_format=None
        )
        policy = profile_service.suggest_policy_for_target(
            connection,
            table=table,
            schema=schema,
            catalog=catalog,
            path=path,
            file_format=file_format,
            secret_store=get_secret_store(),
        )
        return {
            "suite_id": suite_id,
            "saved": False,
            "identifier_column": policy.get("identifier_column"),
            "pii_columns": policy.get("pii_columns", []),
        }


@mcp.tool
def test_connection(connection_id: str) -> dict[str, Any]:
    """Check whether a stored connection can actually reach its datasource.

    Use this for 'is the Snowflake connection working?' or when a run has failed
    and you need to tell a dead credential from a broken check. Opens a live
    connection using the stored credential and reports success or a classified
    failure reason.

    Nothing is changed and no credential is ever returned — this reports only
    whether the probe worked. Requires the **member** workspace role: it spends a
    stored credential against a remote system, which is not something a read-only
    Viewer does. Fixing a failing connection (re-auth, edit, delete) is
    Admin-only and is deliberately not available here at all — a credential must
    never pass through an AI assistant.
    """
    cid = _parse_uuid(connection_id, field="connection_id")
    with _ctx() as (session, user), _service_errors():
        # The coarse axis: a connection has no suite, so there is no resource
        # ladder to ride and `require_permission` has nothing to gate on. This is
        # what `_require_role` exists for, and it mirrors the REST route's
        # `MemberUser` exactly (ADR 0033's matrix puts `test` at Member+ while
        # every connection *mutation* is Admin-only).
        _require_role(user, DEFAULT_WORKSPACE_ROLE)
        connection = connection_service.get_connection(session, cid)
        connection_service.test_connection(session, cid, secret_store=get_secret_store())
        return {
            "connection_id": connection_id,
            "name": connection.name,
            "type": connection.type,
            "env": connection.env,
            "ok": True,
        }


@mcp.tool
def import_suite(
    connection_id: str,
    # Bounded to the COLUMNS (`suites.name` is String(128), `description` is
    # String(1024)), not to the per-check bounds `validate_lengths` applies —
    # those are a different table. Over-length here reaches Postgres and raises
    # StringDataRightTruncation, which is not a `DataQError` and so escapes
    # `_service_errors` as an opaque internal failure.
    name: Annotated[str, Field(min_length=1, max_length=128)],
    checks: list[dict[str, Any]],
    description: Annotated[str, Field(max_length=1024)] | None = None,
    version: int = 1,
) -> dict[str, Any]:
    """Create a whole suite in one call, from an exported suite document.

    Use this to copy a suite between environments — ``export_suite`` the source,
    then import the document onto a different connection ('recreate the orders
    suite against the QA warehouse'). ``checks`` is the exported document's check
    list verbatim; ``connection_id`` is the datasource the new suite runs against,
    and it may be a different one from the source.

    Creates a **new** suite owned by you — it never merges into or overwrites an
    existing one, so importing twice gives you two suites. The whole document is
    validated before anything is written, so a bad check means nothing is created
    rather than a half-built suite.

    Requires the **member** workspace role (creating a suite is not a read-only
    action) and a datasource connection — orchestration providers cannot be suite
    datasources.
    """
    cid = _parse_uuid(connection_id, field="connection_id")
    _validate_import_document(name=name, description=description, checks=checks)
    with _ctx() as (session, user), _service_errors():
        # Suite creation is Member+ (ADR 0033), and this is the second door into
        # it — the REST `POST /suites` and `POST /suites/import` both carry
        # `MemberUser`. A door that creates the same resource under another name
        # is exactly what #741's review found ungated on `_probe`, so it is gated
        # here at the tool rather than assumed to be covered elsewhere.
        _require_role(user, DEFAULT_WORKSPACE_ROLE)
        suite = suite_io_service.import_suite(
            session,
            version=version,
            name=name,
            description=description,
            checks=checks,
            connection_id=cid,
            created_by=user.id,
        )
        return {
            "id": str(suite.id),
            "name": suite.name,
            "connection_id": connection_id,
            "check_count": len(checks),
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
    namespace: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    # Bounded to match the REST endpoint's own `top_n` (#327 review, P4). It is
    # not just a result-size knob any more: the batched profiler materialises one
    # rank row per `top_n` in the statement itself, so an unbounded value from an
    # LLM-generated argument would compile a multi-megabyte query in the request
    # thread. The bound lives in the tool schema so the client sees it too.
    top_n: Annotated[int, Field(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    """Profile one or more columns of a table or file on a suite's connection.

    Use this for 'profile the revenue column in FACT_ORDERS'. Runs the column
    profiler (no persistence) and returns, per column: null count + fraction,
    distinct count, min/max, and the top ``top_n`` values. ``table`` (+
    optional ``schema``/``catalog``) / ``path`` (+ ``file_format``) default to
    the suite's own run target, so they only need passing to profile something
    *other* than what the suite runs against. ``namespace`` is only meaningful
    for an Iceberg table when passing an explicit ``table`` (Iceberg addresses
    ``namespace.table``); it defaults to the suite target's namespace when no
    explicit ``table``/``path`` is given, so it only needs passing alongside
    your own ``table``. Requires edit access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        connection = session.get(Connection, suite.connection_id)
        if connection is None:
            raise ToolError("suite has no connection")
        if table is None and path is None:
            # `_profile_target_defaults` -> `run_target.resolve_target` already
            # folds the suite target's namespace into `table` for Iceberg — don't
            # fold it a second time here.
            table, schema, catalog, path, file_format = _profile_target_defaults(
                suite, connection, schema=schema, catalog=catalog, file_format=file_format
            )
            namespace = None
        result = profile_service.profile_connection(
            connection,
            columns=columns,
            top_n=top_n,
            table=table,
            schema=schema,
            catalog=catalog,
            namespace=namespace,
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
        log.warning(
            "mcp_disabled_no_auth",
            note="/mcp not mounted — no Azure auth, no email OTP config, no dev bypass",
        )
        return None
    # The mode, not a re-derivation of it: `pat_only` (an OTP deployment) used to
    # be reported as "dev_bypass" by the old ternary, which is both wrong and the
    # most alarming possible thing to say about a production deployment.
    log.info("mcp_enabled", auth=mcp_auth_mode())
    # FastMCP (≥3.4.3) guards the streamable-HTTP transport with a Host allowlist
    # for DNS-rebinding protection, defaulting to loopback only ("127.0.0.1",
    # "localhost", "::1") — anything else gets a 421 Misdirected Request. DataQ
    # always fronts the api with the nginx proxy (ADR 0028 §5), which forwards the
    # *upstream* Host (the internal ACA FQDN in prod, `api` in compose) so ACA's
    # Envoy routes correctly — none of which are loopback, so the guard 421s every
    # proxied MCP request. DNS-rebinding protection is a browser-vs-localhost threat
    # model that doesn't apply here: the api has no public ingress and every /mcp
    # request is PAT- or JWT-authenticated fail-closed (`build_auth_provider` —
    # PAT-only in an OTP deployment, which is *narrower*, never weaker). Allow
    # the proxied hosts so the transport guard doesn't shadow the real auth gate.
    # The same middleware also 403s a request whose browser `Origin` isn't
    # allow-listed. That check is a CSRF defence, and CSRF needs an AMBIENT
    # credential — one the browser attaches by itself. /mcp has none: every call is
    # authenticated by an `Authorization: Bearer` header (Azure JWT or DataQ PAT),
    # which an attacker page cannot obtain or make the browser send. Allow all
    # origins for the same reason we relax the host check, so a browser-based client
    # (e.g. claude.ai) isn't 403'd.
    #
    # ADR 0032 introduced DataQ's first ambient credential — the `dataq_session`
    # cookie — so this premise was re-checked rather than assumed (#734). It holds,
    # and now holds by *construction* rather than by absence: `_PatOrJwtVerifier`
    # rejects a `dq_sess_` bearer by prefix before any validation, and the MCP layer
    # reads the `Authorization` header only — `resolve_current_user` derives the
    # caller solely from the verified token's claims and never touches
    # `request.cookies`. So a browser that holds a DataQ session and is lured to a
    # hostile page sends the cookie to /mcp and is still rejected: there is no
    # cookie-authenticated path here to forge a request against. A test pins it.
    # Driven by Settings, not hardcoded (#728): the allowlist is the one
    # deploy-target coupling that used to live in app code, contradicting the
    # ADR 0010/0013 config guardrail. The default keeps ACA working untouched;
    # MCP_ALLOWED_HOSTS covers EKS/GKE/on-prem/local-first, where a proxy
    # forwarding a different upstream Host otherwise gets an unconfigurable 421.
    return mcp.http_app(
        path="/",
        allowed_hosts=get_settings().mcp_allowed_host_list,
        allowed_origins=["*"],
    )
