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
    INCIDENT_STATUSES,
    ORCHESTRATION_PROVIDERS,
    PIPELINE_RUN_STATUSES,
    Asset,
    Check,
    Connection,
    Run,
    Suite,
    User,
)
from backend.app.db.session import get_session
from backend.app.mcp import docs_catalog
from backend.app.mcp.auth import (
    McpAuthError,
    build_auth_provider,
    mcp_auth_mode,
    mcp_enabled,
    resolve_current_user,
)
from backend.app.services import (
    asset_view_service,
    audit_service,
    channel_service,
    check_service,
    connection_service,
    dashboard_service,
    dryrun_service,
    incident_service,
    live_probe,
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
#: immune to a missing one only because its `CheckDocumentIn` Pydantic model always
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


#: `detail` keys the service layer uses for "here are the values that WOULD be accepted"
#: (`validate_kind`, `validate_engine`, `validate_dimension`, `validate_expectation_check`).
_RECOVERY_DETAIL_KEYS = ("supported", "known")

#: Cap on the appended list — the expectation allowlist is the long one, and a ToolError is text
#: an LLM re-reads on every retry.
_RECOVERY_VALUES_MAX = 60


def _tool_error_text(exc: DataQError) -> str:
    """The message, plus the accepted values when the error carries them.

    REST hands `detail` to the caller in the error envelope; this context manager kept only
    `message`, so an LLM refused by any of the four validators above was told what was wrong and
    never what would work — and then guessed again. The values are the recovery path, so they
    belong in the one channel MCP actually delivers.
    """
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    for key in _RECOVERY_DETAIL_KEYS:
        values = detail.get(key)
        if isinstance(values, list) and values:
            shown = [str(v) for v in values[:_RECOVERY_VALUES_MAX]]
            more = "" if len(values) <= _RECOVERY_VALUES_MAX else f" (+{len(values) - len(shown)})"
            return f"{exc.message}. Accepted values: {', '.join(shown)}{more}"
    return exc.message


@contextmanager
def _service_errors() -> Generator[None]:
    """Turn a service-layer DataQError (404/403/422) into a clean ToolError so the
    LLM gets actionable text instead of an opaque masked exception."""
    try:
        yield
    except DataQError as exc:
        raise ToolError(_tool_error_text(exc)) from exc


def _page_window(timestamps: list[datetime | None]) -> dict[str, Any]:
    """The time interval a count-capped page actually covers.

    Several tools cap by COUNT and are asked questions bounded by TIME ("what
    failed today?", "did anything fail overnight?"). The REST caller wrote the
    query and knows what they asked for; the UI user sees the timestamps in the
    table. An LLM has neither, so a page of the 20 newest rows reads as "the
    period you asked about" — which is #1442, and the same shape recurs on every
    count-capped list here.

    Returning the interval as DATA lets a model check whether the window it was
    asked about is inside the page, instead of inferring it from row timestamps
    it may well summarise away.
    """
    present = [t for t in timestamps if t is not None]
    return {
        "newest_in_page": max(present).isoformat() if present else None,
        "oldest_in_page": min(present).isoformat() if present else None,
    }


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


def _run_results_payload(
    session: Session, suite: Suite, run: Run, *, actor: User | None = None
) -> dict[str, Any]:
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

    **The G1 access event is written here, not at the two call sites** (#431). The
    reason is the same one this function is shared for: a second copy of the audit
    call is a second place to forget it, and the surface it protects — who read
    which failing rows — is one where a silent gap is indistinguishable from
    nobody having looked.
    """
    final = run.status in rollup.AGGREGATABLE_RUN_STATUSES
    results = run_service.list_results(session, run.id) if final else []
    checks = {c.id: c for c in session.scalars(select(Check).where(Check.suite_id == suite.id))}
    policy = suite.column_policy
    # The warehouse's own column classifications (G3, #433) — the governance floor
    # a suite policy cannot lift. Read from the cached map on the asset, never
    # from the warehouse: an MCP read must not open a datasource connection.
    # `None` means no opinion, which is what shipped before G3.
    tags = _asset_column_tags(session, suite, run)
    # Per-RESULT (tested_column, expectation_type) as of when each result was
    # written (#1489) — not the check's current state, which is freely editable
    # after the fact and would silently re-label what old results show/audit.
    context = run_service.historical_check_context(session, results, checks)

    def _tested_column(result_id: uuid.UUID) -> str | None:
        return context.get(result_id, (None, None))[0]

    # Redact ONCE, here, and reuse below. An earlier version redacted every result
    # a second time purely to compute `exposed_ids` and then threw the result
    # away, so every MCP results read paid the redaction pass twice.
    rendered = [
        {
            "result_id": str(r.id),
            **_redacted_sample(r, _tested_column(r.id), policy, tags),
            "observed_value": run_service.redact_observed_value(
                r.observed_value,
                tested_column=_tested_column(r.id),
                policy=policy,
                tags=tags,
            ),
        }
        for r in results
    ]
    # `redaction` is the state the redactor computed for THIS read: `full` means
    # everything row-level was masked and `null` means there was no row-level
    # content at all, so neither exposed anything. `observed_value` is the OTHER
    # door to raw cells and needs its own test rather than a null check — a fully
    # masked list and a plain row count are both non-None and neither is an
    # exposure (see `run_service.observed_value_exposes_cells`). `expectation_type`
    # (#1486/#1489) is what lets that test tell a max/min's literal cell apart
    # from an aggregate statistic that also happens to have a tested column —
    # resolved historically via `context`, not the live `checks` row, for the
    # same reason `tested_column` is above.
    #
    # Derived from the redacted output rather than the stored row, so the policy
    # that decides what the caller sees is the same one that decides what the
    # audit records — the two cannot disagree about whether an exposure happened.
    exposed_ids = [
        r["result_id"]
        for result, r in zip(results, rendered, strict=True)
        if r["redaction"] in {"none", "partial"}
        or run_service.observed_value_exposes_cells(
            r["observed_value"], expectation_type=context.get(result.id, (None, None))[1]
        )
    ]
    rendered_by_id = {r["result_id"]: r for r in rendered}
    audit_service.record_access(
        session,
        action="run_results.read",
        entity_type="run",
        entity_id=run.id,
        actor=actor,
        exposed=bool(exposed_ids),
        detail={
            "suite_id": str(suite.id),
            "result_count": len(results),
            "exposed_result_ids": exposed_ids,
            # The surface matters to an investigator: an LLM client may carry a
            # value into a conversation and onward in ways a browser session does
            # not, so "who read this" is not the whole question — "through what"
            # is part of it.
            "surface": "mcp",
        },
    )

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
                # The id, so a model that finds the failing check here can reach
                # `get_check` / `get_check_history` / `snooze_check` directly
                # instead of name-matching back through `list_checks`.
                "check_id": str(r.check_id) if r.check_id else None,
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
                "expected_value": r.expected_value,
                # Redaction reused from `rendered` above, not recomputed. Beyond
                # the wasted second pass, computing it twice would let the payload
                # and the access event disagree about what was masked — and the
                # event's whole value is that it reports what the caller actually
                # saw.
                **{k: v for k, v in rendered_by_id[str(r.id)].items() if k != "result_id"},
            }
            for r in results
        ],
    }


def _asset_column_tags(
    session: Session, suite: Suite, run: Run | None = None
) -> dict[str, str] | None:
    """Thin alias for `run_service.asset_column_tags` — see its docstring.

    This used to be a deliberate copy, on the grounds that it was three lines over
    the ORM and the precedence that must not diverge lived in `run_service`. That
    held while there was no shared home for it; #1419/#1479 gave the live probes
    one, and a third spelling of the governance floor is exactly the
    guard-at-one-door shape those issues are made of.
    """
    return run_service.asset_column_tags(session, suite, run)


def _redacted_sample(
    result: Any,
    tested_column: str | None,
    policy: dict[str, Any] | None,
    tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The failing-row sample plus **how much of it was masked** (#424/#1115).

    The REST route has shipped `redaction` / `redacted_columns` since #1115 and
    MCP called the stateless redactor, so an AI client received a masked sample
    with no way to tell masking had happened. Both readings are wrong and
    confident: mask tokens reported as the data, or a fully-masked sample
    reported as "no failing rows were captured".

    `redaction` is `full` / `partial` / `none`, or `null` when the sample carried
    no row-level content at all — which is the one case where there is nothing
    true to claim either way.
    """
    sample, state, redacted_columns = run_service.redact_sample_failures_with_state(
        result.sample_failures, tested_column=tested_column, policy=policy, tags=tags
    )
    return {
        "sample_failures": sample,
        "redaction": state,
        "redacted_columns": redacted_columns,
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

    ``last_run.status`` is the **execution** status — a run is ``succeeded`` when
    DataQ managed to execute it, even if every check inside it failed. Never
    describe a suite as healthy from this field; use ``get_suite_results`` for
    the data-quality outcome.
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

    Use this for 'what failed in <suite> on its last run?'.

    **This returns the suite's most recent run, whenever that was** — there is no
    date filter and no way to ask for a particular day. Check the returned
    ``run.started_at`` before answering anything phrased about "today" or "last
    night"; use ``list_runs`` + ``get_run_results`` to reach an earlier run.

    Returns the most recent run's lifecycle status plus, per check: the check
    name and id, its pass/warn/fail/critical (or skip/error) status, the observed
    vs expected value (**redacted on the same column-aware policy as the
    samples** — a masked observed value is not the measured one), how much of the
    dataset the check actually saw (``sampling`` — null means a complete read; a
    non-null record means the verdict came from a sample), any sample failing
    rows, and ``redaction`` / ``redacted_columns`` saying how much of those rows
    was masked. A masked sample is not an absent one — never describe redacted
    rows as "no failing rows". Returns an empty result set if the suite has never
    run. Requires at least view access to the suite.

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
        return {"suite_id": suite_id, **_run_results_payload(session, suite, latest, actor=user)}


@mcp.tool
def get_health_score(window_days: int = 7) -> dict[str, Any]:
    """Get the workspace data-quality health score and its trend.

    Use this for 'what's the data health this week?'. Returns the overall health
    score (0-100, severity-weighted), the pass rate, total runs and active
    connections over the trailing ``window_days`` (default 7, max 90), plus
    ``trend``.

    Read the fields exactly as they are defined, because three of them are drawn
    from different populations:

    - ``trend`` is a per-day count of **runs** that succeeded and failed —
      run *lifecycle* status, not data quality, and **not a per-day score**
      (no daily score is computed). Days with no runs are zero-filled, and runs
      in any other state (running, cancelled) appear in neither count, so the
      two need not sum to that day's runs.
    - ``health_score`` and ``pass_rate`` are **null**, not 0, when nothing was
      evaluated in the window — no completed runs, or every check skipped or
      errored. Null is "no data", never "bad". Both come only from runs in a
      final state, while ``total_runs`` counts every run created in the window,
      so a large ``total_runs`` beside a null score means the runs did not
      complete.
    - ``active_connections`` is **not windowed and not a health signal**: it is
      how many distinct connections the accessible suites reference. A
      connection counts here even if it has never run or its credential is dead
      — use ``list_connections`` for connection state.

    Scoped to the suites the user can access (a workspace-admin sees the whole
    workspace).
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
def get_pipeline_status(
    provider: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """Get recent orchestration pipeline/DAG runs with their correlated DQ result.

    Use this for 'why did the customer pipeline fail?'. Returns the most recent
    ADF / Airflow / dbt pipeline runs —
    provider, pipeline/DAG id, run status, start/end times — and, when a DQ suite
    was triggered by that pipeline run (and is visible to the user), the triggered
    run's id and status. Optionally filter by ``provider`` ('adf', 'airflow' or
    'dbt') and/or ``status``.

    **There is no time filter**, so "did anything fail overnight?" cannot be
    asked directly: this returns the ``limit`` most recent runs, and
    ``oldest_in_page`` says how far back you actually saw. If ``oldest_in_page``
    is later than the window you were asked about, you have not seen the whole
    window — raise ``limit`` or page with ``offset``, and say so rather than
    answering "nothing failed".

    Only pipelines DataQ has ingested appear here. A short or empty result can
    also mean no orchestration connection is configured for that provider and
    environment, or that its poller is failing — check ``list_connections``
    before reporting an all-clear.
    """
    # All three orchestration providers (ADR 0029 added dbt), from the shared
    # vocabulary. The old two-name literal rejected `dbt` — the obvious next call
    # after `list_trigger_bindings(provider="dbt")` returns a dbt binding.
    if provider is not None and provider not in ORCHESTRATION_PROVIDERS:
        raise ToolError(f"provider must be one of {list(ORCHESTRATION_PROVIDERS)}")
    if status is not None and status not in PIPELINE_RUN_STATUSES:
        # An unvalidated status would return `[]`, which reads as "no pipeline
        # failed" on the one question this tool exists to answer (#828).
        raise ToolError(f"status must be one of {list(PIPELINE_RUN_STATUSES)}")
    with _ctx() as (session, user):
        total = orchestration_service.count_pipeline_runs(session, provider=provider, status=status)
        runs = orchestration_service.list_pipeline_runs(
            session, provider=provider, status=status, limit=limit, offset=offset
        )
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
                    # Distinguishes "no suite was triggered" from "a suite was
                    # triggered and you cannot see it" — the same fact the
                    # docstring stated in prose and the payload conflated.
                    "dq_run_restricted": dq is not None and dq.suite_id not in accessible,
                }
            )
        return {
            "total": total,
            "returned": len(out),
            "truncated": offset + len(out) < total,
            **_page_window([pr.started_at or pr.created_at for pr in runs]),
            "pipeline_runs": out,
        }


@mcp.tool
def get_adf_pipeline_status(
    provider: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """Deprecated alias for ``get_pipeline_status`` — use that tool instead.

    Kept only so a client with this name pinned (in a saved prompt or a static
    config) does not break. The name predates dbt (ADR 0029) and Airflow
    support: despite the name, this always covered ADF **and** Airflow **and**
    dbt, which ``get_pipeline_status``'s name states honestly. Identical
    behavior in every other respect — same arguments, same return shape.
    """
    return get_pipeline_status(provider=provider, status=status, limit=limit, offset=offset)


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

    A null ``alert_snoozed_until`` rules out a per-check snooze only — it does
    **not** mean an alert would have been delivered, which also depends on the
    suite's notification config and severity routing
    (``get_notification_config``).

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
            "returned": min(len(checks), limit),
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
    not how its definition changed. **For how the check's definition changed —
    who edited it, when, and what the thresholds used to be — use
    ``list_check_versions``.** The two answer different questions and are easy to
    confuse: a check that "started failing" may have started failing because the
    data moved (visible here) or because someone tightened its threshold (visible
    there), and this tool cannot distinguish them.

    **This is a count-capped page, not a time window** — it returns the most
    recent ``limit`` results and takes no date range. When ``truncated`` is true,
    older results exist beyond ``oldest_in_page``, so the earliest point here is
    a page boundary and **not** an onset: raise ``limit`` (max 200), and if it is
    still truncated say the onset is *before* ``oldest_in_page`` rather than
    naming the first row's date. An empty ``points`` means the check has never
    produced a result, not that it has never failed, and points may include
    results from runs that never completed.

    Requires view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="view")
        total = check_service.count_check_results(session, sid, cid)
        points = check_service.list_check_result_history(session, sid, cid, limit=limit)
        return {
            "suite_id": suite_id,
            "check_id": check_id,
            # Against a real total, not `len(points) == limit` — that inference
            # is wrong on the exact-boundary page, the #925 mistake `/assets`
            # grew `X-Total-Count` to avoid. Getting it wrong here is not
            # cosmetic: with the docstring below, a COMPLETE history reported as
            # truncated makes the model refuse to name an onset it can see.
            "total": total,
            "truncated": len(points) < total,
            **_page_window([p.created_at for p in points]),
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
def list_check_versions(
    suite_id: str,
    check_id: str,
    limit: Annotated[int, Field(ge=1, le=200)] = 30,
) -> dict[str, Any]:
    """Get one check's edit history — how its definition has changed over time.

    Use this for 'who changed this threshold?', 'what did this check look like
    last week?', or 'was this check edited around the time it started failing?'.
    Returns up to ``limit`` snapshots, newest first: the ``version_no``, the
    check's name, kind, expectation type, dimension, full ``config`` and the three
    severity thresholds **as they were at that version**, plus when the change was
    made and who made it.

    ``total`` reports how many versions exist regardless of ``limit``, so a short
    page is not mistaken for the whole history — the oldest versions are the ones
    dropped, which are exactly the ones "what did this look like originally?" is
    asking for.

    This is *edit* history, not result history. **For how the check has behaved
    run over run — pass/fail and the measured value — use
    ``get_check_history``.** Answering "why did this start failing?" usually
    needs both: the data may have moved, or the definition may have.

    A snapshot is written on create and after every edit that actually changes
    something, so a check that has never been edited still has version 1 —
    **except for checks authored before version history existed**, which have no
    snapshot of their original definition. For those, ``total: 0`` means "no
    recorded history", not "never edited", and the oldest snapshot is the state
    after their first later edit, not the original.

    ``changed_by_name`` is null when the editor was a system actor or a user who
    has since been removed — read that as "the author is not recorded", not as
    "nobody edited it".

    To put the check back to one of these snapshots, use
    ``restore_check_version``. Requires view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="view")
        versions = check_service.list_check_versions(session, sid, cid)
        return {
            "suite_id": suite_id,
            "check_id": check_id,
            # Before the slice, so truncation is visible rather than implied by a
            # page that happens to be `limit` long.
            "total": len(versions),
            "returned": min(len(versions), limit),
            "truncated": len(versions) > limit,
            "versions": [
                {
                    "version_no": v.version_no,
                    "name": v.name,
                    "kind": v.kind,
                    "expectation_type": v.expectation_type,
                    # Snapshotted (ADR 0038) — reporting the check's CURRENT
                    # dimension beside an OLD config would misstate what the
                    # check was at that version.
                    "dimension": v.dimension,
                    "config": v.config,
                    # `_num`, not the raw attribute: these are NUMERIC columns and
                    # arrive as `Decimal`, which the JSON encoder refuses. REST is
                    # immune because Pydantic coerces on the way out; MCP has no
                    # Pydantic in the path, which is exactly how #1273 happened.
                    "warn_threshold": _num(v.warn_threshold),
                    "fail_threshold": _num(v.fail_threshold),
                    "critical_threshold": _num(v.critical_threshold),
                    "changed_at": v.created_at.isoformat(),
                    "changed_by_name": v.changed_by_name,
                }
                for v in versions[:limit]
            ],
        }


@mcp.tool
def list_runs(
    suite_id: str | None = None,
    status: str | None = None,
    since_hours: Annotated[float | None, Field(gt=0)] = None,
    until_hours: Annotated[float | None, Field(ge=0)] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """List recent suite runs, newest first, with each run's data-quality outcome.

    Use this to see recent runs, or to find a run id to drill into with
    ``get_run_results``. Returns, per run: its id, the
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

    ``since_hours``/``until_hours`` scope by ``created_at`` and are relative
    offsets from now ("N hours ago"), not clock times — you don't need to know
    the server's current time to use them. For "what ran today", pass
    ``since_hours=24``. For a bounded window ("yesterday only"), pass both:
    ``since_hours=48, until_hours=24`` covers the 24-48-hours-ago slice. Without
    ``since_hours`` the page is capped by COUNT, not time — read
    ``newest_in_page`` / ``oldest_in_page``, state the window you actually saw,
    and page with ``offset`` until ``oldest_in_page`` precedes the period you
    were asked about. Never describe an unfiltered page as "today's runs".

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
        run_service.validate_read_filters(
            status=status, since_hours=since_hours, until_hours=until_hours
        )
        include_all = is_workspace_admin(user)
        runs = run_service.list_runs(
            session,
            user_id=user.id,
            suite_id=sid,
            status=status,
            limit=limit,
            offset=offset,
            include_all=include_all,
            since_hours=since_hours,
            until_hours=until_hours,
        )
        # One grouped query for the whole page's outcomes, not one per run — the
        # N+1 an LLM caller cannot see the cost of (#947).
        outcomes = run_service.check_outcome_counts(session, [r.id for r in runs])
        total = run_service.count_runs(
            session,
            user_id=user.id,
            suite_id=sid,
            status=status,
            include_all=include_all,
            since_hours=since_hours,
            until_hours=until_hours,
        )
        return {
            "total": total,
            "returned": len(runs),
            # Consistent with every other paged tool: a client told to branch on
            # `truncated` reads its ABSENCE as false, and reports a capped page as
            # the whole set — the exact failure the field exists to prevent.
            "truncated": offset + len(runs) < total,
            # Still useful even WITH since_hours/until_hours set (#1442): a page
            # can be truncated by `limit` inside a wide window, so the covered
            # interval may be narrower than the window asked for.
            **_page_window([r.created_at for r in runs]),
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
        return {
            "suite_id": str(run.suite_id),
            **_run_results_payload(session, suite, run, actor=user),
        }


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

    **Read ``consecutive_run_failures`` narrowly.** It is non-zero only when
    *every* suite running on the connection is currently failing — the shape a
    dead credential has — and it is then the smallest such streak across those
    suites, capped at the last 20 runs each. A single succeeding suite resets it
    to 0 and clears the error even while another suite on the same connection
    fails every run, so a per-suite problem is invisible here; use
    ``list_runs(suite_id=…)`` for that.

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

    ``last_run_error``/``last_poll_error`` are a **stored, classified** reason
    from the connection's last real run or poll (safe to quote verbatim — never
    raw driver text). This is a different thing from ``test_connection``, which
    performs a **live** probe right now and reports pass/fail only, with no
    reason at all (its failure is unclassified by design, since the live driver
    message can carry credential fragments). Prefer this tool's stored reason
    for diagnosing why something failed; use ``test_connection`` only to check
    whether the connection currently works.
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

    ``next_run_at`` is a stored value the dispatcher advances when it fires the
    schedule, and it is reported as ``null`` whenever the schedule is disabled.
    **On an ENABLED schedule, a value already in the past means it did not fire on
    time and the dispatcher is not running** — report that rather than quoting a
    past time as the next fire. (Pausing leaves the stored value untouched, which
    is why a disabled schedule's is masked rather than shown as overdue.)

    ``last_run_at`` is when the schedule fired, not
    whether the run succeeded (see ``list_runs``).

    A disabled schedule still exists and still reads back here — it simply does
    not fire, so do not describe a suite as unscheduled on the strength of a row
    being present.

    **A cron schedule is only one of the two ways a suite runs automatically.**
    The other is an orchestration trigger — see ``list_trigger_bindings``. A suite
    with no schedule may still run nightly because a pipeline triggers it, so
    answer "when does this suite run?" from both, not from this tool alone.

    Scoped to suites the user can access (a workspace-admin sees every suite).
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

    A disabled binding still exists and still reads back here — it simply never
    fires — so check ``enabled`` before answering "is this suite wired to the
    pipeline?". And an enabled binding is *wiring*, not proof anything runs: it
    fires only if its provider connection is still receiving or polling events
    (see ``list_connections`` health) and the target suite has a run target.
    Confirm with ``list_runs``.

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
    suite's **own** override, the **workspace** default, or a linked reusable
    **channel**, plus whether the suite additionally has its own **generic
    HMAC-signed webhook(s)** (``has_generic_webhooks`` — PagerDuty/Opsgenie/
    ServiceNow/Jira-style; #1662) linked, which is a distinct channel *type* from
    the Teams webhook ``has_webhook`` reports and has no suite/workspace
    fallback of its own — it exists only via a linked channel.

    Read the ``*_source`` fields, not just the booleans, when the question is
    "who gets told": a suite with no override of its own still alerts through the
    workspace channels, so per-suite configuration being absent never means
    nobody is notified. A ``*_source`` of ``"channel"`` means a linked channel is
    that destination's ONLY active source. But delivery is **additive, not
    either/or**: Teams/Slack/email each merge the suite-or-workspace
    destination with every linked channel of the same type, so
    ``*_channel_linked`` is reported **independently** of ``*_source`` — when
    it's ``true`` alongside a ``*_source`` of ``"suite"`` or ``"workspace"``,
    alerts go to **both** destinations, not one. No MCP tool currently lists or
    identifies which channel that is; this tool can only report that one exists.

    Webhook **URLs are never returned** — only whether one is set. A webhook URL
    is a bearer credential: anyone holding it can post into that channel, so it
    is stored as a secret reference and this tool reports its presence, not its
    value.

    "Why did nobody get alerted?" is not fully answered here: this tool only
    covers whether a channel is configured and enabled. A check can also be
    **snoozed** (``list_checks``), the run's outcome can fall below the
    ``alert_on`` threshold (``list_runs``), or an unchanged repeat failure can be
    **deduplicated** rather than re-alerted (compare with the previous terminal
    run via ``list_runs`` / ``get_check_history``).

    Requires view access to the suite.
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
        #
        # A THIRD source, beside the suite override and the workspace default: a
        # reusable channel (#1514) linked to this suite. `EmailPublisher.publish`
        # and its Teams/Slack siblings all merge channel-resolved destinations in
        # alongside the legacy suite/workspace ones — reporting only the first two
        # would repeat this tool's own "nobody" false-negative for a suite that
        # relies solely on a linked channel with no suite override and no
        # workspace default configured.
        linked_channels = channel_service.list_channels_for_suite(session, sid)
        has_teams_channel = any(c.type == "teams" and c.webhook_secret_ref for c in linked_channels)
        has_slack_channel = any(c.type == "slack" and c.webhook_secret_ref for c in linked_channels)
        has_email_channel = any(c.type == "email" and c.email_recipients for c in linked_channels)
        # The fourth channel type (#1662) — has no suite/workspace fallback of its
        # own, so it never routes through `_channel()`; a suite relying solely on
        # one would otherwise be invisible to every field below. Presence-only,
        # matching `resolve_webhook_channels`' own gate — the HMAC secret is never
        # resolved here.
        has_generic_webhooks = any(
            c.type == "webhook" and c.webhook_url and c.hmac_secret_ref for c in linked_channels
        )

        def _channel(
            suite_value: Any, workspace_value: Any, *, channel_configured: bool = False
        ) -> tuple[bool, str | None]:
            # `channel_configured` is reported by the caller as a SEPARATE
            # `*_channel_linked` field, independently of the source picked here —
            # Teams/Slack/email each deliver to the union of the suite-or-workspace
            # destination AND every linked channel (`teams.py`/`slack.py`/
            # `email.py` all merge `primary` with `channel_service.resolve_*`), so
            # collapsing a channel into this single-source pick would hide that a
            # suite/workspace override and a channel can both be actively
            # delivering at once.
            if suite_value:
                return True, "suite"
            if workspace_value:
                return True, "workspace"
            if channel_configured:
                return True, "channel"
            return False, None

        has_webhook, webhook_source = _channel(
            config.webhook_secret_ref if config else None,
            settings.teams_webhook_secret_name,
            channel_configured=has_teams_channel,
        )
        has_slack, slack_source = _channel(
            config.slack_webhook_secret_ref if config else None,
            settings.slack_webhook_secret_name,
            channel_configured=has_slack_channel,
        )
        # Recipients alone do not mean email is delivered: `EmailPublisher.publish`
        # no-ops unless the workspace SMTP transport (username + password secret)
        # is configured, and that gate applies to a per-suite recipient list
        # exactly as it does to `EMAIL_TO`. Reporting recipients as "email is on"
        # would overclaim on any deployment that names recipients but never wired
        # a mailer. `email_from` is deliberately NOT part of this gate —
        # `EmailPublisher` falls back to `email_username` as the sender when it's
        # unset (matches `alerting/email.py`'s `self._sender = sender or
        # username`), so a deployment that never set an explicit sender is still
        # actively delivering, not disabled (#1724).
        smtp_ready = bool(settings.email_username and settings.email_password_secret_name)
        has_email, email_source = (
            _channel(
                config.email_recipients if config else None,
                settings.email_to,
                channel_configured=has_email_channel,
            )
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
            # Independent of webhook_source: true whenever a linked Teams channel
            # is ALSO delivering, so `source: "suite"` + `channel_linked: true`
            # means both destinations receive the alert, not one or the other.
            "webhook_channel_linked": has_teams_channel,
            "has_slack_webhook": has_slack,
            "slack_webhook_source": slack_source,
            "slack_webhook_channel_linked": has_slack_channel,
            "has_email_recipients": has_email,
            "email_recipients_source": email_source,
            "email_recipients_channel_linked": has_email_channel,
            # A distinct fourth channel type (#1662) with no suite/workspace
            # fallback — see docstring. Never conflate with has_webhook (Teams).
            "has_generic_webhooks": has_generic_webhooks,
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

    It carries **check definitions only**: no results, no run history, no
    credentials — and also **no connection, no run target, no schedules, no
    trigger bindings, no notification config and no column policy**. A suite
    created from this document is not runnable until ``update_suite`` gives it a
    target, and none of its automation comes with it. When the user asks to see
    "the whole suite", pair this with ``list_schedules``,
    ``list_trigger_bindings`` and ``get_notification_config``.

    A comparison check's baseline connection appears as its ``(name, env)``
    pair, never as an id or anything resolvable to a secret. Requires view
    access to the suite.
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

    Use this for 'run the orders suite'. Queues the suite and dispatches it to
    the worker, returning the new run's id and queued status — poll
    ``get_run_status`` with that id for progress. Requires edit access. Fails
    fast if the suite has no valid run target configured.

    **You cannot choose the environment or the dataset here.** A run always uses
    the suite's own connection and run target, both fixed on the suite. If the
    user names an environment, check it against ``list_suites`` /
    ``list_connections`` first — a suite bound to QA cannot be run against DEV
    from this tool, and getting a DEV equivalent means ``import_suite`` onto a
    DEV connection plus ``update_suite`` to give it a target.

    There is no de-duplication: calling twice starts two concurrent runs, and
    this tool cannot see a run a schedule or pipeline trigger started moments
    ago — check ``list_runs`` before re-triggering. Every check in the suite
    runs, including snoozed ones (a snooze mutes alerting only); there is no way
    to run a single check.
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
    resolve at once at the end. Read a rising ``elapsed_ms`` with
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
            # The same #318 gate `list_runs` and `get_run_results` apply, which
            # this tool was missing: while false, `counts` and the per-check
            # statuses describe only the phases committed so far. A 30-check
            # suite three checks in reports `{"pass": 3}`, which is a true
            # progress reading and a false verdict.
            "results_final": progress.run.status in rollup.AGGREGATABLE_RUN_STATUSES,
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
    """Add a new check (a Great Expectations expectation) to a suite. Requires
    edit access to the suite. Returns the created check's id.

    Use this for 'add a null check on email to the customer suite'. ``name`` is a
    human label; ``expectation_type`` is a GX expectation (e.g.
    ``expect_column_values_to_not_be_null``); ``config`` carries its arguments
    (e.g. ``{"column": "email"}``). Optional warn/fail/critical thresholds band
    the result severity.

    **DataQ enables a vetted SUBSET of GX's built-ins, not all of them.** A type
    outside it is refused even when Great Expectations itself has it — the error
    says which of the two happened and lists every accepted type, so read it
    rather than re-guessing. Scalar aggregates (``expect_column_mean_to_be_between``
    and siblings) are deliberately absent: use a ``volume`` or ``anomaly`` monitor
    for those, and a custom-SQL check for any rule with no vetted type.

    **`config` is validated against GX's own schema only — never against the
    datasource.** A column name that doesn't exist (a typo, wrong case) is
    accepted here and only surfaces as an `error` result the next time the
    suite runs. Confirm a column name with `list_columns` first. For an
    ``expectation``-kind check, `dryrun_check` can also preview it against
    live data before creating it — but `dryrun_check` only supports
    `expectation`, `schema_drift` and `anomaly`; a `freshness` or `volume`
    monitor has no preview and can only be checked by creating it and running
    the suite.

    For a monitor rather than a rule, set ``kind`` and pair it with
    ``expectation_type="monitor:<kind>"``: ``freshness`` (hours since
    ``MAX(column)``), ``volume`` (row count vs ``min_rows``/``max_rows`` — this
    counts the true dataset size on every datasource, including ADLS / S3 /
    Iceberg, unlike an ordinary row-count *expectation* check on a sampled
    suite, which measures the sample), ``schema_drift`` (columns
    added/removed/retyped vs a learned baseline), or ``anomaly`` (see below).

    For a cross-dataset reconciliation check use ``kind="comparison"`` with
    ``expectation_type="comparison:records"``, ``source_connection_id`` (the
    baseline connection to compare against) and a config carrying ``source``
    (the baseline dataset spec) + ``keys`` (join key columns).

    **`anomaly`** — "tell me when this looks unusual compared to normal" —
    learns a rolling mean/stddev of the table's own ``row_count`` or
    ``freshness_age_hours`` and scores each run's z-score against it. Its
    config takes:
    - ``target_metric`` (required): ``row_count`` or ``freshness_age_hours``.
    - ``column`` (required only for ``freshness_age_hours``): which timestamp
      column to measure.
    - ``window`` (optional): how many past runs the rolling baseline covers.
    - ``min_points`` (optional): runs needed before scoring starts — earlier
      runs report ``skip``, not a false anomaly.
    - ``seasonality`` (optional): set to compare against the same day-of-week
      rather than a flat rolling window.

    It needs a positive fail/critical threshold, which is the z-score
    sensitivity (3 is a common starting point) — this is not a value the
    metric itself will ever reach, so do not treat it like a domain threshold.

    ``dimension`` optionally overrides the DQ dimension (one of accuracy,
    completeness, consistency, integrity, timeliness, uniqueness, validity);
    leave it unset and DataQ derives it from the check type where derivable.
    **A returned `dimension: null` means "unclassified", not "failed to
    save"** — accuracy/integrity and custom SQL are never derivable, and an
    unclassified check renders as a coverage gap on the asset scorecard, not
    an error. Only pass ``dimension`` explicitly when the user names one.

    **Creating a check does not run it.** It takes effect on the suite's next
    run (manual, scheduled, or trigger-fired) and changes nothing about past
    runs or results.
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
    to empty** through this tool. Say so rather than passing 0 or an empty string,
    which would set that value, not clear it. If an earlier version of the check
    had the field empty, ``restore_check_version`` will clear it — that is the
    one path that applies emptiness rather than skipping it; otherwise the check
    must be recreated. ``kind`` cannot be changed at all; recreate the check as
    the other kind.

    Changing ``expectation_type`` is held to the same vetted set ``create_check``
    describes, so a type outside it is refused here too and the check keeps its
    current definition.

    Every update snapshots the new state as a check version, so the change is
    reviewable with ``list_check_versions`` and reversible with
    ``restore_check_version``. Requires edit access to the suite.
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
def restore_check_version(
    suite_id: str,
    check_id: str,
    version_no: Annotated[int, Field(ge=1)],
) -> dict[str, Any]:
    """Put a check back to one of its earlier versions.

    Use this for 'undo that threshold change' or 'restore the orders row-count
    check to how it was on Monday'. Get ``version_no`` from
    ``list_check_versions`` — do not guess it.

    Unlike ``update_check``, this applies the whole snapshot, including fields
    that were empty at that version: restoring a version that had no warn
    threshold clears the warn threshold, rather than leaving today's value in
    place. That is the point of a restore, and it is why this is not the same as
    patching the fields back by hand.

    **Nothing is lost and nothing is renumbered.** History is additive: the
    restore is recorded as a new version on top, so the state you are replacing
    remains in ``list_check_versions`` and can itself be restored. Restoring the
    version the check is already on is a no-op and records nothing.

    **An old snapshot can be refused.** It is re-validated against today's rules,
    not simply written back, so a version created before a validation rule
    shipped may be rejected — the error names what is wrong and the check is left
    exactly as it was. That is deliberate: it prevents reinstating a definition
    the authoring path would no longer accept.

    A check's kind is immutable and is never changed by a restore. Requires edit
    access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        check = check_service.restore_check_version(session, sid, cid, version_no, actor_id=user.id)
        return {**_check_summary(check), "restored_from_version": version_no}


@mcp.tool
def delete_check(suite_id: str, check_id: str) -> dict[str, Any]:
    """Permanently delete a check from a suite — **and every result it ever
    recorded**.

    Use this for 'remove the row-count check from the orders suite', but read the
    scope first: the delete cascades. The check, its version history, its stored
    monitor baseline, **all of its historical results, and every incident it ever
    raised — including one currently open or acknowledged** — go with it, so past
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
        check_service.delete_check(session, sid, cid, actor_id=user.id)
        return {"deleted": True, "check_id": check_id, "name": name}


@mcp.tool
def snooze_check(
    suite_id: str,
    check_id: str,
    hours: Annotated[float, Field(gt=0, le=8760)] | None = None,
) -> dict[str, Any]:
    """Mute a check's alerts for a while — or un-mute it now.

    **Suppression is per *run*, not per check.** An alert is only withheld when
    EVERY failing check in that run is snoozed — so silencing one noisy check in
    a suite that fails for several reasons does not stop the alerts, and the
    alert that fires still contains this check's failure. A run that fails to
    *execute* (dead credential, worker error) always alerts, snooze or not.

    Only alert **delivery** is muted. The check still runs, still records a
    failing result, still counts toward the run's ``worst_severity``, and still
    opens an incident visible to ``list_incidents``.

    Use this for 'stop alerting on the freshness check until tomorrow' or, with
    ``hours`` omitted, 'turn alerts back on for that check'. Pass ``hours`` to
    snooze for that many hours from now; omit it to clear any snooze immediately.

    A snoozed check **still runs and still fails** — only the alert is
    suppressed. Do not describe a snoozed check as disabled, and do not reach for
    this when the user wants the check to stop evaluating. Requires edit access.

    There is no separate unsnooze tool — omitting ``hours`` is how you
    un-mute. **The consequence is that "un-mute", "un-snooze", "turn
    alerts back on" and "start alerting again" are all served by this tool too,
    despite its name saying the opposite** — call it with ``hours`` omitted.
    There is no separate unsnooze tool to look for.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    cid = _parse_uuid(check_id, field="check_id")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        check = (
            check_service.snooze_check(session, sid, cid, hours=hours, actor_id=user.id)
            if hours is not None
            else check_service.clear_check_snooze(session, sid, cid, actor_id=user.id)
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

    **Only ``expectation``, ``schema_drift`` and ``anomaly`` kinds can be
    previewed.** A ``freshness`` or ``volume`` monitor check has no dry-run
    support at all — refused with an error. The only way to check one is to
    create it and run the suite.

    **A preview reads what a real run would read, so it inherits the target's
    sampling.** On ADLS / S3 / Iceberg the evaluation may be over a capped
    sample rather than the whole dataset — so a ``pass`` describes the
    sample, and an ``expect_table_row_count_to_be_between`` preview's row
    count is the SAMPLE's count, not the file's. (This is unrelated to the
    ``volume`` monitor kind, which counts the true dataset size on every
    datasource and cannot reach this tool at all — see above.) Say so rather
    than reporting either as a fact about the full table.

    A preview is held to the same rules a save is, so an ``expectation_type``
    outside DataQ's vetted set (see ``create_check``) is refused here rather than
    previewed and then rejected at creation.

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
        # Redaction follows the DESTINATION (#1419/#1479, `services.live_probe`).
        # An MCP consumer is `EGRESS`: a model that will quote the value into a
        # conversation and may carry it further, so it masks — while the REST
        # dry-run panel is `INTERACTIVE` and shows values. Under the old
        # column-property framing those two were a contradiction; under the
        # destination rule they are the right answer twice. Masking here also
        # keeps the preview agreeing with `get_suite_results`, which redacts the
        # same column on the same suite — an LLM seeing a value in one and a mask
        # in the other has no way to tell which is the truth.
        live_probe.record_probe_access(
            session,
            action="check.dryrun",
            suite_id=suite.id,
            actor=user,
            destination=live_probe.Destination.EGRESS,
            masked=True,
            columns=[c] if (c := (config or {}).get("column")) else None,
            detail={"expectation_type": expectation_type, "kind": kind},
            actor_kind="user",
        )
        return {
            "status": outcome.status,
            "metric_value": _num(outcome.metric_value),
            "observed_value": live_probe.redact_probe_observed_value(
                outcome.observed_value,
                tested_column=(config or {}).get("column"),
                policy=suite.column_policy,
                # The governance floor applies to a PREVIEW too (G3, #433). A
                # dry-run reads live warehouse data and hands it to a model, so a
                # tag honoured on the results path and not here would mask the
                # column in the record and expose it in the rehearsal.
                tags=_asset_column_tags(session, suite),
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
    all; the stored expression is still there, and ``update_schedule`` with
    ``enabled=true`` starts it firing and reports the resolved fire time then.

    A suite may be scheduled before its run target is configured; the dispatcher
    re-checks at fire time and skips if it is still unset. Requires edit access.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    # The same screen `update_schedule` applies to the same two columns. Cron and
    # timezone validation would reject a NUL-bearing value anyway, but as "invalid
    # timezone" — which sends an assistant looking for a zone-name typo that isn't
    # there. Guarding one door and not its sibling is this surface's recurring
    # defect; here it was inverted, with the newer door the guarded one.
    if contains_nul({"cron": cron, "timezone": timezone}):
        raise ToolError("NUL (\\x00) characters are not allowed in a schedule's fields")
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
def update_schedule(
    schedule_id: str,
    # Bounded to the columns, exactly as `create_schedule` is: an unbounded
    # LLM-generated string reaches Postgres as StringDataRightTruncation, which is
    # a psycopg error rather than a `DataQError` and so escapes `_service_errors`
    # (#567's class). A guard on the create door and not the update door is no
    # guard at all.
    cron: Annotated[str | None, Field(min_length=1, max_length=128)] = None,
    timezone: Annotated[str | None, Field(min_length=1, max_length=64)] = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Change a suite's cron schedule — its cadence, its timezone, or pause/resume it.

    Use this for 'move the orders run to 3am', 'pause the nightly schedule', or
    'switch that schedule to Toronto time'. Every argument is optional and an
    omitted one is left exactly as it was, so pausing a schedule is
    ``enabled=false`` alone — you do not need to restate the cron.

    ``cron`` is a standard five-field expression and ``timezone`` an IANA name
    (``America/Toronto``), **not** a UTC offset. Both are validated; an invalid
    one is an error rather than a schedule that silently stops firing.

    Returns the schedule's new state including ``next_run_at`` — when it will
    actually fire next, or ``null`` if the result is disabled, because a paused
    schedule does not fire at all. When it is present, confirm the change to the
    user from it rather than from the cron string, since it is the resolved
    interpretation and the cron is only the input. When it is ``null`` the
    schedule is paused: say that instead of naming a time, and note that
    retiming a paused schedule stores the new cadence without scheduling
    anything until it is re-enabled.

    **Resuming a paused schedule re-bases it; it does not backfill.** Runs that
    would have happened while it was paused do not happen retroactively — the
    schedule simply starts again from its next future slot. If the user wants the
    missed run, trigger it explicitly with ``trigger_suite_run``.

    **This governs the cron schedule only.** A suite can also be started by an
    orchestration trigger binding when a pipeline finishes, and on demand via
    ``trigger_suite_run``. Before telling a user the suite will not run, check
    ``list_trigger_bindings`` for it — pausing here does not disable a binding
    (use ``update_trigger_binding``). The change is picked up by the dispatcher
    within about a minute and does **not** cancel a run already queued or in
    progress (use ``cancel_run``).

    To stop the cron for good use ``delete_schedule``; disabling keeps the row
    and the expression. Requires edit access to the
    schedule's suite.
    """
    schid = _parse_uuid(schedule_id, field="schedule_id")
    # Same boundary rejection every other free-text tool argument gets: NUL can't
    # be stored by Postgres and the driver's ValueError would escape
    # `_service_errors` as an opaque internal failure (#567).
    if contains_nul({"cron": cron or "", "timezone": timezone or ""}):
        raise ToolError("NUL (\\x00) characters are not allowed in a schedule's fields")
    with _ctx() as (session, user), _service_errors():
        # `update_schedule` resolves the schedule and gates on ITS suite (404 for a
        # caller who cannot see that suite), so the id alone is not a way in.
        schedule = schedule_service.update_schedule(
            session,
            schid,
            user_id=user.id,
            cron_expr=cron,
            timezone=timezone,
            enabled=enabled,
        )
        return {
            "id": str(schedule.id),
            "suite_id": str(schedule.suite_id),
            "cron": schedule.cron,
            "timezone": schedule.timezone,
            "enabled": schedule.enabled,
            "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
            # `null` when disabled, not the stored timestamp — the column always
            # holds a computed next fire, but the dispatcher filters on `enabled`
            # and never reaches it. Same rule as `create_schedule` and
            # `list_schedules`; the three must not disagree about a paused
            # schedule, which is precisely the state a user asks about.
            "next_run_at": (schedule.next_run_at.isoformat() if schedule.enabled else None),
        }


@mcp.tool
def delete_schedule(schedule_id: str) -> dict[str, Any]:
    """Delete a suite's cron schedule so it stops running automatically.

    Use this for 'stop the nightly orders run'. The suite and its checks are
    untouched — only the automatic trigger goes, and the suite can still be run
    on demand.

    **If the intent is a pause rather than a removal, use ``update_schedule``
    with ``enabled=false`` instead** — that keeps the row and the cron expression
    so it can be resumed later, where this tool discards both. Requires edit
    access to the schedule's suite.
    """
    schid = _parse_uuid(schedule_id, field="schedule_id")
    with _ctx() as (session, user), _service_errors():
        # Read it BEFORE deleting so the response can say what was destroyed —
        # `delete_check` does this for the same reason. Handed the wrong id, a
        # bare `{"deleted": true}` lets a model confirm the deletion of "the
        # nightly run" with nothing in the payload to contradict it.
        schedule = schedule_service.get_schedule(session, schid, user_id=user.id)
        deleted = {
            "suite_id": str(schedule.suite_id),
            "cron": schedule.cron,
            "timezone": schedule.timezone,
            "enabled": schedule.enabled,
        }
        # `delete_schedule` resolves the schedule and gates on ITS suite (404 for
        # a caller who cannot see that suite), so the id alone is not a way in.
        schedule_service.delete_schedule(session, schid, user_id=user.id)
        return {"deleted": True, "schedule_id": schedule_id, **deleted}


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
    they name exactly that class of silent no-fire. But an **empty** ``warnings``
    does not mean the wiring is sound: no ambiguity check runs at all when the
    binding is created disabled, and if no orchestration connection exists for
    this provider and environment, DataQ never observes that pipeline and the
    binding can never fire. Check ``list_connections(type=provider, env=env)``
    before telling the user it is wired. When they later report the suite did not
    run, ``get_near_misses`` shows the mismatches actually observed.

    **An "already exists" error does not mean the trigger works.** The uniqueness
    key is provider + pipeline + environment + suite and does **not** include
    ``enabled``, so a *disabled* binding collides here exactly like a live one. If
    you get that error while wiring something up, check
    ``list_trigger_bindings`` and, if the existing one is disabled, enable it with
    ``update_trigger_binding`` — otherwise you will report the trigger as in place
    when it never fires. Requires edit access.
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
def update_trigger_binding(binding_id: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable an orchestration trigger without deleting it.

    Use this for 'stop the orders suite running after the nightly load, but keep
    the wiring' or to switch one back on. A disabled binding still exists and
    still reads back from ``list_trigger_bindings`` — it simply never fires.
    That stops **this pipeline** from starting the suite; it does not stop a cron
    schedule, another binding on a different pipeline or environment, or a manual
    ``trigger_suite_run``. Check those before saying the suite will no longer
    run.

    **What a binding points at cannot be changed here.** Its provider,
    pipeline/DAG id, environment and target suite are its identity and are
    immutable; to re-target it, delete it with ``delete_trigger_binding`` and
    create a new one. This tool only flips the switch.

    Any returned ``warnings`` are advisory, not errors — read them out. They are
    recomputed on enable rather than carried over from creation, because
    re-enabling a binding is exactly when a provider/environment ambiguity
    becomes able to lose triggers again; ``get_near_misses`` reports the
    mismatches that have actually cost a trigger. Requires edit access to the
    binding's suite.
    """
    bid = _parse_uuid(binding_id, field="binding_id")
    with _ctx() as (session, user), _service_errors():
        # Resolves the binding and gates on ITS suite (404 for a caller who cannot
        # see that suite), so the id alone is not a way in.
        result = trigger_binding_service.update_binding(
            session, bid, user_id=user.id, enabled=enabled
        )
        return {
            "id": str(result.binding.id),
            "provider": result.binding.provider,
            "pipeline_or_dag_id": result.binding.pipeline_or_dag_id,
            "env": result.binding.env,
            "suite_id": str(result.binding.suite_id),
            "enabled": result.binding.enabled,
            "warnings": [
                {"code": w.code, "message": w.message, "other_envs": w.other_envs}
                for w in result.warnings
            ],
        }


@mcp.tool
def delete_trigger_binding(binding_id: str) -> dict[str, Any]:
    """Delete an orchestration trigger so a pipeline stops running its suite.

    Use this for 'unhook the orders checks from the nightly load'. The suite, its
    checks and the pipeline are all untouched — only the link between them goes,
    and the suite can still be run on demand or on a cron schedule.

    **If the intent is a pause rather than a removal, use
    ``update_trigger_binding`` with ``enabled=false`` instead** — that keeps the
    binding so it can be switched back on, where this tool discards it and
    re-creating one means knowing the provider, pipeline id and environment
    again. Requires edit access to the binding's suite.
    """
    bid = _parse_uuid(binding_id, field="binding_id")
    with _ctx() as (session, user), _service_errors():
        # Read before deleting, like `delete_schedule` — and here the echoed
        # fields are exactly what re-creating the binding would require, which
        # the docstring already tells the caller they will need.
        binding = trigger_binding_service.get_binding(session, bid, user_id=user.id)
        deleted = {
            "provider": binding.provider,
            "pipeline_or_dag_id": binding.pipeline_or_dag_id,
            "env": binding.env,
            "suite_id": str(binding.suite_id),
        }
        trigger_binding_service.delete_binding(session, bid, user_id=user.id)
        return {"deleted": True, "binding_id": binding_id, **deleted}


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
    Present it as a proposal for the user to confirm, then apply it with
    ``set_column_policy`` — reading the current one with ``get_column_policy``
    first, since setting a policy replaces it wholesale; do not
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
    connection using the stored credential and reports success or a failure.

    **What a pass proves is narrow:** the credential authenticates and the
    datasource answers a trivial query. It does **not** check that a suite's
    target table exists, that the role can read it, or that the run
    configuration is complete — a connection can pass here and fail every suite
    run (a Snowflake connection with no Role does exactly that). An ``ok: true``
    beside a failing run means "not a dead credential", not "the connection is
    fine".

    A failure is deliberately **unclassified**: the driver's own message can
    carry DSN and credential fragments, so it is withheld. Do not speculate
    about the cause — report that the probe failed and that the server logs
    carry the detail. This is different from ``list_connections``, whose
    ``last_run_error``/``last_poll_error`` ARE classified (from the connection's
    last real run/poll, not a live probe) — prefer that tool when you want a
    reason rather than a pass/fail.

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

    **Only the name, description and check definitions are copied.** Not
    copied, and each needing to be recreated deliberately: the run target (so the
    new suite is **not runnable** — the returned ``runnable`` says so, and
    ``update_suite`` is the fix), schedules, trigger bindings, the notification
    config, the column redaction policy, and any shares. This is a copy of the
    RULES, not of the automation around them.

    Creates a **new** suite owned by you, with none of the source suite's shares
    carried over (workspace admins still see it, as they see every suite). It
    never merges into or overwrites an existing one, so importing twice gives you
    two suites. The whole document is
    validated before anything is written, so a bad check means nothing is created
    rather than a half-built suite. That validation includes DataQ's vetted set of
    GX expectation types: a document written by hand, or exported from a build that
    enabled more types, is refused as a whole — the error names the offending type
    and lists the accepted ones, so remove or replace that check and re-import.

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
            # A read of what was STORED, not an echo of the argument — the field
            # is only useful as confirmation if it can disagree with the input.
            "check_count": len(suite.checks),
            # An export document carries no run target, so an imported suite
            # cannot run until `update_suite` gives it one. The tool that creates
            # that state now says so in data, instead of leaving the caller to
            # discover it when `trigger_suite_run` fails.
            "target": suite.target,
            "runnable": suite.target is not None,
        }


@mcp.tool
def update_suite(
    suite_id: str,
    name: Annotated[str, Field(min_length=1, max_length=128)] | None = None,
    description: Annotated[str, Field(max_length=1024)] | None = None,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Change a suite's name, description, or **what it runs against**.

    Use this for 'point the orders suite at ANALYTICS.ORDERS_V2', 'rename it', or
    — most often — to give a newly imported suite a run target, since a suite
    without one cannot be run at all.

    ``target`` says which dataset the suite's checks execute against, and its
    shape depends on the connection's type: ``{"table": ..., "schema": ...}`` for
    a SQL warehouse, ``{"catalog": ..., "schema": ..., "table": ...}`` for Unity
    Catalog, ``{"namespace": ..., "table": ...}`` for Iceberg, or
    ``{"path": ..., "file_format": "csv"|"parquet"}`` for a flat file. A flat-file
    target can instead select a rolling **batch** with ``pattern`` +
    ``strategy``. An invalid shape for the connection type is rejected.

    Only what you pass changes; omitted arguments are left alone, and ``target``
    is **replaced wholesale** rather than merged — send the complete target, not
    just the field you are changing. A suite's connection cannot be changed at
    all: create or import the suite against the connection you want.

    Requires edit access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    # Namespaced, NOT merged into one dict: a `target` key called "name" would
    # shadow the suite name and slip a NUL past the check — #567's class defeated
    # by dict-merge shadowing rather than by a missing guard.
    if contains_nul({"name": name or "", "description": description or "", "target": target or {}}):
        raise ToolError("NUL (\\x00) characters are not allowed in a suite's fields")
    # Through the REST route's own request model, not a hand-rolled check: it is
    # what validates `file_format`, caps every string, and rejects unknown keys.
    # Passing the raw dict to the service saved an `xlsx` file_format that then
    # failed every run — a config error deferred to execution.
    parsed = _parse_suite_target(target)
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        had_policy = suite.column_policy is not None
        old_target = dict(suite.target) if suite.target else None
        suite = suite_service.update_suite(
            session, sid, name=name, description=description, target=parsed, actor_id=user.id
        )
        # Parity with the REST route (#634): a target-setting update on a
        # policy-less suite gets the same best-effort auto-classify as create.
        # Without it, a suite imported and made runnable over MCP never derives a
        # redaction policy and captures failing samples with no row locator.
        policy_pending = False
        policy_may_be_stale = False
        if parsed is not None and suite.target is not None and suite.column_policy is None:
            run_dispatch.dispatch_auto_classify(suite.id)
            policy_pending = True
        elif had_policy and parsed is not None and parsed != old_target:
            # Re-pointing a policied suite can strand its policy — the stored
            # columns may not exist in the new target. Deliberately not
            # re-derived (#642); made observable instead (#643).
            log.warning(
                "suite_policy_possibly_stale",
                suite_id=str(suite.id),
                reason="target_changed_on_policied_suite",
            )
            policy_may_be_stale = True
        return {
            "id": str(suite.id),
            "name": suite.name,
            "description": suite.description,
            "connection_id": str(suite.connection_id),
            "target": suite.target,
            # Whether the suite can actually run now — the question this tool is
            # usually called to fix, and one an LLM should confirm rather than
            # infer from the absence of an error. It is the ONE precondition
            # `trigger_suite_run` fails fast on — not a prediction that the run
            # will succeed: the suite may have no checks, the credential may be
            # dead, and the target table may not exist. None of that is checked.
            "runnable": suite.target is not None,
            # Both of these were previously emitted to the server log only — and
            # the caller who just re-pointed the suite is the one person able to
            # act on them.
            "column_policy_pending": policy_pending,
            "column_policy_may_be_stale": policy_may_be_stale,
        }


@mcp.tool
def get_column_policy(suite_id: str) -> dict[str, Any]:
    """Get the suite's failing-sample redaction policy — which columns are masked.

    Use this to answer 'is the email column masked in failure samples?' or before
    proposing a change with ``set_column_policy``. Returns the
    ``identifier_column`` (the one non-PII column shown so a failing row can be
    located) and ``pii_columns`` (always masked).

    A suite with **no policy set** reports both as empty. That does **not** mean
    nothing is masked: DataQ still applies a governance floor from datasource
    tags and a name/value classifier at redaction time, and that floor overrules
    this policy. Read an empty policy as "no suite-level override", never as "no
    protection". Requires view access to the suite.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="view")
        policy = suite.column_policy or {}
        return {
            "suite_id": suite_id,
            "configured": suite.column_policy is not None,
            "identifier_column": policy.get("identifier_column"),
            "pii_columns": policy.get("pii_columns", []),
            # Returned so a read-modify-write can preserve it. Without this
            # field an assistant following this tool's own advice — read the
            # policy, change one thing, write it back — has no way to know
            # fail-closed was on, and the write would carry it away.
            "require_classification": bool(policy.get("require_classification")),
        }


@mcp.tool
def set_column_policy(
    suite_id: str,
    # Bounded like the REST twin: the policy is walked on every read-time
    # redaction, so an unbounded list from an LLM argument is paid on every
    # sample render, not once at write.
    pii_columns: Annotated[list[str], Field(max_length=200)],
    identifier_column: Annotated[str, Field(max_length=255)] | None = None,
) -> dict[str, Any]:
    """Set which columns are masked in this suite's failing-sample rows.

    Use this to apply what ``suggest_column_policy`` proposed, or to act on 'mask
    the email column in failure samples'. ``pii_columns`` are always masked;
    ``identifier_column`` is the single non-PII column left visible so a failing
    row can still be located. The identifier may not also be listed as PII, and
    may not itself classify as direct PII — either is rejected.

    **Do not promise that a column will become visible.** Masking is decided by
    three layers, and this policy is only the middle one: a datasource governance
    tag always masks, and an unclassified column defaults to masked. So removing
    a column from ``pii_columns`` usually does *not* reveal it.

    The one case that does reveal a column is naming it as ``identifier_column``
    — and only when it does not itself classify as PII (an ``EMAIL`` named as the
    identifier stays masked). Since this call replaces the whole policy, dropping
    a column from ``pii_columns`` can re-expose it if it is also the tested
    column or the identifier. Check the result with ``get_column_policy`` and a
    real sample rather than asserting either outcome.

    **This replaces the whole policy**, it does not add to it: send the complete
    list, and read the current one with ``get_column_policy`` first if you are
    adding a column.

    Passing an empty ``pii_columns`` does **not** clear the override — it stores
    an empty policy, which still counts as configured and permanently opts the
    suite out of automatic PII classification. There is no way to un-set a policy
    from here; if that is what the user wants, say so rather than sending an
    empty list.

    What changes is how samples are **displayed**. Masking applies immediately,
    including to runs that already happened, and so does the identifier's
    un-masking effect at read time. What is *not* retroactive is which locator
    column was **captured**: that was chosen when the run executed, so a past run
    can only ever show an identifier whose column is present in its stored
    sample.
    Requires edit access.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    # Namespaced for the same reason as `update_suite`: a PII column literally
    # named "identifier_column" would otherwise shadow the checked value.
    if contains_nul({"identifier_column": identifier_column or "", "pii_columns": pii_columns}):
        raise ToolError("NUL (\\x00) characters are not allowed in a column policy")
    with _ctx() as (session, user), _service_errors():
        require_permission(session, sid, user.id, minimum="edit")
        suite = suite_service.set_column_policy(
            session,
            sid,
            identifier_column=identifier_column,
            pii_columns=pii_columns,
            # Omitted, so the stored value is preserved. This tool deliberately
            # has no parameter for fail-closed mode: it is a compliance posture
            # an operator sets in the app, not something an assistant should be
            # able to switch off in passing while editing a PII column list.
            require_classification=None,
            actor_id=user.id,
        )
        policy = suite.column_policy or {}
        return {
            "suite_id": suite_id,
            "identifier_column": policy.get("identifier_column"),
            "pii_columns": policy.get("pii_columns", []),
            # Returned so a read-modify-write can preserve it. Without this
            # field an assistant following this tool's own advice — read the
            # policy, change one thing, write it back — has no way to know
            # fail-closed was on, and the write would carry it away.
            "require_classification": bool(policy.get("require_classification")),
        }


# ── assets & incidents (ADR 0034 / 0037, Tier 3B) ─────────────────────────


def _asset_summary_payload(a: Any) -> dict[str, Any]:
    """One asset's workspace-true rollup, LLM-shaped.

    `monitored` is derived rather than left to the reader: `suite_count == 0` is
    an asset DataQ knows about but nothing checks (the #1103 inventory-sync
    case), and "no failures" is a true and useless statement about it.
    """
    return {
        "id": str(a.id),
        "namespace": a.namespace,
        "name": a.name,
        "env": a.env,
        "description": a.description,
        "suite_count": a.suite_count,
        "monitored": a.suite_count > 0,
        # ── data quality ──
        "worst_severity": a.worst_severity,
        "checks_total": a.checks_total,
        "checks_passed": a.checks_passed,
        "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
        # ── reachability / execution (never data quality) ──
        "has_operational_error": a.has_operational_error,
        "has_skip": a.has_skip,
        "has_failed_run": a.has_failed_run,
        "has_active_run": a.has_active_run,
        "has_cancelled_run": a.has_cancelled_run,
    }


@mcp.tool
def list_assets(
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """List the data assets (tables, views, files) DataQ knows about, with health.

    Use this for 'what tables do we monitor?' or as the first step in 'is orders
    healthy?'.

    **Assets are returned in alphabetical order and there is no health filter or
    sort.** A truncated page is therefore an alphabetical slice, never "the
    unhealthiest assets" — to answer that, page with ``offset`` until
    ``truncated`` is false. An asset appears here only because a suite targets
    it, lineage emitted it, or its connection has inventory sync enabled, so a
    table absent from the list may simply never have been enumerated.

    Assets are the grain people think in, whereas suites are the grain checks are
    authored in. Returns each asset's namespace, name, environment, how many
    suites target it, and its latest health, plus ``total`` / ``truncated`` so
    you can tell a page from the whole set.

    **The health numbers are workspace-true, not scoped to this user.** They
    aggregate over EVERY suite targeting the asset, including
    suites the caller has no grant on. That is deliberate — one verdict per
    asset, identical for everyone — but it means you must not describe these
    figures as "your" checks or imply the caller could see them all. Only
    `get_asset`'s composing-suite LIST is filtered to their grants.

    Reading the fields honestly:

    - `worst_severity: null` means "nothing is currently failing", which covers
      **both** all-passed and nothing-ever-evaluated. Check `checks_total` and
      `last_run_at` before calling an asset healthy.
    - `monitored: false` (`suite_count: 0`) means no suite targets it at all — it
      is unchecked, not passing. Never report it as clean.
    - `has_operational_error` / `has_skip` / `has_failed_run` are *reachability*,
      not data quality: DataQ could not evaluate against the datasource, or a
      precondition was not met. An asset can be reachability-broken with
      `worst_severity: null`, and that is a problem, not a pass.
    """
    with _ctx() as (session, _user), _service_errors():
        total = asset_view_service.count_assets(session)
        assets = asset_view_service.list_visible_assets(session, limit=limit, offset=offset)
        return {
            "total": total,
            "returned": len(assets),
            # Explicit rather than left to be inferred from `len == limit`, which
            # is wrong on the exact-boundary page (#925 — the same reason the REST
            # route grew X-Total-Count).
            "truncated": offset + len(assets) < total,
            "assets": [_asset_summary_payload(a) for a in assets],
        }


@mcp.tool
def get_asset(asset_id: str) -> dict[str, Any]:
    """Get one asset's health, the suites that check it, and its lineage neighbours.

    Use this for 'is the orders table healthy?', 'what checks run on this table?',
    'what feeds this table?' or 'what breaks if this is wrong?'. Returns the
    workspace-true summary, a per-dimension `scorecard`, the composing suites the
    caller can see (each with its latest run), and the upstream/downstream lineage
    neighbourhood with the edges connecting them.

    Two scoping rules that must not be conflated:

    - `summary` and `scorecard` are **workspace-true** — computed over every
      suite targeting the asset, including ones the caller cannot see.
    - `suites` lists **only** what the caller may view; `restricted_suite_count`
      is how many more compose the asset. When that count is above zero, say so:
      the listed checks are not the whole story behind the summary.

    `scorecard.uncovered` names DQ dimensions with **no checks at all** — the
    actionable half, and usually a better answer to "how is this asset doing?"
    than the score. A dimension `score` of `null` means nothing evaluated, which
    is neither 0 nor 100. `unclassified_checks` are checks with no dimension
    (custom SQL, or never classified) and are deliberately not bucketed anywhere.

    `lineage.qualified_by` is non-empty when a lineage source is failing, stale or
    coarse. In that case an empty or thin neighbour list proves nothing about the
    real graph — report the qualification rather than "nothing feeds this table".
    """
    aid = _parse_uuid(asset_id, field="asset_id")
    with _ctx() as (session, user), _service_errors():
        detail = asset_view_service.get_visible_asset(
            session, aid, user_id=user.id, include_all=is_workspace_admin(user)
        )
        qualifiers: list[str] = []
        for src in detail.failing_lineage_sources:
            qualifiers.append(
                f"lineage poll failing on connection '{src.name}' "
                f"({src.consecutive_failures} consecutive failures)"
            )
        for wh in detail.warehouse_lineage_status:
            if wh.last_error:
                qualifiers.append(f"warehouse lineage refresh failing on '{wh.name}'")
            if wh.stale:
                qualifiers.append(f"warehouse lineage on '{wh.name}' has not refreshed recently")
            if wh.degraded_reason:
                qualifiers.append(
                    f"warehouse lineage on '{wh.name}' is coarse: {wh.degraded_reason}"
                )
        scorecard = detail.scorecard
        return {
            "summary": _asset_summary_payload(detail.summary),
            "scorecard": (
                None
                if scorecard is None
                else {
                    "covered": [
                        {
                            "dimension": d.dimension,
                            "checks_total": d.checks_total,
                            "checks_passing": d.checks_passing,
                            "checks_evaluated": d.checks_evaluated,
                            "score": _num(d.score),
                        }
                        for d in scorecard.covered
                    ],
                    "uncovered": list(scorecard.uncovered),
                    "unclassified_checks": scorecard.unclassified_checks,
                }
            ),
            "suites": [
                {
                    "suite_id": str(cs.suite_id),
                    "name": cs.name,
                    "my_permission": cs.my_permission,
                    "latest_run": {
                        "run_id": str(cs.latest_run.run_id) if cs.latest_run.run_id else None,
                        "status": cs.latest_run.status,
                        "worst_severity": cs.latest_run.worst_severity,
                        "checks_total": cs.latest_run.checks_total,
                        "checks_passed": cs.latest_run.checks_passed,
                        "finished_at": (
                            cs.latest_run.finished_at.isoformat()
                            if cs.latest_run.finished_at
                            else None
                        ),
                    },
                }
                for cs in detail.suites
            ],
            # Non-zero ⇒ the summary above covers suites not listed here.
            "restricted_suite_count": detail.restricted_suite_count,
            "lineage": {
                "upstream": [_lineage_node_payload(n) for n in detail.upstream],
                "downstream": [_lineage_node_payload(n) for n in detail.downstream],
                "edges": [
                    {"source": str(e.source), "target": str(e.target), "columns": e.columns}
                    for e in detail.lineage_edges
                ],
                # Empty ⇒ the graph is as complete as DataQ can make it. Non-empty
                # ⇒ absence of an edge is not evidence of absence of a dependency
                # (#828 — a broken poller must never read as "no lineage").
                "qualified_by": qualifiers,
            },
        }


def _lineage_node_payload(node: Any) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "namespace": node.namespace,
        "name": node.name,
        "env": node.env,
        # False ⇒ DataQ knows the table exists but nothing checks it.
        "is_monitored": node.is_monitored,
        "depth": node.depth,
    }


def _incident_payload(incident: Any) -> dict[str, Any]:
    ev: dict[str, Any] = incident.evidence if isinstance(incident.evidence, dict) else {}

    def _layer(key: str) -> dict[str, Any]:
        # A layer is `null` when it could not be built (the check was deleted, no
        # trend yet) — never an empty dict, so the two must not be conflated by a
        # bare `.get(key, {})`.
        value = ev.get(key)
        return value if isinstance(value, dict) else {}

    check = _layer("check")
    asset = _layer("asset")
    failing = _layer("failing_result")
    return {
        "id": str(incident.id),
        "status": incident.status,
        "suite_id": str(incident.suite_id),
        "check_id": str(incident.check_id),
        "check_name": check.get("name"),
        "asset_id": str(incident.asset_id),
        "asset_namespace": asset.get("namespace"),
        "asset_name": asset.get("name"),
        "latest_severity": failing.get("status"),
        "occurrence_count": incident.occurrence_count,
        "created_at": incident.created_at.isoformat(),
        "last_seen_at": incident.last_seen_at.isoformat(),
        "acknowledged_at": (
            incident.acknowledged_at.isoformat() if incident.acknowledged_at else None
        ),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        # 'user' = a person closed it; 'auto' = a later passing result closed it.
        # Null while still open.
        "resolved_by": incident.resolved_by,
        # Non-null ⇒ this (asset, check) pair broke before, was resolved, and
        # broke again. Without it a recurrence of a weekly problem reads as
        # brand new — `created_at` today, `occurrence_count: 1` — which is the
        # opposite of what the user needs to know. `resolve_incident`'s own
        # docstring promised this link and nothing returned it.
        "prior_incident_id": (
            str(incident.prior_incident_id) if incident.prior_incident_id else None
        ),
        "is_recurrence": incident.prior_incident_id is not None,
        # Echoed so a confirmation is data rather than faith: `acknowledge_note`
        # is only overwritten when a note is passed, so a re-ack without one
        # keeps the previous note under a new actor.
        "acknowledge_note": incident.acknowledge_note,
        "resolution_note": incident.resolution_note,
    }


@mcp.tool
def list_incidents(
    status: str | None = None,
    suite_id: str | None = None,
    asset_id: str | None = None,
    since_hours: Annotated[float | None, Field(gt=0)] = None,
    until_hours: Annotated[float | None, Field(ge=0)] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict[str, Any]:
    """List data-quality incidents — what is unresolved *right now*, and since when.

    Use this for "what's broken?", "what's still open?", "has anyone
    acknowledged the orders failure?". An incident is the deduplicated,
    stateful roll-up of repeated failures of one check on one asset: the first
    breach opens it, later breaches raise `occurrence_count` rather than piling
    up new rows, and a passing result (or a person) resolves it.

    **This tool answers "what is unresolved now", not "what failed during
    period X".** Incidents auto-resolve on the first passing result (per-suite
    configurable, default on) — a check that failed at 03:00 and passed at 09:00
    has already auto-resolved, so it will not appear under `status="open"` even
    though it genuinely failed today. For "what failed today/this week", use
    `list_runs` or `get_check_history` instead; a confidently empty page here
    does not mean nothing failed in the window, only that nothing is currently
    unresolved.

    Filter by `status` (`open`, `acknowledged`, `resolved`), `suite_id`, or
    `asset_id`. Default is every status — pass `status="open"` for "what is
    broken right now", since a resolved incident is history, not a live problem.

    `since_hours`/`until_hours` filter on `last_seen_at` (the most recent
    breach, not when the incident first opened) and are relative offsets from
    now ("N hours ago") rather than clock times. An incident opened last week
    that breached again an hour ago still matches `since_hours=1`.

    `occurrence_count` is how many times the check has breached, not how many
    incidents exist; a count of 40 on one incident is one ongoing problem, not
    forty. Prefer `last_seen_at` over `created_at` when asked whether something
    is still happening.

    Scoped to suites the caller can access (a workspace-admin sees all), so an
    empty result means "nothing visible to you", which is not the same as
    "nothing is wrong in the workspace" — unlike `list_assets`, whose health
    numbers are workspace-wide.
    """
    if status is not None and status not in INCIDENT_STATUSES:
        # A typo'd status would otherwise return `[]` — indistinguishable from
        # "nothing is broken", on the one question where that is worst (#828).
        raise ToolError(f"status must be one of {list(INCIDENT_STATUSES)}")
    with _ctx() as (session, user), _service_errors():
        incident_service.validate_read_filters(since_hours=since_hours, until_hours=until_hours)
        sid = _parse_uuid(suite_id, field="suite_id") if suite_id is not None else None
        aid = _parse_uuid(asset_id, field="asset_id") if asset_id is not None else None
        if aid is not None and session.get(Asset, aid) is None:
            # An unknown asset id would otherwise return an empty page, which
            # reads as "nothing is broken on that asset" — the #828 shape the
            # `status` guard above exists to prevent, in the sibling argument.
            # Asset identity is workspace-visible (ADR 0037), so saying an id is
            # unknown leaks nothing; the incidents themselves stay grant-scoped.
            raise ToolError(f"no asset with id {asset_id}")
        if sid is not None:
            # The up-front gate: without it a suite the caller cannot see returns
            # an empty list, which reads as a clean bill of health rather than a
            # denial.
            require_permission(session, sid, user.id, minimum="view")
        include_all = is_workspace_admin(user)
        total = incident_service.count_incidents(
            session,
            user_id=user.id,
            include_all=include_all,
            asset_id=aid,
            suite_id=sid,
            state=status,
            since_hours=since_hours,
            until_hours=until_hours,
        )
        incidents = incident_service.list_incidents(
            session,
            user_id=user.id,
            include_all=include_all,
            asset_id=aid,
            suite_id=sid,
            state=status,
            limit=limit,
            offset=offset,
            since_hours=since_hours,
            until_hours=until_hours,
        )
        return {
            "total": total,
            "returned": len(incidents),
            "truncated": offset + len(incidents) < total,
            **_page_window([i.last_seen_at for i in incidents]),
            "incidents": [_incident_payload(i) for i in incidents],
        }


@mcp.tool
def get_incident(incident_id: str) -> dict[str, Any]:
    """Get one incident with its evidence card — why it opened and what else broke.

    Use this for 'why did the orders freshness incident open?' or 'what else was
    failing at the time?'. Returns the incident's lifecycle state plus the
    `evidence` snapshot captured when it last breached: the failing check and its
    observed value, a kind-shaped detail view of it, the asset, the recent metric
    trend, the sibling checks in the same run, a cross-suite sample of other
    checks on the SAME asset, the upstream pipeline run, and the downstream
    blast radius.

    The evidence card is a **snapshot taken at the last occurrence**, not a live
    read — describe it as "when this last failed", not "right now". It carries no
    failing sample rows by design, so it cannot show which specific records were
    bad; use the run results for that.

    ``kind_detail`` names the fields specific to the failing check's monitor kind
    (`age_hours` for freshness, `deviation_pct` for volume, `added`/`removed`/
    `type_changed` for schema_drift, `z_score`/`insufficient_history` for
    anomaly) instead of making you parse `failing_result.observed_value`'s four
    different JSONB shapes yourself. It is null for an ordinary GX expectation or
    a comparison check — the common case, where `observed_value` already IS the
    shape. For a `freshness`/`volume`/`schema_drift`/`anomaly` check it is
    non-null whenever the card carries one at all: `evidence` is only ever
    captured from a genuinely warned/failed/critical occurrence of that check
    (never an operationally-errored or skipped one), so a null `kind_detail`
    there means the same rare thing a null `check_name` below does, not that
    the check ran cleanly.

    ``same_asset_siblings`` is the cross-suite signal `sibling_checks` cannot
    see: the latest result of every other check that targets this SAME asset, in
    ANY suite, from the last 7 days — e.g. a volume check on `orders` in a
    different suite dropping 40% right before this freshness check breached.
    Only a sibling's **cleanly-completed** (`succeeded`) run counts; a sibling
    check whose own run failed operationally (e.g. a dead credential on that
    OTHER suite) is silently absent rather than shown as failing — itself a
    plausible root cause this card gives you no way to see. The list is also
    capped at the 20 most recently-updated checks, so on an asset shared by many
    suites it is a recent sample, not a complete inventory — the same "floor,
    not complete" caveat `downstream_blast_radius` carries below. Entries whose
    suite you cannot view are withheld and folded into
    `same_asset_siblings_restricted_count` instead of being named.
    `same_asset_siblings_restricted_count` is present (`0` or higher) whenever
    `same_asset_siblings` itself is present — a `0` means nothing was withheld,
    not that the field is missing — so don't report "no other checks touch this
    asset" from an empty list without also checking that count is `0`.

    ``downstream_blast_radius`` is ``[]`` for three reasons that look
    identical: the failing asset was never resolved, the asset is a genuine
    lineage leaf, or **this workspace has no lineage recorded at all**. It is
    also depth-capped, so a non-empty list is a floor rather than a complete
    inventory. Never report "nothing downstream is affected" from an empty
    radius — confirm lineage exists with ``get_asset`` first.

    ``check_name``, ``asset_name`` and ``latest_severity`` are read from the same
    snapshot, so a check renamed since the last occurrence still reports its old
    name, and a null there means the layer could not be built (usually a deleted
    check) — not that the check is passing. The lifecycle fields (``status``,
    ``occurrence_count``, ``last_seen_at``, the ack/resolve stamps) are live.

    A `null` layer inside `evidence` does **not** by itself mean something went
    wrong, and the distinction matters because each layer means a different thing
    by it:

    - `upstream_pipeline_run` is null for every manually-triggered or scheduled
      run — most runs. It means "no orchestration pipeline triggered this", which
      is normal, not a missing pipeline or a DataQ failure.
    - `kind_detail` null is benign for an `expectation`/`comparison` check, but
      NOT for a `freshness`/`volume`/`schema_drift`/`anomaly` one — every card
      is captured from a genuinely warned/failed/critical occurrence of its
      check (an operational error or skip never reaches this card), so there a
      null means the same rare thing a null `check_name` above means.
    - `metric_trend`, `sibling_checks` and `same_asset_siblings` are `[]` when
      there is nothing to show, so a null there really is a layer that could not
      be built.
    - `profile_diff` is always null — not implemented, not a failed attempt.

    Requires view access to the incident's suite; an incident on a suite the
    caller cannot see is indistinguishable from one that does not exist.
    """
    iid = _parse_uuid(incident_id, field="incident_id")
    with _ctx() as (session, user), _service_errors():
        incident = incident_service.load_visible_incident(
            session, iid, user_id=user.id, for_action=False
        )
        return {
            **_incident_payload(incident),
            "evidence": incident_service.evidence_for_caller(session, incident, user_id=user.id),
        }


#: Cap on an ack/resolve note, matching the REST `IncidentActionRequest`. The
#: column is unbounded Text, so without this an LLM-generated note is unbounded
#: too — the same boundary the #567/#1421 class of findings kept surfacing.
_NOTE_MAX_LEN = 2000


@mcp.tool
def ack_incident(
    incident_id: str,
    note: Annotated[str, Field(max_length=_NOTE_MAX_LEN)] | None = None,
) -> dict[str, Any]:
    """Acknowledge an incident — record that someone is looking at it.

    Use this for 'acknowledge that', 'I'm on it', or 'mark the orders freshness
    incident as being investigated'. An optional ``note`` records why or who.

    **Acknowledging changes nothing about the data and does not stop alerts.**
    The check still runs, still fails, and still fires notifications on its own
    schedule; this only moves the incident from `open` to `acknowledged` so the
    workspace can see it is owned. To stop the alerting itself, use
    ``snooze_check``; to declare the problem over, use ``resolve_incident``.

    Acknowledging an already-acknowledged incident is fine — it records the newer
    actor and note. Acknowledging a **resolved** one is refused: a resolved
    incident is closed for good, and a later breach of the same check opens a new
    incident rather than reopening this one.

    Requires edit access to the incident's suite.
    """
    iid = _parse_uuid(incident_id, field="incident_id")
    if contains_nul({"note": note or ""}):
        raise ToolError("NUL (\\x00) characters are not allowed in a note")
    with _ctx() as (session, user), _service_errors():
        incident = incident_service.load_visible_incident(
            session, iid, user_id=user.id, for_action=True
        )
        incident = incident_service.acknowledge_incident(
            session, incident, user_id=user.id, note=note
        )
        return _incident_payload(incident)


@mcp.tool
def resolve_incident(
    incident_id: str,
    note: Annotated[str, Field(max_length=_NOTE_MAX_LEN)] | None = None,
) -> dict[str, Any]:
    """Resolve an incident — declare the problem over.

    Use this for 'resolve that', 'the orders backfill fixed it', or 'close the
    freshness incident'. An optional ``note`` records the resolution.

    **This is a statement about the incident, not a fix to the data.** Resolving
    does not re-run anything and does not make the check pass; if the underlying
    problem is still there, the very next failing run opens a **new** incident
    (linked back to this one), because a resolved incident is never reopened.
    Prefer ``trigger_suite_run`` to confirm the fix before resolving, and say so
    rather than resolving on the user's assumption that something is fixed.

    A double-resolve is refused. Incidents also auto-resolve on the first passing
    result unless the suite has that turned off, so an incident may already be
    closed without anyone acting.

    Requires edit access to the incident's suite.
    """
    iid = _parse_uuid(incident_id, field="incident_id")
    if contains_nul({"note": note or ""}):
        raise ToolError("NUL (\\x00) characters are not allowed in a note")
    with _ctx() as (session, user), _service_errors():
        incident = incident_service.load_visible_incident(
            session, iid, user_id=user.id, for_action=True
        )
        incident = incident_service.resolve_incident(session, incident, user_id=user.id, note=note)
        return _incident_payload(incident)


@mcp.tool
def get_near_misses(suite_id: str | None = None) -> dict[str, Any]:
    """Find orchestration triggers that are silently never firing.

    Use this when a user says 'the suite was supposed to run after the pipeline
    and it did not', or to investigate the warning ``create_trigger_binding``
    returns. Each row is a real, observed event: a pipeline/DAG run **did**
    succeed in ``run_env``, no binding was scoped to that env, and an enabled
    binding for the same pipeline exists in ``binding_env`` — so that run
    triggered nothing, and the two environments disagree.

    This is the diagnosis for the failure mode a binding cannot report about
    itself: it exists, it looks correct in ``list_trigger_bindings``, and nothing
    fires. Two limits to state rather than paper over:

    - A row means **those runs** triggered nothing, not that the binding is
      permanently dead. A near-miss is recorded per run and only when that run's
      env matched no binding, so a pipeline id that *also* runs in
      ``binding_env`` is firing correctly there. Confirm with ``list_runs``
      before telling a user a trigger has never worked.
    - An empty result does **not** prove a binding is firing. Only mismatches
      observed within the deployment's near-miss window are reported, and that
      window is returned as ``window_hours`` (48 by default, but configurable —
      quote the returned value, never a default). A mismatch older than it ages
      out entirely, so a weekly DAG's may never appear; the pipeline may also
      simply not have run, or the binding may be disabled (check ``enabled`` in
      ``list_trigger_bindings``).

    Optionally narrow to one ``suite_id``. Scoped to suites the caller can
    access, since a near-miss is derived from suite-owned binding config.
    """
    with _ctx() as (session, user), _service_errors():
        sid = _parse_uuid(suite_id, field="suite_id") if suite_id is not None else None
        if sid is not None:
            require_permission(session, sid, user.id, minimum="view")
        rows = orchestration_service.list_env_near_misses(
            session, user_id=user.id, include_all=is_workspace_admin(user), suite_id=sid
        )
        return {
            # The window is `trigger_env_near_miss_recent_hours` and is
            # deployment-configurable, so "roughly two days" was wrong on any
            # workspace that changed it. Returned so the model states the real
            # bound rather than a default it cannot see.
            "window_hours": get_settings().trigger_env_near_miss_recent_hours,
            "near_misses": [
                {
                    "provider": r.provider,
                    "pipeline_or_dag_id": r.pipeline_or_dag_id,
                    # Where the pipeline actually succeeded...
                    "run_env": r.run_env,
                    # ...and where the binding is looking. These differing IS the bug.
                    "binding_env": r.binding_env,
                    "last_observed_at": r.updated_at.isoformat(),
                }
                for r in rows
            ],
        }


@mcp.tool
def get_doc(
    page: Annotated[str, Field(json_schema_extra={"enum": docs_catalog.list_pages()})],
) -> dict[str, Any]:
    """Read a published DataQ user-facing doc page, verbatim.

    Use this for questions about how DataQ works or what it supports, e.g.
    'what are best practices for authoring a check?' or 'what compliance
    mechanisms does DataQ have?' — answer from the returned ``content``, not
    from general knowledge, since these pages are the maintained source of
    truth. Content is returned exactly as published, with **no summarization**;
    a long page may need several turns to read in full if you need everything
    in it.

    ``page`` is one of a small curated set — the enum below is the complete,
    current list; there is no broader catalog to page through. It deliberately
    excludes architecture/ADR docs (contributor-facing design rationale, not
    what this question is usually about) and every other unpublished internal
    doc — this tool has no path to anything outside the pages listed. An
    unrecognized ``page`` raises an error restating the current valid list, so
    retry from that rather than guessing a slug.

    Not scoped to any suite: these pages are public and workspace-agnostic, so
    every authenticated caller sees the same content.
    """
    with _ctx():
        try:
            content = docs_catalog.read_page(page)
        except docs_catalog.DocNotFoundError as exc:
            raise ToolError(str(exc)) from exc
        return {"page": page, "content": content}


@mcp.tool
def list_columns(
    suite_id: str,
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
) -> dict[str, Any]:
    """List the column names of a suite's table or file.

    Use this **before authoring a check** — it is the cheap way to get exact
    column names, and far cheaper than ``profile_column``, which reads data to
    compute statistics. Guessing a column name produces a check that runs and
    errors, so read the names rather than inferring them from a table name.

    ``table`` (+ optional ``schema``/``catalog``) or ``path`` (+ ``file_format``)
    default to the suite's own run target, so they only need passing to inspect
    something *other* than what the suite runs against. ``namespace`` is only
    meaningful for an Iceberg table alongside an explicit ``table`` (Iceberg
    addresses ``namespace.table``); with no explicit ``table``/``path`` the
    suite target supplies its own namespace, so passing one there is ignored.

    Returns names only — no types, no data, no statistics. It cannot tell you
    whether a column is nullable or what it contains; use ``profile_column`` for
    that.

    Requires **edit** access to the suite, not view: this opens a live connection
    to the datasource using the stored credential, the same gate the profiler and
    dry-run carry for the same reason.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        connection = session.get(Connection, suite.connection_id)
        if connection is None:
            raise ToolError("suite has no connection")
        if table is None and path is None:
            # Same defaulting as `profile_column` — and the same reason it clears
            # `namespace`: the resolver already folds the target's namespace into
            # `table` for Iceberg, so passing it again would double it.
            table, schema, catalog, path, file_format = _profile_target_defaults(
                suite, connection, schema=schema, catalog=catalog, file_format=file_format
            )
            namespace = None
        columns = profile_service.list_columns(
            connection,
            table=table,
            schema=schema,
            catalog=catalog,
            namespace=namespace,
            path=path,
            file_format=file_format,
            secret_store=get_secret_store(),
        )
        # Audited, not masked. This probe opens the customer's warehouse with a
        # stored credential, so "who touched this table" must be answerable — but
        # it returns column NAMES, which are schema rather than data. Hence
        # `values_in_scope=False` rather than `masked=True`: there was nothing to
        # redact, and claiming a redaction that never happened is the same class
        # of dishonest field this whole seam exists to remove. `exposed` is False
        # because nothing was disclosed, not because it was hidden.
        live_probe.record_probe_access(
            session,
            action="column.list",
            suite_id=suite.id,
            actor=user,
            destination=live_probe.Destination.EGRESS,
            masked=False,
            values_in_scope=False,
            columns=columns,
            detail={"table": table, "path": path},
        )
        return {
            # The fully-qualified object actually read. These may have been
            # defaulted off the suite's run target, and "ORDERS" on its own is
            # ambiguous across schemas and catalogs.
            "table": table,
            "schema": schema,
            "catalog": catalog,
            "namespace": namespace,
            "path": path,
            "file_format": file_format,
            "columns": columns,
        }


def _parse_suite_target(target: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a raw target dict through the REST route's own `SuiteTarget` model.

    Imported at call time to keep the API layer out of this module's import graph
    (it already borrows `contains_nul` from `api.v1._base` the same way).

    Worth routing through rather than trusting the service: `suite_service`
    validates the target's *field combination* per connection type, but
    `SuiteTarget` is what validates `file_format` against `csv|parquet`, caps
    every string, and rejects unknown keys.
    """
    if target is None:
        return None
    from pydantic import ValidationError

    from backend.app.api.v1.suites import SuiteTarget

    try:
        return SuiteTarget.model_validate(target).to_storage()
    except ValidationError as exc:
        raise ToolError(f"invalid run target: {exc.errors(include_url=False)}") from exc


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
    your own ``table``. **Snowflake and Unity Catalog are profiled in full; ADLS, S3 and Iceberg
    targets are profiled over a sample of at most 100,000 rows.** When
    ``sampled`` is true, ``row_count`` is the number of rows **sampled** — not
    the size of the file or table — and every statistic describes only that
    sample. Say so rather than reporting a sample fraction as a fact about the
    dataset.

    A null ``min_value`` / ``max_value`` / ``distinct_count`` means the statistic
    is unavailable, not that the column is empty: the column may be entirely
    null, or the stat may not be computable for its type (mixed uncomparable
    values, nested list/struct cells).

    **Values are masked for columns the suite's redaction policy considers
    sensitive**, and ``redacted_columns`` names them. Statistics survive masking —
    ``null_count``, ``null_fraction``, ``distinct_count`` and each ``top_values``
    ``count`` are facts *about* the data, not the data — so "how complete is this
    column, how skewed is it" is answerable even when the literal values are not
    shown. A masked column's ``min_value`` / ``max_value`` are null because they
    were withheld, NOT because the column is empty; check ``redacted_columns``
    before reporting a column as having no values.

    This docstring previously said the values were unredacted and asked you not
    to profile PII columns. That was an instruction, not a control, and the
    control now exists. Requires edit access to the suite, and the read is
    recorded in the workspace audit log.
    """
    sid = _parse_uuid(suite_id, field="suite_id")
    with _ctx() as (session, user), _service_errors():
        suite = require_permission(session, sid, user.id, minimum="edit")
        connection = session.get(Connection, suite.connection_id)
        if connection is None:
            raise ToolError("suite has no connection")
        # BEFORE `_profile_target_defaults` runs: it overwrites these locals with
        # the suite's own target, so reading them afterwards always looks like an
        # explicit override — which silently dropped the tag floor on every call.
        probed_other = any(v is not None for v in (table, path, schema, catalog))
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
        # Only the SQL path (Snowflake / Unity Catalog) aggregates the whole
        # table; ADLS / S3 / Iceberg read at most `_SAMPLE_ROWS` rows into pandas
        # and compute locally. `row_count` is then the SAMPLE size, not the
        # table's — "how many rows are in the orders file?" answered `100000`
        # exactly, and every null fraction was a sample fraction stated as fact.
        sampled = result.path is not None or connection.type == "iceberg"
        # An MCP consumer is `EGRESS` (#1419/#1479): `top_values` / `min_value` /
        # `max_value` are real cell contents, and a model may quote them onward.
        # Until now the ONLY control here was a sentence in this docstring telling
        # the model not to profile PII columns — an instruction, not a control,
        # and the same "documented and enforced nowhere" shape as the ADR 0033
        # `*_secret_name` hole. Statistics survive masking; only literal values go.
        #
        # Tags apply only when the probe hit the suite's OWN asset: an explicit
        # table/path override may name a different table whose columns collide by
        # name, and the asset's tags could hand out a clearance belonging to it.
        tags = live_probe.applicable_tags(
            _asset_column_tags(session, suite), probed_other_target=probed_other
        )
        sensitive = live_probe.sensitive_profile_columns(
            result.columns,
            policy=suite.column_policy,
            tags=tags,
            destination=live_probe.Destination.EGRESS,
        )
        # NOT `columns` — that name is this tool's own parameter (the list of
        # column NAMES to profile). Shadowing it silently changed what was
        # profiled; mypy caught it, which a looser type would not have.
        shown_columns = live_probe.mask_profile_columns(result.columns, sensitive=sensitive)
        live_probe.record_probe_access(
            session,
            action="column.profile",
            suite_id=suite.id,
            actor=user,
            destination=live_probe.Destination.EGRESS,
            masked=True,
            columns=[c.column for c in result.columns],
            sensitive_columns=sensitive,
            detail={"table": result.table, "path": result.path, "sampled": sampled},
        )
        return {
            "row_count": result.row_count,
            "table": result.table,
            "path": result.path,
            # Which qualified object was actually read — the tool may have
            # defaulted these off the suite's target, and "ORDERS" alone is
            # ambiguous across schemas and catalogs.
            "schema": result.schema,
            "catalog": result.catalog,
            "file_format": result.file_format,
            "sampled": sampled,
            "sample_row_limit": profile_service.SAMPLE_ROWS if sampled else None,
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
                # `shown_columns`, NOT `result.columns` — iterating the raw list
                # is what would leave the masking above inert while reading as
                # correct.
                for c in shown_columns
            ],
            #: Which columns came back masked, so a model can say "the policy
            #: hides these" instead of reporting them as empty. A null min/max on
            #: a masked column means masked, not absent.
            "redacted_columns": sensitive,
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
