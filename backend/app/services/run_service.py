"""GX-agnostic run core: drive the `Run` lifecycle via an injected `CheckRunner`."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import delete, func, null, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.core.timeutil import as_utc
from backend.app.datasources.base import (
    SAMPLE_ROW_CAP,
    VALUE_SIGNAL_SUMMARY_KEY,
    CheckOutcome,
    CheckRunner,
    CheckSpec,
    MonitorRunner,
    MonitorSpec,
)
from backend.app.datasources.monitors import (
    MONITOR_KINDS,
    SCALAR_MONITOR_KINDS,
    STATEFUL_MONITOR_KINDS,
)
from backend.app.datasources.sql import strip_statement_echo
from backend.app.db.chunked_dml import CHUNK_SIZE, chunked_dml
from backend.app.db.models import (
    CHECK_ORDER,
    COMPARISON_KIND,
    GX_ENGINE,
    RESULT_OPERATIONAL_STATUSES,
    RESULT_STATUSES,
    RUN_STATUSES,
    SEVERITY_RANK,
    Asset,
    Check,
    CheckVersion,
    Result,
    Run,
    worst_severity,
)
from backend.app.services import run_dispatch, suite_service
from backend.app.services.column_classification import ColumnClass, classify_column, is_sensitive
from backend.app.services.failure_classifier import safe_failure_reason
from backend.app.services.rollup import status_histograms
from backend.app.services.severity import resolve_status

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _build_result(run_id: uuid.UUID, check: Check, outcome: CheckOutcome) -> Result:
    """Map a check + its GX outcome to a `Result`, deriving the severity tier
    (ADR 0005/0016; the scalar persists as `metric_value`, ADR 0012).
    """
    status, metric = resolve_status(
        outcome,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
    )
    # Zero-sample privacy mode (#1676): a deployment-level switch, so this is the ONE choke
    # point every check kind funnels through — gate it here and every reader (results API,
    # alerts, MCP) inherits the suppression from what's actually persisted.
    zero_sample = get_settings().privacy_zero_sample_mode
    if outcome.errored:
        # One funnel to `observed_value` for every kind, so the #1203 strip can't diverge.
        error_message = strip_statement_echo(outcome.error_message)
        observed = {"error": error_message} if error_message else None
        # The provoking cell (#989) rides separately so the read layer can redact it — under
        # zero-sample mode it never leaves the runner at all, not even redacted.
        if (
            not zero_sample
            and outcome.observed_value
            and "unparsed_value" in outcome.observed_value
        ):
            observed = {**(observed or {}), **sanitize_json(outcome.observed_value)}
        sample = None
    else:
        observed = sanitize_json(outcome.observed_value)
        sample = None if zero_sample else sanitize_json(outcome.sample_failures)
    return Result(
        run_id=run_id,
        check_id=check.id,
        status=status,
        metric_value=metric,
        observed_value=observed,
        expected_value=sanitize_json(outcome.expected_value),
        sample_failures=sample,
        # Persisted on EVERY status, incl. `error` (#595).
        sampling=sanitize_json(outcome.sampling),
    )


_EXPECTATION_KIND = "expectation"


@dataclass(frozen=True)
class OutcomePhase:
    """One unit of execution: ``(check index, outcome)`` pairs plus whether the
    phase's rows may be committed as soon as built (#318) — see
    `_run_outcome_phases` for the publishability rule.
    """

    resolved: list[tuple[int, CheckOutcome]]
    publishable: bool


def _run_outcome_phases(
    runner: CheckRunner,
    *,
    table: str,
    schema: str | None,
    checks: list[Check],
    index_columns: list[str] | None = None,
    comparison_executor: Callable[[Check], CheckOutcome] | None = None,
    stateful_monitor_executor: Callable[[Check], CheckOutcome] | None = None,
) -> Iterator[OutcomePhase]:
    """Run a suite's checks, dispatched by `check.kind` (ADR 0012), yielding each
    unit of execution as it resolves.
    """

    # Engine partition first (ADR 0036): native checks route via `run_native_check` when
    # advertised, else a per-check `error` — never a silent skip or a sibling-killing raise.
    def _engine(c: Check) -> str:
        return c.engine or GX_ENGINE

    advertised = frozenset(getattr(runner, "supported_native_engines", frozenset()))
    native_run = getattr(runner, "run_native_check", None)
    for i, c in enumerate(checks):
        engine = _engine(c)
        if engine == GX_ENGINE:
            continue
        if engine in advertised and callable(native_run):
            outcome = cast(
                CheckOutcome,
                native_run(
                    kind=c.kind,
                    expectation_type=c.expectation_type,
                    config=dict(c.config),
                    table=table,
                    schema=schema,
                ),
            )
        else:
            outcome = _executor_outcome(
                None,
                c,
                missing=(
                    f"engine '{engine}' is not available on this "
                    "connection — the check was authored for a "
                    "platform-native engine this deployment cannot run "
                    "(ADR 0036); re-check the connection's engine "
                    "capabilities or re-point the check to 'gx'"
                ),
            )
        yield OutcomePhase(resolved=[(i, outcome)], publishable=True)
    checks_gx = [(i, c) for i, c in enumerate(checks) if _engine(c) == GX_ENGINE]
    expectation_idx = [i for i, c in checks_gx if c.kind == _EXPECTATION_KIND]
    monitor_idx = [i for i, c in checks_gx if c.kind in SCALAR_MONITOR_KINDS]
    stateful_idx = [i for i, c in checks_gx if c.kind in STATEFUL_MONITOR_KINDS]
    comparison_idx = [i for i, c in checks_gx if c.kind == COMPARISON_KIND]
    handled = {_EXPECTATION_KIND, *MONITOR_KINDS, COMPARISON_KIND}
    # GX partition only: native-engine checks were already resolved above.
    unsupported = sorted({c.kind for _, c in checks_gx if c.kind not in handled})
    if unsupported:
        raise NotImplementedError(f"no run path for check kind(s) {', '.join(unsupported)}")

    for i in comparison_idx:
        yield OutcomePhase(
            resolved=[
                (
                    i,
                    _executor_outcome(
                        comparison_executor,
                        checks[i],
                        missing=(
                            "comparison checks need the comparison run path (no executor "
                            "supplied on this caller — ADR 0015)"
                        ),
                    ),
                )
            ],
            publishable=True,
        )
    if expectation_idx:
        specs = [
            CheckSpec(expectation_type=checks[i].expectation_type, kwargs=dict(checks[i].config))
            for i in expectation_idx
        ]
        suite_outcome = runner.run_checks(
            table=table, schema=schema, checks=specs, index_columns=index_columns
        )
        # One atomic GX batch; `strict=True` keeps a wrong-arity runner loud.
        yield OutcomePhase(
            resolved=list(zip(expectation_idx, suite_outcome.checks, strict=True)),
            publishable=True,
        )
    if monitor_idx:
        # Never `isinstance(runner, MonitorRunner)` (#429): a runtime_checkable
        # Protocol matches on method NAME alone and would TypeError at the call.
        supported = frozenset(getattr(runner, "supported_monitor_kinds", frozenset()))
        unsupported_kinds = sorted({checks[i].kind for i in monitor_idx} - supported)
        if unsupported_kinds:
            raise NotImplementedError(
                f"{type(runner).__name__} does not support monitor kind(s) "
                f"{', '.join(unsupported_kinds)} — these need a monitor-capable "
                "datasource (Snowflake / Unity Catalog / Iceberg / ADLS Gen2 / S3)"
            )
        if not callable(getattr(runner, "run_monitors", None)):
            raise NotImplementedError(
                f"{type(runner).__name__} advertises monitor kinds but implements "
                "no run_monitors — runner capability and implementation drifted"
            )
        monitor_runner = cast(MonitorRunner, runner)
        monitors = [
            MonitorSpec(kind=checks[i].kind, config=dict(checks[i].config)) for i in monitor_idx
        ]
        monitor_outcomes = monitor_runner.run_monitors(
            table=table, schema=schema, monitors=monitors
        )
        yield OutcomePhase(
            resolved=list(zip(monitor_idx, monitor_outcomes, strict=True)),
            publishable=True,
        )
    # LAST and unpublishable: baseline writes must ride the terminal commit (#318).
    for i in stateful_idx:
        yield OutcomePhase(
            resolved=[
                (
                    i,
                    _executor_outcome(
                        stateful_monitor_executor,
                        checks[i],
                        missing=(
                            "stateful monitor kinds need the baseline-diff run path (no "
                            "executor supplied on this caller — #592)"
                        ),
                    ),
                )
            ],
            publishable=False,
        )


def _executor_outcome(
    executor: Callable[[Check], CheckOutcome] | None,
    check: Check,
    *,
    missing: str,
) -> CheckOutcome:
    """Run an injected per-check executor, or report its absence as an operational
    ``error`` (#122) — never a raise, so siblings still run.
    """
    if executor is None:
        return CheckOutcome(
            expectation_type=check.expectation_type,
            success=False,
            errored=True,
            error_message=missing,
        )
    return executor(check)


def _cancelled_mid_run(session: Session, run: Run) -> bool:
    """Did a cancel commit (from the API session) while this run was executing?"""
    if session.scalar(select(Run.status).where(Run.id == run.id)) != "cancelled":
        return False
    session.refresh(run)
    return True


def discard_run_results(session: Session, run_id: uuid.UUID) -> None:
    """Drop every result row a run wrote — staged AND committed (#318)."""
    session.rollback()
    try:
        session.execute(delete(Result).where(Result.run_id == run_id))
        session.commit()
    except Exception:
        session.rollback()
        log.exception("run_partial_results_discard_failed", run_id=str(run_id))


def execute_run(
    session: Session,
    *,
    run: Run,
    checks: list[Check],
    runner: CheckRunner,
    table: str,
    schema: str | None = None,
    index_columns: list[str] | None = None,
    comparison_executor: Callable[[Check], CheckOutcome] | None = None,
    stateful_monitor_executor: Callable[[Check], CheckOutcome] | None = None,
) -> Run:
    """Run ``checks`` against ``table`` via ``runner`` and persist the outcome."""
    run.status = "running"
    run.started_at = _now()
    session.commit()
    log.info(
        "run_started",
        run_id=str(run.id),
        suite_id=str(run.suite_id),
        n_checks=len(checks),
        table=table,
    )

    # Any failure drives the run terminal-'failed', never stuck 'running'; only
    # scalars cross phases — retaining CheckOutcomes is the #595 memory hazard.
    suite_success = True
    n_results = 0
    published = False
    try:
        for phase in _run_outcome_phases(
            runner,
            table=table,
            schema=schema,
            checks=checks,
            index_columns=index_columns,
            comparison_executor=comparison_executor,
            stateful_monitor_executor=stateful_monitor_executor,
        ):
            rows = [
                _build_result(run.id, checks[i], check_outcome)
                for i, check_outcome in phase.resolved
            ]
            session.add_all(rows)
            suite_success = suite_success and all(oc.success for _, oc in phase.resolved)
            n_results += len(rows)
            # Cooperative cancellation: a committed cancel must not be overwritten.
            if _cancelled_mid_run(session, run):
                _discard_results_if_any(session, run, published=published)
                log.info("run_cancelled_during_execution", run_id=str(run.id))
                return run
            if not phase.publishable:
                continue
            session.commit()
            published = True
        if _cancelled_mid_run(session, run):
            _discard_results_if_any(session, run, published=published)
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        if not _mark_succeeded(session, run):
            # Lost the race: the confirmed cancel stays authoritative (#318 G4).
            _discard_results_if_any(session, run, published=published)
            session.refresh(run)
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
    except Exception as exc:
        _discard_results_if_any(session, run, published=published)
        # A cancelled run that also errored stays 'cancelled'.
        if _cancelled_mid_run(session, run):
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        run.status = "failed"
        run.finished_at = _now()
        # Redaction-safe reason (#605/#595): raw text can carry DSN/credential/cell
        # fragments — those stay in the server log, never the persisted reason.
        run.failure_reason = safe_failure_reason(exc)
        session.commit()
        log.exception("run_failed", run_id=str(run.id), table=table)
        return run

    log.info(
        "run_completed",
        run_id=str(run.id),
        suite_success=suite_success,
        n_results=n_results,
    )
    return run


def _mark_succeeded(session: Session, run: Run) -> bool:
    """Flip a still-``running`` run to ``succeeded``; return whether it won (#318 G4)."""
    finished = _now()
    result = session.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == "running")
        .values(status="succeeded", finished_at=finished, failure_reason=None)
    )
    won = cast("CursorResult[Any]", result).rowcount == 1
    session.commit()
    if not won:
        return False
    # Mirror onto the ORM object: the Core UPDATE bypassed it and callers read it.
    run.status = "succeeded"
    run.finished_at = finished
    run.failure_reason = None
    return True


