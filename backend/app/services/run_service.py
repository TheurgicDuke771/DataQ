"""Execute a suite's checks via a datasource adapter and persist the results.

This is the GX-agnostic core of a run: it drives the `Run` lifecycle, calls a
`CheckRunner` (injected — Snowflake in production, a fake in tests), and maps the
returned `SuiteOutcome` onto `Result` rows. GX/Snowflake specifics live behind
the adapter; this layer only knows the DTOs in ``datasources.base``.

Run.status describes *execution*, not data quality: a run that completes is
``succeeded`` even when checks fail (the failures live in ``Result.status`` /
``SuiteOutcome.success``). ``failed`` means the run could not execute — the
adapter raised (e.g. could not reach the warehouse).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, null, select, update
from sqlalchemy.orm import Session

from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.datasources.base import (
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
from backend.app.db.models import (
    COMPARISON_KIND,
    RESULT_OPERATIONAL_STATUSES,
    RESULT_STATUSES,
    RUN_STATUSES,
    SEVERITY_RANK,
    Check,
    Result,
    Run,
    worst_severity,
)
from backend.app.services import run_dispatch, suite_service
from backend.app.services.column_classification import ColumnClass, classify_column, is_sensitive
from backend.app.services.failure_classifier import classify_failure_reason
from backend.app.services.severity import resolve_status

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _build_result(run_id: uuid.UUID, check: Check, outcome: CheckOutcome) -> Result:
    """Map a check + its GX outcome to a `Result`, deriving the severity tier.

    The unexpected-percent badness scalar is extracted once and used both to band
    the tier (ADR 0005 / 0016) and to persist as the durable, SQL-aggregatable
    `metric_value` (ADR 0012). `duration_ms` stays NULL in v1 — per-check timing
    isn't separable from GX's single suite-level `validate()` (reserved seam).

    A check the runner could not *evaluate* (`outcome.errored` — e.g. it raised
    referencing a missing column) is an operational ``error`` result (#122), not a
    data failure: no severity tier, no `metric_value`. It's orthogonal to the
    health score (ADR 0005 weights only the four tiers), so it must never be
    banded as `fail`. The error message lands in `observed_value` for debugging —
    GX exception messages are schema-level (no row data), so they don't go through
    the `sample_failures` retention/PII path.
    """
    status, metric = resolve_status(
        outcome,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
    )
    if outcome.errored:
        # An errored check has no observed metric and no failing-row sample; surface
        # the (schema-level, row-data-free) GX message for debugging instead.
        observed = {"error": outcome.error_message} if outcome.error_message else None
        sample = None
    else:
        observed = sanitize_json(outcome.observed_value)
        sample = sanitize_json(outcome.sample_failures)
    return Result(
        run_id=run_id,
        check_id=check.id,
        status=status,
        metric_value=metric,
        observed_value=observed,
        expected_value=sanitize_json(outcome.expected_value),
        sample_failures=sample,
    )


_EXPECTATION_KIND = "expectation"


def _run_outcomes(
    runner: CheckRunner,
    *,
    table: str,
    schema: str | None,
    checks: list[Check],
    index_columns: list[str] | None = None,
    comparison_executor: Callable[[Check], CheckOutcome] | None = None,
    stateful_monitor_executor: Callable[[Check], CheckOutcome] | None = None,
) -> list[CheckOutcome]:
    """Run a suite's checks, dispatching by `check.kind` (ADR 0012), and return one
    outcome per check in the **same order** (so they zip 1:1 onto result rows).

    * ``expectation`` kind → the GX `CheckRunner.run_checks`.
    * scalar monitor kinds (``freshness``/``volume``) → `run_monitors` on a
      runner that advertises the kind (#429); an unsupported kind raises here,
      never silently mis-runs.
    * stateful monitor kinds (``schema_drift``, #592) → the injected
      ``stateful_monitor_executor`` (the worker builds one via
      `schema_drift.build_schema_drift_executor` — it owns the session and the
      baseline store, which runners must never see). A caller that supplies
      none gets a per-check operational ``error`` outcome (#122).
    * ``comparison`` → the injected ``comparison_executor`` (the worker builds
      one via `comparison_run.build_comparison_executor`, #794); same
      no-executor semantics.
    * any other reserved kind (`anomaly`) has no run path *or* authoring path
      → `NotImplementedError` (unreachable via CRUD).

    This composes with the connection-type runner selection (ADR 0011): `kind`
    chooses the *monitor*, `connection.type` chose the *adapter* (the runner)."""
    expectation_idx = [i for i, c in enumerate(checks) if c.kind == _EXPECTATION_KIND]
    monitor_idx = [i for i, c in enumerate(checks) if c.kind in SCALAR_MONITOR_KINDS]
    stateful_idx = [i for i, c in enumerate(checks) if c.kind in STATEFUL_MONITOR_KINDS]
    comparison_idx = [i for i, c in enumerate(checks) if c.kind == COMPARISON_KIND]
    handled = {_EXPECTATION_KIND, *MONITOR_KINDS, COMPARISON_KIND}
    unsupported = sorted({c.kind for c in checks if c.kind not in handled})
    if unsupported:
        raise NotImplementedError(f"no run path for check kind(s) {', '.join(unsupported)}")

    outcomes: list[CheckOutcome | None] = [None] * len(checks)
    for i in stateful_idx:
        if stateful_monitor_executor is None:
            outcomes[i] = CheckOutcome(
                expectation_type=checks[i].expectation_type,
                success=False,
                errored=True,
                error_message=(
                    "stateful monitor kinds need the baseline-diff run path (no "
                    "executor supplied on this caller — #592)"
                ),
            )
        else:
            outcomes[i] = stateful_monitor_executor(checks[i])
    for i in comparison_idx:
        if comparison_executor is None:
            outcomes[i] = CheckOutcome(
                expectation_type=checks[i].expectation_type,
                success=False,
                errored=True,
                error_message=(
                    "comparison checks need the comparison run path (no executor "
                    "supplied on this caller — ADR 0015)"
                ),
            )
        else:
            outcomes[i] = comparison_executor(checks[i])
    if expectation_idx:
        specs = [
            CheckSpec(expectation_type=checks[i].expectation_type, kwargs=dict(checks[i].config))
            for i in expectation_idx
        ]
        suite_outcome = runner.run_checks(
            table=table, schema=schema, checks=specs, index_columns=index_columns
        )
        for i, oc in zip(expectation_idx, suite_outcome.checks, strict=True):
            outcomes[i] = oc
    if monitor_idx:
        # Capability gate (#429): the runner ADVERTISES which monitor kinds it
        # evaluates. Never `isinstance(runner, MonitorRunner)` — a
        # runtime_checkable Protocol matches on the method NAME alone, so an
        # unrelated `run_monitors` would pass the gate and TypeError at the call;
        # and per-kind capability keeps this dispatch data-driven as stateful
        # kinds (#592/#593) land on some runners before others.
        supported = frozenset(getattr(runner, "supported_monitor_kinds", frozenset()))
        unsupported_kinds = sorted({checks[i].kind for i in monitor_idx} - supported)
        if unsupported_kinds:
            raise NotImplementedError(
                f"{type(runner).__name__} does not support monitor kind(s) "
                f"{', '.join(unsupported_kinds)} — these need a monitor-capable "
                "datasource (Snowflake / Unity Catalog / Iceberg)"
            )
        if not callable(getattr(runner, "run_monitors", None)):
            # The mirror hole of the old isinstance gate: advertising kinds
            # without the method must reject as cleanly as the reverse.
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
        for i, oc in zip(monitor_idx, monitor_outcomes, strict=True):
            outcomes[i] = oc

    # Every index is filled: expectation_idx + monitor_idx + stateful_idx +
    # comparison_idx together cover all checks once the unsupported-kind guard
    # above has run.
    return [cast(CheckOutcome, oc) for oc in outcomes]


def _cancelled_mid_run(session: Session, run: Run) -> bool:
    """Did a cancel commit (from the API session) while this run was executing?

    ``refresh`` issues a fresh SELECT, so under READ COMMITTED it sees the API
    session's committed ``cancelled`` even though this (worker) session set the
    run ``running`` earlier. Note: with ``autoflush=False`` (db/session.py) the
    refresh does NOT flush the caller's pending result rows — they stay staged for
    the caller to either ``commit`` (not cancelled) or ``rollback`` (cancelled).
    """
    session.refresh(run)
    return run.status == "cancelled"


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
    """Run ``checks`` against ``table`` via ``runner`` and persist the outcome.

    ``run`` must already be persisted (it carries the id the results link to).
    ``index_columns`` (the suite's identifier column, #415) is requested from GX so
    failing rows are captured with a locator; ``None`` keeps the scalar-only sample.
    Returns the same `Run`, updated to ``succeeded`` or ``failed``.
    """
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

    # Everything from here — dispatching by kind, running the adapter, building
    # rows, and persisting them — is guarded so any failure drives the run to a
    # terminal 'failed' state. Without this, a DB error during add_all/commit (or
    # an unrunnable check kind) would leave the run stuck in 'running' forever.
    # rollback() discards any partial result inserts before we record the failure.
    try:
        outcomes = _run_outcomes(
            runner,
            table=table,
            schema=schema,
            checks=checks,
            index_columns=index_columns,
            comparison_executor=comparison_executor,
            stateful_monitor_executor=stateful_monitor_executor,
        )
        rows = [
            _build_result(run.id, check, check_outcome)
            for check, check_outcome in zip(checks, outcomes, strict=True)
        ]
        session.add_all(rows)
        # Cooperative cancellation: if a cancel committed (from the API session)
        # while GX ran, don't overwrite it with a terminal success — drop the now-
        # moot (still-pending, unflushed) results and leave the run 'cancelled'.
        if _cancelled_mid_run(session, run):
            session.rollback()
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        run.status = "succeeded"
        run.finished_at = _now()
        # Clear any reason a prior reap stamped: a slow-but-alive worker whose run
        # was reaped (failed + REAPED_REASON) can still finish and commit success
        # here — it must not surface as succeeded-with-a-failure-reason (#605).
        run.failure_reason = None
        session.commit()
    except Exception as exc:
        session.rollback()
        # Same cooperative check on the failure path: a run the user cancelled
        # mid-flight that *also* errored stays 'cancelled', not masked as 'failed'.
        if _cancelled_mid_run(session, run):
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        run.status = "failed"
        run.finished_at = _now()
        # Redaction-safe reason (#605): classify the exception into a fixed
        # message — the raw text (which can carry DSN/credential fragments) stays
        # in the server log below, never on the persisted/surfaced reason.
        run.failure_reason = classify_failure_reason(exc)
        session.commit()
        log.exception("run_failed", run_id=str(run.id), table=table)
        return run

    log.info(
        "run_completed",
        run_id=str(run.id),
        suite_success=all(o.success for o in outcomes),
        n_results=len(rows),
    )
    return run


def skip_run(session: Session, *, run: Run, checks: list[Check], reason: str) -> Run:
    """Record a run that had nothing to evaluate — every check `skip`ped (#122).

    Used when the adapter is never invoked because there's no data to validate
    (e.g. the target batch hasn't landed yet). The run still **succeeds** — it
    executed end to end, it just found nothing to check — and each check gets a
    ``skip`` Result carrying the ``reason`` (operational, not a severity tier, so
    it's excluded from the health-score N per ADR 0005). Distinct from ``failed``,
    which means the run could not execute.
    """
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


# ── read model (PR-C0b: the runs/results surface) ────────────────────────────
# Reads are scoped to suites the user can access — owned (`created_by`) or shared
# (`shares`), the same visibility `suite_service.list_suites` enforces. The API
# layer additionally calls `require_permission` for single-suite / single-run
# lookups (404 hides existence); this subquery is the defence-in-depth filter so
# a list query can never leak a run from a suite the caller can't see.


def list_runs(
    session: Session,
    *,
    user_id: uuid.UUID,
    suite_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    include_all: bool = False,
) -> list[Run]:
    """Runs for suites the user can access, newest first (`created_at` desc).

    Optionally narrowed to one ``suite_id`` and/or a ``status``. The accessible
    subquery is always applied, so passing a ``suite_id`` the user can't see
    yields an empty list (the API layer 404s that case up front via
    `require_permission`, but the filter keeps the service safe on its own).
    ``include_all`` spans every suite — the workspace-admin view (ADR 0027).
    """
    accessible = suite_service.accessible_suite_ids(user_id, include_all=include_all)
    stmt = (
        select(Run).where(Run.suite_id.in_(accessible)).order_by(Run.created_at.desc()).limit(limit)
    )
    if suite_id is not None:
        stmt = stmt.where(Run.suite_id == suite_id)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    return list(session.scalars(stmt))


def check_outcome_counts(
    session: Session, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int, str | None]]:
    """Per-run ``(checks_total, checks_passed, worst_severity)`` for a set of runs,
    in a single grouped query (no N+1). ``worst_severity`` is the highest of
    warn/fail/critical present, else ``None`` (all passed / only operational).

    ``checks_total``/``checks_passed`` count **evaluated** checks — the four
    severity tiers (pass/warn/fail/critical) — and **exclude** operational
    ``skip``/``error`` (#122), so the X/Y matches the run-detail page's "Checks
    passed" denominator and an all-skip run reports total 0 (rendered ``—``, not a
    misleading green ``0/N``).

    Lets the runs list surface a run's *data-quality* outcome — distinct from the
    run's *execution* status, which is ``succeeded`` even when checks failed."""
    if not run_ids:
        return {}
    rows = session.execute(
        select(Result.run_id, Result.status, func.count())
        .where(Result.run_id.in_(run_ids))
        .group_by(Result.run_id, Result.status)
    ).all()
    by_run: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for run_id, status, n in rows:
        by_run[run_id][status] = n
    out: dict[uuid.UUID, tuple[int, int, str | None]] = {}
    for run_id, by_status in by_run.items():
        passed = by_status.get("pass", 0)
        # Worst check outcome via the single shared severity helper (#655); skip/error
        # aren't failing tiers, so they never rank.
        worst = worst_severity(by_status)
        # Evaluated checks only: pass + the three failing tiers (skip/error excluded).
        total = passed + sum(by_status.get(tier, 0) for tier in SEVERITY_RANK)
        out[run_id] = (total, passed, worst)
    return out


def operational_result_flags(
    session: Session, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[bool, bool]]:
    """Per-run ``(has_error, has_skip)`` over its **operational** results (#122).

    The exact complement of :func:`check_outcome_counts`, which counts only the
    *evaluated* severity tiers and deliberately drops ``skip``/``error``. Those
    dropped rows are the signal that DataQ could not evaluate a check — the
    datasource threw (``error``) or a precondition wasn't met (``skip``) — so the
    asset view reads them to derive **connection** health (can we reach the thing?)
    separately from **suite** health (is the data good?), per #803.

    Presence, not counts: one grouped query, a row exists iff that status occurs.
    """
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
# The complement, single-sourced from the canonical status set so a new
# lifecycle status can't silently escape the reaper's net (#309).
_NON_TERMINAL_STATUSES = frozenset(RUN_STATUSES) - _TERMINAL_STATUSES


def cancel_run(session: Session, run: Run) -> bool:
    """Transition a non-terminal run to ``cancelled``; return whether it changed.

    Returns ``False`` if the run is already terminal (succeeded/failed/cancelled)
    — the API surfaces that as 409. Sets ``finished_at``; ``started_at`` is left
    as-is (NULL if the run was still queued). This is the DB half; the API layer
    also best-effort revokes the Celery task, and the worker honours the
    ``cancelled`` status cooperatively (start-check + in-flight guard).
    """
    if run.status in _TERMINAL_STATUSES:
        return False
    run.status = "cancelled"
    run.finished_at = _now()
    session.commit()
    log.info("run_cancelled", run_id=str(run.id))
    return True


def list_results(session: Session, run_id: uuid.UUID) -> list[Result]:
    """The result rows for a run, in stable check order (`created_at`)."""
    return list(
        session.scalars(select(Result).where(Result.run_id == run_id).order_by(Result.created_at))
    )


# ── run progress (A1: the poll surface for the live-progress UI) ──────────────


@dataclass(frozen=True)
class CheckProgress:
    """One check's progress within a run. ``status`` is ``None`` when the check has
    no result row — *pending* while the run is active, or *not recorded* for a
    terminal run (a ``failed`` run rolls back and writes no results, so consumers
    must read this together with the run's lifecycle ``status``, not in isolation)."""

    check_id: uuid.UUID
    name: str
    status: str | None


@dataclass(frozen=True)
class RunProgress:
    """A run's live progress: lifecycle status + per-check resolution + a status
    histogram, the compact shape the live-progress UI polls."""

    run: Run
    total_checks: int
    completed_checks: int
    counts: dict[str, int]
    checks: list[CheckProgress]


def get_run_progress(session: Session, run: Run) -> RunProgress:
    """Assemble a run's progress from the suite's checks + the run's results.

    DB-driven (not Celery task state): the worker writes the ``run.status``
    lifecycle (queued → running → succeeded/failed/cancelled) and the per-check
    ``Result`` rows, so the DB is the source of truth and this composes with the
    same suite-scoped authz the rest of the read API uses.

    Each suite check maps to its result's status, or ``None`` while pending.
    Note: because GX validates a suite in one atomic batch, all result rows land
    together at completion — so mid-run a check reads ``pending`` and the
    histogram fills at the terminal transition (this endpoint reports lifecycle +
    final per-check resolution, not sub-GX incremental progress). Checks are taken
    from the *current* suite definition; a result is matched to its check by id.
    """
    checks = list(
        session.scalars(
            select(Check).where(Check.suite_id == run.suite_id).order_by(Check.created_at)
        )
    )
    # One result per (run_id, check_id) in v1 (each run writes one row per check);
    # keyed by check_id to join against the suite's current checks.
    results = {r.check_id: r for r in list_results(session, run.id)}
    counts: dict[str, int] = dict.fromkeys(RESULT_STATUSES, 0)
    per_check: list[CheckProgress] = []
    completed = 0
    for check in checks:
        result = results.get(check.id)
        status = result.status if result is not None else None
        per_check.append(CheckProgress(check_id=check.id, name=check.name, status=status))
        if status is not None:
            completed += 1
            counts[status] = counts.get(status, 0) + 1
    return RunProgress(
        run=run,
        total_checks=len(checks),
        completed_checks=completed,
        counts=counts,
        checks=per_check,
    )


# ── sample-failures redaction (PII-safe surfacing on the read API) ────────────

# Aggregate summary keys in a GX sample are counts/percentages, not row data, so
# they are safe to surface. Everything else — notably `partial_unexpected_list`,
# the raw offending cell values — is treated as potential PII and masked. These
# mirror the producer's `gx_runner._SAMPLE_KEYS`; keep the two in sync when the
# sample shape grows (a new safe aggregate must be added here or it gets masked).
_SAMPLE_SAFE_KEYS = frozenset({"unexpected_count", "unexpected_percent"})
# Same sentinel string as the structlog redactor (core.logging._REDACTED); the
# two redactors stay deliberately separate (key-based for logs, value-based here).
_REDACTED_VALUE = "<redacted>"


def _redact_sample_value(value: Any) -> Any:
    """Mask data values while preserving container shape and dict keys.

    List length and row-dict column names are *schema*, not row data, so they
    stay (they tell the viewer how many rows / which columns failed); every leaf
    value is replaced with ``"<redacted>"``.
    """
    if isinstance(value, dict):
        return {key: _redact_sample_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_redact_sample_value(item) for item in value]
    return _REDACTED_VALUE


# Datasource-tag values that HARD-mask a column (#415 level 1 — the governance
# floor an override can't lift). The datasource-tags layer that *populates* `tags`
# is a later increment; today callers pass no tags, so this is dormant but wired.
_SENSITIVE_TAG_VALUES = frozenset({"sensitive", "pii", "confidential", "restricted", "secret"})


def _tag_sensitive(column: str, tags: Mapping[str, str] | None) -> bool:
    """Level 1 — a datasource governance tag marks the column sensitive (hard floor)."""
    if not tags:
        return False
    tag = tags.get(column) or tags.get(column.strip().lower()) or ""
    return str(tag).strip().lower() in _SENSITIVE_TAG_VALUES


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
) -> bool:
    """Whether a column is **known** sensitive — a governance tag (floor), an explicit
    override, or an *affirmative* name/value PII signal (not the conservative default).
    Gates the **tested** and **identifier** columns: those are shown *unless* known
    sensitive (seeing the failing value / locating the row is the point)."""
    return (
        _tag_sensitive(column, tags) or _policy_pii(column, policy) or is_sensitive(column, values)
    )


def _may_show_incidental(
    column: str,
    values: Sequence[Any],
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
) -> bool:
    """Whether an *incidental* column (not the tested / identifier one) may be shown:
    only when it's affirmatively an IDENTIFIER or SAFE value — everything else
    default-masks (#415), so security can't regress. A governance tag / override-PII
    always masks; an override-named identifier shows **unless it is affirmatively PII**
    (a designated locator can't un-mask a column whose name/values are direct PII —
    e.g. an ``EMAIL`` set as identifier, or a natural key holding emails)."""
    if _tag_sensitive(column, tags) or _policy_pii(column, policy):
        return False
    if _policy_identifier(column, policy):
        return not is_sensitive(column, values)
    return classify_column(column, list(values)) is not ColumnClass.PII


def _values_by_column(rows: Sequence[Any]) -> dict[str, list[Any]]:
    """Gather each column's values across the sampled failing rows, so the classifier's
    value signal (emails, id-shape) sees the whole column, not one cell."""
    out: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict):
            for col, val in row.items():
                out[str(col)].append(val)
    return dict(out)


def _redact_row(
    row: Any,
    *,
    tested_column: str | None,
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
    values_by_column: Mapping[str, list[Any]],
) -> Any:
    """Mask a failing-row dict per column: the tested column shows unless *known*
    sensitive; every other column shows only if affirmatively identifier/safe
    (default-mask). Non-dict rows fall back to full masking."""
    if not isinstance(row, dict):
        return _redact_sample_value(row)
    # Case-insensitive tested-column match: GX returns the warehouse's column casing
    # (Snowflake upper-cases), which need not match the check config's `column`.
    tested = (tested_column or "").strip().lower()
    out: dict[Any, Any] = {}
    for col, val in row.items():
        name = str(col)
        vals = values_by_column.get(name, [val])
        if tested and name.strip().lower() == tested:
            show = not _known_sensitive(name, vals, policy, tags)
        else:
            show = _may_show_incidental(name, vals, policy, tags)
        out[col] = val if show else _redact_sample_value(val)
    return out


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
) -> Any:
    """Per-column masking for a comparison sample row, matching policy and
    classifier on the suffix-stripped column name (both sides of a PII column
    mask together; the join-key columns are unsuffixed and match directly).
    The hard-mask levels (governance tags + `pii_columns`) match BOTH the raw
    and stripped names, so an entry written as the displayed suffixed name
    (`status_src`), or a real column that genuinely ends in `_src`, still
    masks — an explicit listing must never be silently ignored. There is no
    `tested_column` in a comparison — every column is incidental, so
    everything not affirmatively identifier/safe default-masks (#415)."""
    if not isinstance(row, dict):
        return _redact_sample_value(row)
    out: dict[Any, Any] = {}
    for col, val in row.items():
        raw = str(col)
        name = _strip_side_suffix(raw)
        vals = values_by_column.get(raw, [val])
        hard_masked = _tag_sensitive(raw, tags) or _policy_pii(raw, policy)
        show = not hard_masked and _may_show_incidental(name, vals, policy, tags)
        out[col] = val if show else _redact_sample_value(val)
    return out


def redact_sample_failures(
    sample: dict[str, Any] | None,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Redact a result's `sample_failures` for safe surfacing on the read API.

    `sample_failures` carries aggregate counts plus `partial_unexpected_list` — the
    failing values of the **tested column** — and (when the runner records it) an
    `unexpected_index_list` of failing rows. Suite-level ``view`` authz lets
    share-recipients read a suite's results, so PII must not cross that boundary
    unredacted (CLAUDE.md PII rule; purged on the retention sweep below).

    Column-aware policy (#415) — surgical, not blanket, over three authority layers:
    datasource **tags** (``tags``, a governance floor — later increment), the suite
    **override** (``policy``), and the name+value **classifier**. Rules:

    * numeric summary keys (`unexpected_count` / `unexpected_percent`) always pass;
    * `partial_unexpected_list` (the tested column's scalar failing values) passes when
      the tested column is **not known sensitive** — so a non-PII breach (a bad
      ``LINE_TOTAL``) is *visible* while a PII tested column (``email``) stays masked;
      with **no** ``tested_column`` the list is masked (no column context → safe default);
    * row-dicts (`unexpected_index_list`, or a dict-shaped `partial_unexpected_list`)
      are redacted **per column** — the tested column + identifiers + safe values shown,
      PII + unclassified masked;
    * everything else default-masks. ``None`` sample passes through unchanged.
    """
    if not sample:
        return None
    index_rows = sample.get("unexpected_index_list")
    index_vbc = _values_by_column(index_rows) if isinstance(index_rows, list) else {}
    out: dict[str, Any] = {}
    for key, value in sample.items():
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
                )
                for row in value
            ]
        elif key == "partial_unexpected_list" and isinstance(value, list):
            if value and all(isinstance(v, dict) for v in value):
                vbc = _values_by_column(value)
                out[key] = [
                    _redact_row(
                        row,
                        tested_column=tested_column,
                        policy=policy,
                        tags=tags,
                        values_by_column=vbc,
                    )
                    for row in value
                ]
            elif tested_column is not None and not _known_sensitive(
                tested_column, value, policy, tags
            ):
                out[key] = value  # the tested column's failing values — surfaced
            else:
                out[key] = _redact_sample_value(value)
        elif key in _COMPARISON_SAMPLE_KEYS and isinstance(value, list):
            # Comparison buckets (ADR 0015, #794): rows carry `<col>_src` /
            # `<col>_tgt` pairs plus unsuffixed key columns. Policy/classifier
            # matching runs on the SUFFIX-STRIPPED name so a `pii_columns`
            # entry like `email` masks both sides — while unknown columns keep
            # the default-mask posture.
            vbc = _values_by_column(value)
            out[key] = [
                _redact_comparison_row(row, policy=policy, tags=tags, values_by_column=vbc)
                for row in value
            ]
        else:
            out[key] = _redact_sample_value(value)
    return out


def _is_safe_summary(key: str, value: Any) -> bool:
    """A passthrough-safe aggregate: an allowlisted key whose value is a plain
    number (``bool`` excluded — it's an ``int`` subclass but not a count)."""
    return (
        key in _SAMPLE_SAFE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool)
    )


# ── retention sweep (configurable PII purge of old result samples) ────────────


def purge_expired_sample_failures(
    session: Session, *, retention_days: int, now: datetime | None = None
) -> int:
    """Scrub `sample_failures` from results older than ``retention_days``.

    ``sample_failures`` is the only result column that can carry real (possibly
    PII-bearing) data rows; after the retention window we null it out (to a true
    SQL NULL) and stamp ``sample_failures_purged_at`` so the purge is auditable.
    The result row itself — and crucially ``metric_value`` — is **kept**, so
    dashboard trends / anomaly baselines survive the purge (ADR 0012); this is a
    PII-minimisation sweep, not a run-history delete. Returns the rows scrubbed.

    Only rows that actually hold a sample *object* are touched: the JSONB column
    stores Python ``None`` as JSON ``'null'`` (``none_as_null`` defaults False),
    and passing/errored checks write that — so ``IS NOT NULL`` would over-match
    millions of empty rows. ``jsonb_typeof`` excludes both SQL NULL (→ NULL) and
    JSON ``'null'`` (→ ``'null'``), leaving only real ``object``/``array``
    samples. Naturally idempotent (a scrubbed row is SQL NULL → typeof NULL →
    excluded); the ``purged_at IS NULL`` guard makes that intent explicit.

    ``retention_days <= 0`` disables the sweep (returns 0 without touching the DB)
    — a clean off-switch rather than purging everything. The cutoff is anchored on
    ``Result.created_at`` (when the result landed ≈ when the run completed).
    """
    if retention_days <= 0:
        return 0
    moment = now or _now()
    cutoff = moment - timedelta(days=retention_days)
    sample_typeof = func.jsonb_typeof(Result.sample_failures)
    # session.execute(<DML>) returns a CursorResult; the typed overload widens it
    # to Result (no rowcount), so cast to read the affected-row count.
    purge_result = cast(
        CursorResult[Any],
        session.execute(
            update(Result)
            .where(
                Result.created_at < cutoff,
                Result.sample_failures_purged_at.is_(None),
                sample_typeof.isnot(None),
                sample_typeof != "null",
            )
            .values(sample_failures=null(), sample_failures_purged_at=moment)
            # Fire-and-forget bulk DML on a fresh, short-lived worker session with
            # no loaded Result identities — skip the ORM identity-map sync, which
            # under the default 'auto'/'fetch' would emit an extra SELECT of every
            # matching PK before the UPDATE (the WHERE uses jsonb_typeof, so the
            # in-Python 'evaluate' strategy can't apply).
            .execution_options(synchronize_session=False)
        ),
    )
    session.commit()
    purged = purge_result.rowcount
    log.info(
        "sample_failures_purged",
        purged=purged,
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
    )
    return purged


def reap_stuck_runs(
    session: Session, *, threshold_minutes: int, now: datetime | None = None
) -> list[Run]:
    """Drive runs stuck in a non-terminal state past ``threshold_minutes`` to ``failed``.

    Closes the orphan window (#309): a run is committed ``queued`` *before*
    ``run_dispatch`` publishes its task, so a process death in that window — or a
    worker that died mid-execution leaving a run ``running`` — would otherwise leave
    the row non-terminal forever (gap recovery only covers ``pipeline_runs``).

    The reaper **fails** stuck runs rather than re-dispatching them: a ``queued``
    run with no ``celery_task_id`` does *not* prove the task was never published —
    ``dispatch_run`` commits the id in a second, non-atomic step, so the task may
    already be in the broker (see its no-2-phase-commit note). Re-dispatching could
    double-run; failing is safe and visible (the run shows ``failed`` in the runs
    table / dashboard and the user re-runs manually), reusing the canonical
    ``run_dispatch.mark_dispatch_failed`` shape every trigger path uses.

    Deliberately **does not publish an alert**: a ``running`` run only crosses the
    threshold if it ran longer than the longest plausible suite, which can't be
    distinguished from a slow-but-alive worker without a heartbeat. Alerting would
    risk an *irreversible* spurious operational-failure notification (and a second
    one when the live worker later finishes). A reaped run is an infra/liveness
    event — surfaced in the UI here and via App Insights — not a per-suite
    data-quality alert. If the worker is in fact still alive it overwrites the
    status with its true outcome on completion (a harmless self-correction; with
    no alert sent there is no side effect to retract).

    Staleness is measured from ``COALESCE(started_at, created_at)`` so an actively-
    running run that *started* recently isn't reaped on the strength of an old
    ``created_at``. The threshold must exceed the longest plausible run.
    ``threshold_minutes <= 0`` disables the sweep. Returns the reaped runs.
    """
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
    # A `running` run emitted an OpenLineage START (the worker got that far before
    # dying); a `queued` one never did. Capture the started set before the flip so
    # the post-commit terminal emit only fires for runs that actually opened a run
    # in Marquez — a queued reap gets none (there's no dangling START to close).
    started_ids = [run.id for run in stuck if run.status == "running"]
    for run in stuck:
        # Canonical terminal-failed shape, one shared `moment` across the batch.
        run_dispatch.mark_dispatch_failed(run, at=moment, reason=run_dispatch.REAPED_REASON)
    if stuck:
        session.commit()
        log.warning(
            "stuck_runs_reaped",
            count=len(stuck),
            threshold_minutes=threshold_minutes,
            cutoff=cutoff.isoformat(),
            run_ids=reaped_ids,
        )
        # Close the dangling START for each reaped-`running` run with a terminal FAIL
        # (ADR 0034, #758) — a worker death otherwise leaves a permanently-RUNNING run
        # in Marquez. Emitted per-run after the status flip commits (so the event maps
        # to FAIL). Lazy import breaks the lineage↔run_service cycle; dark-by-default +
        # fail-open (dispatch never raises), so this is a no-op when emission is off.
        from backend.app.lineage import dispatch as lineage_dispatch

        for run_id in started_ids:
            lineage_dispatch.emit_run_lineage_terminal(session, run_id=run_id)
    return stuck