def _discard_results_if_any(session: Session, run: Run, *, published: bool) -> None:
    """`discard_run_results`, skipped when no phase ever committed."""
    if published:
        discard_run_results(session, run.id)
    else:
        session.rollback()


def skip_run(session: Session, *, run: Run, checks: list[Check], reason: str) -> Run:
    """Record a run that had nothing to evaluate — every check `skip`ped (#122)."""
    run.status = "running"
    run.started_at = _now()
    session.commit()
    rows = [
        Result(run_id=run.id, check_id=check.id, status="skip", observed_value={"reason": reason})
        for check in checks
    ]
    session.add_all(rows)
    run.status = "succeeded"
    run.finished_at = _now()
    session.commit()
    log.info("run_skipped", run_id=str(run.id), reason=reason, n_checks=len(checks))
    return run


# ── read model ── every list query filters by accessible suites: the API layer's
# `require_permission` gates single lookups, this is the defence-in-depth filter.


class RunFilterInvalidError(DataQError):
    status_code = 422
    code = "run_filter_invalid"


def validate_read_filters(
    status: str | None = None,
    *,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> None:
    """422 on a `/runs` filter value outside its closed vocabulary or shape."""
    if status is not None and status not in RUN_STATUSES:
        raise RunFilterInvalidError(
            f"invalid run status {status!r}", detail={"allowed": list(RUN_STATUSES)}
        )
    if since_hours is not None and since_hours <= 0:
        raise RunFilterInvalidError(f"since_hours must be positive, got {since_hours!r}")
    if until_hours is not None and until_hours < 0:
        raise RunFilterInvalidError(f"until_hours must not be negative, got {until_hours!r}")
    if since_hours is not None and until_hours is not None and until_hours >= since_hours:
        # since_hours/until_hours are both "N hours ago" offsets from now, so the
        # window is [now - since_hours, now - until_hours] — until must be the
        # MORE RECENT bound, i.e. the smaller offset.
        raise RunFilterInvalidError(
            f"until_hours ({until_hours!r}) must be less than since_hours ({since_hours!r})"
        )


def _run_filters(
    *,
    user_id: uuid.UUID,
    suite_id: uuid.UUID | None,
    status: str | None,
    include_all: bool,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> list[Any]:
    """The ONE `WHERE` chain shared by :func:`list_runs` and :func:`count_runs`."""
    conditions: list[Any] = [
        Run.suite_id.in_(suite_service.accessible_suite_ids(user_id, include_all=include_all))
    ]
    if suite_id is not None:
        conditions.append(Run.suite_id == suite_id)
    if status is not None:
        conditions.append(Run.status == status)
    if since_hours is not None:
        conditions.append(Run.created_at >= _now() - timedelta(hours=since_hours))
    if until_hours is not None:
        conditions.append(Run.created_at <= _now() - timedelta(hours=until_hours))
    return conditions


def list_runs(
    session: Session,
    *,
    user_id: uuid.UUID,
    suite_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_all: bool = False,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> list[Run]:
    """Runs for suites the user can access, newest first (`created_at` desc,
    `id` desc tie-break — the same total-order paging shape `/pipeline_runs`
    and `/incidents` use, since `created_at` alone ties within one transaction).

    ``since_hours``/``until_hours`` are relative offsets from now ("N hours
    ago"), not absolute timestamps — a caller (LLM or otherwise) states a
    window in terms it already has ("today" -> ``since_hours=24``) without
    needing to know the server's current time.
    """
    stmt = (
        select(Run)
        .where(
            *_run_filters(
                user_id=user_id,
                suite_id=suite_id,
                status=status,
                include_all=include_all,
                since_hours=since_hours,
                until_hours=until_hours,
            )
        )
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt))


def count_runs(
    session: Session,
    *,
    user_id: uuid.UUID,
    suite_id: uuid.UUID | None = None,
    status: str | None = None,
    include_all: bool = False,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> int:
    """Total runs matching the SAME visibility + filters as :func:`list_runs`,
    unaffected by its `limit`/`offset` (#1108 — the `/assets` `X-Total-Count`
    shape: `/runs` had `limit` only and could not be paged at all). Shares
    :func:`_run_filters` with the list so the two cannot drift.
    """
    stmt = (
        select(func.count())
        .select_from(Run)
        .where(
            *_run_filters(
                user_id=user_id,
                suite_id=suite_id,
                status=status,
                include_all=include_all,
                since_hours=since_hours,
                until_hours=until_hours,
            )
        )
    )
    return session.scalar(stmt) or 0


def check_outcome_counts(
    session: Session,
    run_ids: Sequence[uuid.UUID],
    *,
    complete_runs_only: bool = False,
) -> dict[uuid.UUID, tuple[int, int, str | None]]:
    """Per-run ``(checks_total, checks_passed, worst_severity)`` for a set of runs,
    in a single grouped query (no N+1). ``worst_severity`` is the highest of
    warn/fail/critical present, else ``None`` (all passed / only operational).
    """
    out: dict[uuid.UUID, tuple[int, int, str | None]] = {}
    for run_id, by_status in status_histograms(
        session, run_ids, complete_runs_only=complete_runs_only
    ).items():
        passed = by_status.get("pass", 0)
        worst = worst_severity(by_status)
        total = passed + sum(by_status.get(tier, 0) for tier in SEVERITY_RANK)
        out[run_id] = (total, passed, worst)
    return out


def operational_result_flags(
    session: Session, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[bool, bool]]:
    """Per-run ``(has_error, has_skip)`` over its **operational** results (#122)."""
    if not run_ids:
        return {}
    rows = session.execute(
        select(Result.run_id, Result.status)
        .where(Result.run_id.in_(run_ids), Result.status.in_(RESULT_OPERATIONAL_STATUSES))
        .group_by(Result.run_id, Result.status)
    ).all()
    flags: dict[uuid.UUID, tuple[bool, bool]] = {}
    for run_id, status in rows:
        has_error, has_skip = flags.get(run_id, (False, False))
        flags[run_id] = (has_error or status == "error", has_skip or status == "skip")
    return flags


def get_run(session: Session, run_id: uuid.UUID) -> Run | None:
    """Fetch a run by id (no authz — the API layer gates on the run's suite)."""
    return session.get(Run, run_id)


_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
# Single-sourced complement so a new status can't escape the reaper's net (#309).
_NON_TERMINAL_STATUSES = frozenset(RUN_STATUSES) - _TERMINAL_STATUSES


def cancel_run(session: Session, run: Run) -> bool:
    """Transition a non-terminal run to ``cancelled``; return whether it changed."""
    if run.status in _TERMINAL_STATUSES:
        return False
    run.status = "cancelled"
    run.finished_at = _now()
    session.commit()
    log.info("run_cancelled", run_id=str(run.id))
    return True


def list_results(session: Session, run_id: uuid.UUID) -> list[Result]:
    """The result rows for a run, ordered by the **check**, not by the result."""
    return list(
        session.scalars(
            select(Result)
            .outerjoin(Check, Check.id == Result.check_id)
            .where(Result.run_id == run_id)
            .order_by(*CHECK_ORDER)
        )
    )


# ── run progress (A1: the poll surface for the live-progress UI) ──────────────


@dataclass(frozen=True)
class CheckProgress:
    """One check's progress within a run. ``status`` is ``None`` when the check has
    no result row — *pending* while the run is active, or *not recorded* for a
    terminal run (a ``failed`` run rolls back and writes no results, so consumers
    must read this together with the run's lifecycle ``status``, not in isolation).
    """

    check_id: uuid.UUID
    name: str
    status: str | None


@dataclass(frozen=True)
class RunProgress:
    """A run's live progress: lifecycle status + per-check resolution + a status
    histogram + how long it has been going, the compact shape the live-progress
    UI polls.
    """

    run: Run
    total_checks: int
    completed_checks: int
    counts: dict[str, int]
    checks: list[CheckProgress]
    #: Ms since start, server-clock (never the browser's — skew renders nonsense, #318);
    #: ``None`` while queued.
    elapsed_ms: int | None
    #: Any unresolved check of a batch-resolving kind (everything but ``comparison``),
    #: so the UI can explain a stalled-looking `0 / N` from the run's composition (#318 G6).
    batched_pending: bool


def get_run_progress(session: Session, run: Run) -> RunProgress:
    """Assemble a run's progress from the suite's checks + the run's results."""
    checks = list(
        session.scalars(select(Check).where(Check.suite_id == run.suite_id).order_by(*CHECK_ORDER))
    )
    results = {r.check_id: r for r in list_results(session, run.id)}
    counts: dict[str, int] = dict.fromkeys(RESULT_STATUSES, 0)
    per_check: list[CheckProgress] = []
    completed = 0
    batched_pending = False
    for check in checks:
        result = results.get(check.id)
        status = result.status if result is not None else None
        per_check.append(CheckProgress(check_id=check.id, name=check.name, status=status))
        if status is not None:
            completed += 1
            counts[status] = counts.get(status, 0) + 1
        elif check.kind != COMPARISON_KIND:
            batched_pending = True
    return RunProgress(
        run=run,
        total_checks=len(checks),
        completed_checks=completed,
        counts=counts,
        checks=per_check,
        elapsed_ms=_elapsed_ms(run),
        batched_pending=batched_pending,
    )


def _elapsed_ms(run: Run) -> int | None:
    """Server-measured run duration in ms, or ``None`` while still queued."""
    if run.started_at is None:
        return None
    started = as_utc(run.started_at)
    end = as_utc(run.finished_at) if run.finished_at is not None else _now()
    return max(int((end - started).total_seconds() * 1000), 0)


# ── sample-failures redaction (PII-safe surfacing on the read API) ────────────

# Safe aggregate keys only; everything else is potential PII and masked. Mirrors
# the producer's `gx_runner._SAMPLE_KEYS` — keep in sync or new keys get masked.
_SAMPLE_SAFE_KEYS = frozenset({"unexpected_count", "unexpected_percent"})
# Same sentinel as core.logging._REDACTED; the two redactors stay separate.
_REDACTED_VALUE = "<redacted>"

# `VALUE_SIGNAL_SUMMARY_KEY` (#1230) is shared with the writer via `datasources.base`;
# consumed for classification, then dropped from the redacted output.


def _redact_sample_value(value: Any) -> Any:
    """Mask data values while preserving container shape and dict keys."""
    if isinstance(value, dict):
        return {key: _redact_sample_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_redact_sample_value(item) for item in value]
    return _REDACTED_VALUE


# Tag values that HARD-mask a column (#415 level 1 — a floor no override lifts).
_SENSITIVE_TAG_VALUES = frozenset({"sensitive", "pii", "confidential", "restricted", "secret"})


def _tag_sensitive(column: str, tags: Mapping[str, str] | None) -> bool:
    """Level 1 — a datasource governance tag marks the column sensitive (hard floor)."""
    if not tags:
        return False
    tag = tags.get(column) or tags.get(column.strip().lower()) or ""
    return str(tag).strip().lower() in _SENSITIVE_TAG_VALUES


def _policy_requires_classification(policy: Mapping[str, Any] | None) -> bool:
    """Whether this suite is in **fail-closed** mode (G3 / #433)."""
    return bool(policy and policy.get("require_classification"))


#: Tag values that explicitly clear a column; consulted only in fail-closed mode.
#: `internal` is deliberately absent — a confidentiality level, not a no-PII assertion.
_NON_SENSITIVE_TAG_VALUES = frozenset({"public", "non_sensitive", "nonsensitive"})


def _tag_non_sensitive(column: str, tags: Mapping[str, str] | None) -> bool:
    """Level 1, the allow side — a governance tag explicitly clears the column."""
    if not tags:
        return False
    tag = tags.get(column) or tags.get(column.strip().lower()) or ""
    return str(tag).strip().lower() in _NON_SENSITIVE_TAG_VALUES


def _policy_pii(column: str, policy: Mapping[str, Any] | None) -> bool:
    """Level 3 — the suite override explicitly lists the column as PII."""
    if not policy:
        return False
    listed = {str(c).strip().lower() for c in (policy.get("pii_columns") or [])}
    return column.strip().lower() in listed


def _policy_identifier(column: str, policy: Mapping[str, Any] | None) -> bool:
    """Level 3 — the suite override names the column as the shown identifier."""
    if not policy:
        return False
    ident = policy.get("identifier_column")
    return bool(ident) and str(ident).strip().lower() == column.strip().lower()


def _known_sensitive(
    column: str,
    values: Sequence[Any],
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
    *,
    value_signal_summary: Mapping[str, Any] | None = None,
) -> bool:
    """Whether a column is **known** sensitive — a governance tag (floor), an explicit
    override, or an *affirmative* name/value PII signal (not the conservative default).
    Gates the **tested** and **identifier** columns: those are shown *unless* known
    sensitive (seeing the failing value / locating the row is the point).
    """
    if _tag_sensitive(column, tags) or _policy_pii(column, policy):
        return True
    if _policy_requires_classification(policy):
        # Fail-closed (G3): "known sensitive" becomes "not known SAFE".
        cleared = _tag_non_sensitive(column, tags) or _policy_identifier(column, policy)
        if not cleared:
            return True
        return is_sensitive(column, values, value_signal_summary=value_signal_summary)
    return is_sensitive(column, values, value_signal_summary=value_signal_summary)


def _may_show_incidental(
    column: str,
    values: Sequence[Any],
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
    *,
    value_signal_summary: Mapping[str, Any] | None = None,
) -> bool:
    """Whether an *incidental* column (not the tested / identifier one) may be shown: only when
    it's affirmatively an IDENTIFIER or SAFE value — everything else default-masks (#415), so
    security can't regress.
    """
    if _tag_sensitive(column, tags) or _policy_pii(column, policy):
        return False
    if _policy_requires_classification(policy):
        # Fail-closed (G3): shown only on explicit clearance AND nothing affirmatively
        # sensitive — dropping either half breaks or LOOSENS the mode.
        if not (_tag_non_sensitive(column, tags) or _policy_identifier(column, policy)):
            return False
        return not is_sensitive(column, values, value_signal_summary=value_signal_summary)
    if _policy_identifier(column, policy):
        return not is_sensitive(column, values, value_signal_summary=value_signal_summary)
    return (
        classify_column(column, list(values), value_signal_summary=value_signal_summary)
        is not ColumnClass.PII
    )


def _values_by_column(rows: Sequence[Any]) -> dict[str, list[Any]]:
    """Gather each column's values across the sampled failing rows, so the classifier's
    value signal (emails, id-shape) sees the whole column, not one cell.
    """
    out: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict):
            for col, val in row.items():
                out[str(col)].append(val)
    return dict(out)


# Tri-state redaction summary surfaced beside `sample_failures` (#424/#415).
RedactionState = Literal["full", "partial", "none"]


@dataclass
class _RedactionTracker:
    """Accumulates per-column show/mask decisions across one `redact_sample_failures`
    call, so the caller can report an honest summary instead of re-sniffing the
    redacted output for the `"<redacted>"` sentinel (fragile — a genuine cell value
    equal to the sentinel would misreport, and it can't see counts).
    """

    column_state: dict[str, bool] = field(default_factory=dict)
    anonymous_masked: bool = False

    def record(self, column: str | None, shown: bool) -> None:
        if column:
            self.column_state[column] = self.column_state.get(column, False) or shown
        elif not shown:
            self.anonymous_masked = True

    def summary(self) -> tuple[RedactionState | None, list[str]]:
        """`(state, redacted_columns)`. ``state`` is ``None`` when nothing data-bearing was seen
        (e.g. only aggregate counts, or an empty sample) — there is nothing true to claim either
        way, so the caller should omit any redaction label rather than guess.
        ``redacted_columns`` lists columns that were masked everywhere they appeared (never
        shown).
        """
        shown_any = any(self.column_state.values())
        masked_any = any(not shown for shown in self.column_state.values()) or self.anonymous_masked
        redacted_columns = sorted(name for name, shown in self.column_state.items() if not shown)
        if not shown_any and not masked_any:
            return None, []
        if masked_any and not shown_any:
            return "full", redacted_columns
        if shown_any and not masked_any:
            return "none", redacted_columns
        return "partial", redacted_columns


def _displayed_sample_key(sample: Mapping[str, Any]) -> str | None:
    """Which failing-row list a viewer actually sees, or ``None`` if neither renders."""
    index_rows = sample.get("unexpected_index_list")
    if isinstance(index_rows, list):
        index_rows = index_rows[:SAMPLE_ROW_CAP]
    if isinstance(index_rows, list) and index_rows and all(isinstance(r, dict) for r in index_rows):
        return "unexpected_index_list"
    if isinstance(sample.get("partial_unexpected_list"), list):
        return "partial_unexpected_list"
    return None


def _redact_row(
    row: Any,
    *,
    tested_column: str | None,
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
    values_by_column: Mapping[str, list[Any]],
    summary_by_column: Mapping[str, Mapping[str, Any]] | None = None,
    tracker: _RedactionTracker | None = None,
) -> Any:
    """Mask a failing-row dict per column: the tested column shows unless *known*
    sensitive; every other column shows only if affirmatively identifier/safe
    (default-mask). Non-dict rows fall back to full masking.
    """
    if not isinstance(row, dict):
        if tracker is not None:
            # A malformed row shape is still a real mask — count it anonymously (#1115).
            tracker.record(None, False)
        return _redact_sample_value(row)
    # Case-insensitive match: GX returns the warehouse's casing (Snowflake upper-cases).
    tested = (tested_column or "").strip().lower()
    out: dict[Any, Any] = {}
    for col, val in row.items():
        name = str(col)
        vals = values_by_column.get(name, [val])
        col_summary = (summary_by_column or {}).get(name)
        if tested and name.strip().lower() == tested:
            show = not _known_sensitive(name, vals, policy, tags, value_signal_summary=col_summary)
        else:
            show = _may_show_incidental(name, vals, policy, tags, value_signal_summary=col_summary)
        out[col] = val if show else _redact_sample_value(val)
        if tracker is not None:
            tracker.record(name, show)
    return out


# Two interchangeable renderings of the same failing rows (#1190/#1197).
_FAILING_ROW_LIST_KEYS = frozenset({"unexpected_index_list", "partial_unexpected_list"})

# Comparison sample buckets (ADR 0015 §4 — written by `comparison_run`).
_COMPARISON_SAMPLE_KEYS = frozenset({"mismatched", "additional_in_source", "additional_in_target"})


def _strip_side_suffix(name: str) -> str:
    """`<col>_src` / `<col>_tgt` → `<col>` for policy/classifier matching."""
    for suffix in ("_src", "_tgt"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _redact_comparison_row(
    row: Any,
    *,
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
    values_by_column: Mapping[str, list[Any]],
    tracker: _RedactionTracker | None = None,
) -> Any:
    """Per-column masking for a comparison sample row, matching policy and classifier on the
    suffix-stripped column name (both sides of a PII column mask together; the join-key columns
    are unsuffixed and match directly).
    """
    if not isinstance(row, dict):
        if tracker is not None:
            # A malformed row shape is still a real mask — count it anonymously (#1115).
            tracker.record(None, False)
        return _redact_sample_value(row)
    out: dict[Any, Any] = {}
    for col, val in row.items():
        raw = str(col)
        name = _strip_side_suffix(raw)
        vals = values_by_column.get(raw, [val])
        hard_masked = _tag_sensitive(raw, tags) or _policy_pii(raw, policy)
        show = not hard_masked and _may_show_incidental(name, vals, policy, tags)
        out[col] = val if show else _redact_sample_value(val)
        if tracker is not None:
            tracker.record(raw, show)
    return out


def resolve_asset(session: Session, suite: Any, run: Any = None) -> Asset | None:
    """The asset a run/suite targets — the run's own asset when set (the target it
    actually ran against), else the suite's configured asset. The one canonical
    precedence rule; callers needing more than just the column tags (e.g. the
    asset's owner) should use this rather than re-deriving it (#1419/#1479's
    "third spelling of the governance floor" shape).
    """
    asset_id = getattr(run, "asset_id", None) or getattr(suite, "asset_id", None)
    return session.get(Asset, asset_id) if asset_id is not None else None


def asset_column_tags(session: Session, suite: Any, run: Any = None) -> dict[str, str] | None:
    """The warehouse's own column classifications for a suite's asset (G3)."""
    asset = resolve_asset(session, suite, run)
    return asset.column_tags if asset is not None else None


def historical_check_context(
    session: Session,
    results: Sequence[Result],
    checks: Mapping[uuid.UUID, Check],
) -> dict[uuid.UUID, tuple[str | None, str | None]]:
    """Per-**result** `(tested_column, expectation_type)`, resolved as of WHEN that
    result was written — not the check's CURRENT state (#1489).
    """
    return historical_check_context_at(
        session, {r.id: (r.check_id, r.created_at) for r in results}, checks
    )


def historical_check_context_at[K: Hashable](
    session: Session,
    subjects: Mapping[K, tuple[uuid.UUID | None, datetime]],
    checks: Mapping[uuid.UUID, Check],
) -> dict[K, tuple[str | None, str | None]]:
    """`(tested_column, expectation_type)` per subject key, each resolved as of its
    own `(check_id, at)` — the `CheckVersion` rule `historical_check_context` applies
    to results, shared here for stored snapshots that keep no `Result` row, e.g. an
    incident's evidence at its `last_seen_at` (#1809).
    """
    check_ids = {check_id for check_id, _at in subjects.values() if check_id is not None}
    versions_by_check: dict[uuid.UUID, list[CheckVersion]] = defaultdict(list)
    if check_ids:
        for version in session.scalars(
            select(CheckVersion)
            .where(CheckVersion.check_id.in_(check_ids))
            .order_by(CheckVersion.check_id, CheckVersion.created_at)
        ):
            versions_by_check[version.check_id].append(version)

    def _resolve(check_id: uuid.UUID | None, at: datetime) -> tuple[str | None, str | None]:
        check = checks.get(check_id) if check_id is not None else None
        versions = versions_by_check.get(check_id) if check_id is not None else None
        if not versions:
            return (
                (check.config.get("column") if check else None),
                (check.expectation_type if check else None),
            )
        # Last version at-or-before `at`; if all are after (clock skew), the earliest
        # beats the live check as an approximation of history.
        effective = versions[0]
        for version in versions:
            if version.created_at <= at:
                effective = version
            else:
                break
        return effective.config.get("column"), effective.expectation_type

    return {key: _resolve(check_id, at) for key, (check_id, at) in subjects.items()}


# Expectation types whose scalar `observed_value` is a literal cell, not a computed statistic
# (#1486).
_CELL_SCALAR_EXPECTATION_TYPES = frozenset(
    {
        "expect_column_max_to_be_between",
        "expect_column_min_to_be_between",
    }
)


def observed_value_exposes_cells(
    redacted: dict[str, Any] | None,
    *,
    expectation_type: str | None = None,
) -> bool:
    """Whether a **redacted** `observed_value` still carries raw cell values."""
    if not isinstance(redacted, dict):
        return False
    values = redacted.get("observed_value")
    if isinstance(values, list) and any(v != _REDACTED_VALUE for v in values):
        return True
    if (
        expectation_type in _CELL_SCALAR_EXPECTATION_TYPES
        and values is not None
        and values != _REDACTED_VALUE
    ):
        return True
    unparsed = redacted.get("unparsed_value")
    return unparsed is not None and unparsed != _REDACTED_VALUE


def _columnless_scalar_shows(
    value: Any,
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
    *,
    expectation_type: str | None,
) -> bool:
    """Whether a scalar `observed_value` with NO tested column may show (#1793).

    With no column nothing a tag or policy can clear it: it shows only when the producing
    type makes it a statistic (row / unexpected-row count), not a cell
    (`_CELL_SCALAR_EXPECTATION_TYPES`); an unknown type fails closed like the list branch.
    The ladder still runs under an empty name so the value-shape signal and fail-closed
    mode (G3) apply rather than being short-circuited.
    """
    if expectation_type is None or expectation_type in _CELL_SCALAR_EXPECTATION_TYPES:
        return False
    return not _known_sensitive("", [value], policy, tags)


def redact_observed_value(
    observed: dict[str, Any] | None,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
    expectation_type: str | None = None,
) -> dict[str, Any] | None:
    """Redact a result's `observed_value` for the read API (#989, #1229).

    `expectation_type` is what lets a column-less scalar be shown at all (#1793) —
    pass it whenever the caller knows it.
    """
    if not observed:
        return observed
    error = observed.get("error")
    if isinstance(error, str):
        observed = {**observed, "error": strip_statement_echo(error)}
    if "unparsed_value" in observed:
        column = str(observed.get("column") or "")
        value = observed.get("unparsed_value")
        show = bool(column) and not _known_sensitive(column, [value], policy, tags)
        return {**observed, "unparsed_value": value if show else _redact_sample_value(value)}
    if "observed_value" not in observed:
        return observed
    raw_observed_value = observed["observed_value"]
    if isinstance(raw_observed_value, list):
        show = tested_column is not None and not _known_sensitive(
            tested_column, raw_observed_value, policy, tags
        )
        capped = raw_observed_value[:SAMPLE_ROW_CAP]
        return {**observed, "observed_value": capped if show else _redact_sample_value(capped)}
    if tested_column is not None:
        show = not _known_sensitive(tested_column, [raw_observed_value], policy, tags)
    else:
        show = _columnless_scalar_shows(
            raw_observed_value, policy, tags, expectation_type=expectation_type
        )
    if show:
        return observed
    return {**observed, "observed_value": _redact_sample_value(raw_observed_value)}


def redact_sample_failures(
    sample: dict[str, Any] | None,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
    tracker: _RedactionTracker | None = None,
) -> dict[str, Any] | None:
    """Redact a result's `sample_failures` for safe surfacing on the read API."""
    if not sample:
        return None
    index_rows = sample.get("unexpected_index_list")
    index_vbc = _values_by_column(index_rows) if isinstance(index_rows, list) else {}
    # Capture-time full-population summary (#1230); absent on old rows → classifier
    # falls back to capped values.
    raw_summary = sample.get(VALUE_SIGNAL_SUMMARY_KEY)
    summary_by_column = raw_summary if isinstance(raw_summary, dict) else None
    # Only the displayed list feeds the tracker (#1197); redaction still runs over BOTH.
    displayed_key = _displayed_sample_key(sample)
    out: dict[str, Any] = {}
    for key, raw_value in sample.items():
        if key == VALUE_SIGNAL_SUMMARY_KEY:
            # Internal metadata (#1230): consumed above, never re-emitted or tracked.
            continue
        # Tracker suppressed on the losing list so `summary()` describes the table on
        # screen (#1197); comparison buckets render together, so they always accumulate.
        key_tracker = (
            None
            if displayed_key is not None and key in _FAILING_ROW_LIST_KEYS and key != displayed_key
            else tracker
        )
        # Sample bound re-applied at READ time (#1196) so pre-cap rows are corrected free.
        value = raw_value[:SAMPLE_ROW_CAP] if isinstance(raw_value, list) else raw_value
        if _is_safe_summary(key, value):
            out[key] = value
        elif key == "unexpected_index_list" and isinstance(value, list):
            out[key] = [
                _redact_row(
                    row,
                    tested_column=tested_column,
                    policy=policy,
                    tags=tags,
                    values_by_column=index_vbc,
                    summary_by_column=summary_by_column,
                    tracker=key_tracker,
                )
                for row in value
            ]
        elif key == "partial_unexpected_list" and isinstance(raw_value, list):
            if raw_value and all(isinstance(v, dict) for v in raw_value):
                vbc = _values_by_column(raw_value)
                out[key] = [
                    _redact_row(
                        row,
                        tested_column=tested_column,
                        policy=policy,
                        tags=tags,
                        values_by_column=vbc,
                        # Sibling-list summary is valid evidence here too — without it a
                        # PII column reappears unmasked through the sibling field (#1230).
                        summary_by_column=summary_by_column,
                        tracker=key_tracker,
                    )
                    for row in value
                ]
            elif tested_column is not None and not _known_sensitive(
                tested_column,
                raw_value,
                policy,
                tags,
                value_signal_summary=(summary_by_column or {}).get(tested_column),
            ):
                out[key] = value  # the tested column's failing values — surfaced
                if key_tracker is not None:
                    key_tracker.record(tested_column, True)
            else:
                out[key] = _redact_sample_value(value)
                if key_tracker is not None:
                    key_tracker.record(tested_column, False)
        elif key in _COMPARISON_SAMPLE_KEYS and isinstance(value, list):
            # Comparison buckets (ADR 0015, #794): match on the SUFFIX-STRIPPED name so
            # `pii_columns: email` masks both `_src`/`_tgt` sides.
            vbc = _values_by_column(raw_value)
            out[key] = [
                _redact_comparison_row(
                    row, policy=policy, tags=tags, values_by_column=vbc, tracker=tracker
                )
                for row in value
            ]
        else:
            # Unrecognized keys default-mask and register anonymously (#1115) so the
            # summary can't under-report.
            out[key] = _redact_sample_value(value)
            if tracker is not None:
                tracker.record(None, False)
    return out


def redact_sample_failures_with_state(
    sample: dict[str, Any] | None,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any] | None, RedactionState | None, list[str]]:
    """`redact_sample_failures` plus an honest redaction summary (#424)."""
    tracker = _RedactionTracker()
    redacted = redact_sample_failures(
        sample, tested_column=tested_column, policy=policy, tags=tags, tracker=tracker
    )
    state, redacted_columns = tracker.summary()
    return redacted, state, redacted_columns


def _is_safe_summary(key: str, value: Any) -> bool:
    """A passthrough-safe aggregate: an allowlisted key whose value is a plain
    number (``bool`` excluded — it's an ``int`` subclass but not a count).
    """
    return (
        key in _SAMPLE_SAFE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool)
    )


# ── retention sweep (configurable PII purge of old result samples) ────────────


def _purge_column(
    session: Session,
    *,
    cutoff: datetime,
    extra_where: Sequence[Any],
    values: dict[str, Any],
    chunk_size: int = CHUNK_SIZE,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Chunked UPDATEs against `results`, scoped to `created_at < cutoff` plus the caller's own
    column-specific guard (#1253 — shared by both halves of `purge_expired_sample_failures` so
    the two purges stay mechanically identical).
    """

    def _build_statement() -> Any:
        candidate_chunk = (
            select(Result.id)
            .where(Result.created_at < cutoff, *extra_where)
            .order_by(Result.created_at)
            .limit(chunk_size)
        )
        return (
            update(Result)
            .where(Result.id.in_(candidate_chunk), Result.created_at < cutoff, *extra_where)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    return chunked_dml(session, _build_statement, chunk_size=chunk_size, on_batch=on_batch)


def purge_expired_sample_failures(
    session: Session,
    *,
    retention_days: int,
    now: datetime | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """Scrub `sample_failures` and the two PII-bearing `observed_value` shapes
    (list-shaped set-oriented expectations, and a monitor's raw `unparsed_value`
    cell) from results older than ``retention_days``.
    """
    if retention_days <= 0:
        return 0
    moment = now or _now()
    cutoff = moment - timedelta(days=retention_days)

    sample_progress = 0
    observed_progress = 0
    unparsed_progress = 0

    def _on_sample_batch(n: int) -> None:
        nonlocal sample_progress
        sample_progress += n

    def _on_observed_batch(n: int) -> None:
        nonlocal observed_progress
        observed_progress += n

    def _on_unparsed_batch(n: int) -> None:
        nonlocal unparsed_progress
        unparsed_progress += n

    def _log_progress() -> None:
        log.info(
            "sample_failures_purged",
            purged=sample_progress,
            sample_failures_purged=sample_progress,
            observed_value_purged=observed_progress,
            unparsed_value_purged=unparsed_progress,
            total_purged=sample_progress + observed_progress + unparsed_progress,
            retention_days=retention_days,
            cutoff=cutoff.isoformat(),
        )

    try:
        sample_typeof = func.jsonb_typeof(Result.sample_failures)
        _purge_column(
            session,
            cutoff=cutoff,
            extra_where=[
                Result.sample_failures_purged_at.is_(None),
                Result.sample_failures.isnot(None),
                sample_typeof != "null",
            ],
            values={"sample_failures": null(), "sample_failures_purged_at": moment},
            chunk_size=chunk_size,
            on_batch=_on_sample_batch,
        )

        # #1253: observed_value's sibling half of the same PII-minimisation gap.
        observed_inner_typeof = func.jsonb_typeof(Result.observed_value["observed_value"])
        _purge_column(
            session,
            cutoff=cutoff,
            extra_where=[observed_inner_typeof == "array"],
            values={"observed_value": null()},
            chunk_size=chunk_size,
            on_batch=_on_observed_batch,
        )

        # #1267: the third observed_value shape — a monitor's raw, potentially-PII
        # target cell (`{"unparsed_value": ..., "column": ...}`), a different
        # mechanism (#989) than the set-oriented-expectation list above.
        _purge_column(
            session,
            cutoff=cutoff,
            extra_where=[Result.observed_value.has_key("unparsed_value")],
            values={"observed_value": null()},
            chunk_size=chunk_size,
            on_batch=_on_unparsed_batch,
        )
    finally:
        _log_progress()

    return sample_progress + observed_progress + unparsed_progress


def reap_stuck_runs(
    session: Session, *, threshold_minutes: int, now: datetime | None = None
) -> list[Run]:
    """Drive runs stuck in a non-terminal state past ``threshold_minutes`` to ``failed``."""
    if threshold_minutes <= 0:
        return []
    moment = now or _now()
    cutoff = moment - timedelta(minutes=threshold_minutes)
    reference = func.coalesce(Run.started_at, Run.created_at)
    stuck = list(
        session.scalars(
            select(Run).where(Run.status.in_(_NON_TERMINAL_STATUSES), reference < cutoff)
        )
    )
    reaped_ids = [str(run.id) for run in stuck]  # capture before commit expires attrs
    # Hygiene (#318): aggregates already ignore these, but the run's own detail page
    # would otherwise show a half-populated set.
    discard_ids = [run.id for run in stuck if run.status == "running"]
    # Only `running` runs emitted an OpenLineage START; capture before the flip so
    # only those get the terminal close.
    started_ids = list(discard_ids)
    for run in stuck:
        run_dispatch.mark_dispatch_failed(run, at=moment, reason=run_dispatch.REAPED_REASON)
    if stuck:
        session.commit()
        for run_id in discard_ids:
            discard_run_results(session, run_id)
        log.warning(
            "stuck_runs_reaped",
            count=len(stuck),
            threshold_minutes=threshold_minutes,
            cutoff=cutoff.isoformat(),
            run_ids=reaped_ids,
        )
        # Close each dangling START with a terminal FAIL (ADR 0034) after the flip
        # commits. Lazy import breaks the lineage↔run_service cycle; fail-open.
        from backend.app.lineage import dispatch as lineage_dispatch

        for run_id in started_ids:
            lineage_dispatch.emit_run_lineage_terminal(session, run_id=run_id)
    return stuck


def fail_run_worker_lost(
    session: Session, *, run_id: uuid.UUID, now: datetime | None = None
) -> bool:
    """Drive a single run to ``failed`` after its worker process died (#755)."""
    run = session.get(Run, run_id)
    if run is None or run.status not in _NON_TERMINAL_STATUSES:
        return False
    moment = now or _now()
    was_running = run.status == "running"
    run_dispatch.mark_dispatch_failed(run, at=moment, reason=run_dispatch.WORKER_LOST_REASON)
    session.commit()
    if was_running:
        # SIGKILLed child cleared nothing (#318); same hygiene reasoning as the reaper.
        discard_run_results(session, run_id)
    log.warning("run_failed_worker_lost", run_id=str(run_id), was_running=was_running)
    # Same OpenLineage close-the-dangling-START contract as the reaper; fail-open.
    if was_running:
        from backend.app.lineage import dispatch as lineage_dispatch

        lineage_dispatch.emit_run_lineage_terminal(session, run_id=run_id)
    return True
