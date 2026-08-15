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
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import delete, func, null, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

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
from backend.app.services.failure_classifier import safe_failure_reason
from backend.app.services.rollup import status_histograms
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
    banded as `fail`. The error message lands in `observed_value` for debugging.
    That field is outside the `sample_failures` retention/PII path, so the message
    is put through `strip_statement_echo` first (#1203): on a SQL engine the
    runner's message is a SQLAlchemy `StatementError` rendering, whose
    `[SQL: …] [parameters: …]` tail echoes the statement and every bound value —
    target data — straight into a field the read layer's column policy does not
    cover. The driver's own message survives; only the echo goes.

    A check whose *precondition* wasn't met (`outcome.skipped`, #593) is the other
    operational status, ``skip`` — resolved in `severity.resolve_status` so the
    dry-run preview agrees. Its `observed_value` is a normal (DataQ-authored,
    row-data-free) payload explaining what was missing, so it takes the ordinary
    sanitise path rather than the errored branch's message-only one.
    """
    status, metric = resolve_status(
        outcome,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
    )
    if outcome.errored:
        # An errored check has no observed metric and no failing-row sample; surface
        # the runner's message for debugging instead — minus the SQL/parameter echo
        # a SQL engine appends to it (#1203). This is the one place every kind and
        # every datasource funnels through on the way to `observed_value`, so the
        # strip cannot diverge per runner (Snowflake and Unity Catalog share it).
        error_message = strip_statement_echo(outcome.error_message)
        observed = {"error": error_message} if error_message else None
        # An errored monitor may also carry the target cell that provoked it
        # (#989). It rides here rather than inside the message so the read layer
        # can redact it under the suite's column policy — the errored branch
        # deliberately bypasses the `sample_failures` path, and that bypass is
        # exactly what let a cell value reach the UI unmasked.
        if outcome.observed_value and "unparsed_value" in outcome.observed_value:
            observed = {**(observed or {}), **sanitize_json(outcome.observed_value)}
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
        # Persisted on EVERY status, including `error` (#595). The errored branch
        # above drops the observed value and the sample because neither exists for
        # a check that never evaluated — but "this run was reading a sample" is
        # still true of the read that failed, and it is often the explanation.
        sampling=sanitize_json(outcome.sampling),
    )


_EXPECTATION_KIND = "expectation"


@dataclass(frozen=True)
class OutcomePhase:
    """One unit of execution: the checks it resolved, paired with their outcomes.

    ``resolved`` carries ``(check index, outcome)`` pairs rather than two parallel
    lists, so the one arity check lives at the producer and no consumer has to
    re-establish that the two line up.

    ``publishable`` says whether this phase's result rows may be **committed as
    soon as they are built** (#318's incremental progress) — see
    `_run_outcome_phases` for the rule and why it is not merely an optimisation.
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
    """Run a suite's checks, dispatching by `check.kind` (ADR 0012), yielding each
    unit of execution as it resolves.

    **What a phase is, and why the granularity is uneven (#318).** A phase is the
    smallest group DataQ can resolve without re-doing work, which is a property of
    the engine underneath, not a choice made here:

    * ``comparison`` checks run through a per-check executor, so each is its own
      phase — genuine per-check increments;
    * every ``expectation`` is **one** phase, because GX validates a suite in a
      single atomic batch and there is no partial result to observe inside it;
    * scalar monitors (``freshness``/``volume``) are one phase, because
      `run_monitors` loads the frame once and evaluates the whole list against it
      — splitting them would re-read the object per monitor.

    So a suite of 30 expectations still resolves in one step. That is the honest
    shape of the underlying engines, and the consuming UI is built to say so
    (an elapsed-time heartbeat while nothing has resolved) rather than render a
    0% bar that reads as hung.

    **The stateful monitors are a second, independent limit: durability.** A phase
    is ``publishable`` only when running it wrote nothing outside ``results``.
    ``schema_drift``/``anomaly`` fail that test — their executors write
    ``monitor_baselines`` through the caller's session, so committing their result
    rows early would ALSO make those baseline writes durable. `monitor_baseline`'s
    contract is explicit that a rolled-back run strands nothing, and breaking it
    is not cosmetic: a failed run's observation would sit in the anomaly z-score
    window forever, a retry would double-count that window, and a first
    ``schema_drift`` capture from a run that never completed would become the
    reference every later run is diffed against.

    So they are yielded **last and unpublishable**: last because any later commit
    would flush them anyway (a commit is transaction-wide, not phase-scoped), and
    unpublishable so their rows ride the terminal commit with the baseline writes
    they belong to. The cost is that a stateful check no longer ticks the progress
    bar on its own — which is the right trade: an incremental progress bar is a
    convenience, and a baseline poisoned by a failed run is a wrong verdict on
    every subsequent run of that check.

    * ``expectation`` kind → the GX `CheckRunner.run_checks`.
    * scalar monitor kinds (``freshness``/``volume``) → `run_monitors` on a
      runner that advertises the kind (#429); an unsupported kind raises here,
      never silently mis-runs.
    * stateful monitor kinds (``schema_drift`` #592, ``anomaly`` #593) → the
      injected ``stateful_monitor_executor`` (the worker builds one via
      `stateful_monitors.build_stateful_monitor_executor`, which dispatches per
      kind — each engine owns the session and the baseline store, which runners
      must never see). A caller that supplies none gets a per-check operational
      ``error`` outcome (#122).
    * ``comparison`` → the injected ``comparison_executor`` (the worker builds
      one via `comparison_run.build_comparison_executor`, #794); same
      no-executor semantics.
    * a kind with no run path at all → `NotImplementedError` (unreachable via
      CRUD, which refuses to author one).

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

    # The two executor-driven kinds differ only in which callable runs them and
    # what the no-executor message says, so they share one loop (a copy-paste
    # twin is how the two drift). Comparison is read-only, so it publishes;
    # stateful writes baselines, so it does not — and it is deliberately yielded
    # LAST, see the docstring.
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
        # One phase, not len(expectation_idx): GX resolved them in a single atomic
        # batch, so there was never a moment where some were done and others were
        # not. `strict=True` keeps a runner returning the wrong arity loud.
        yield OutcomePhase(
            resolved=list(zip(expectation_idx, suite_outcome.checks, strict=True)),
            publishable=True,
        )
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
                "datasource (Snowflake / Unity Catalog / Iceberg / ADLS Gen2 / S3)"
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
        # One phase for the same reason as the expectation batch, but a different
        # cause: `run_monitors` loads the frame (or opens the connection) once and
        # evaluates the whole list against it.
        yield OutcomePhase(
            resolved=list(zip(monitor_idx, monitor_outcomes, strict=True)),
            publishable=True,
        )
    # LAST, and unpublishable: these write `monitor_baselines` through the
    # caller's session, so their rows must ride the terminal commit — see the
    # docstring. Anything yielded after them would flush those writes.
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
    """Run an injected per-check executor, or report its absence as a check-level
    operational ``error`` (#122) — never a raise, so siblings still run."""
    if executor is None:
        return CheckOutcome(
            expectation_type=check.expectation_type,
            success=False,
            errored=True,
            error_message=missing,
        )
    return executor(check)


def _cancelled_mid_run(session: Session, run: Run) -> bool:
    """Did a cancel commit (from the API session) while this run was executing?

    Reads the status **column** rather than ``session.refresh(run)``: under READ
    COMMITTED both see the API session's committed ``cancelled``, but a scalar
    SELECT of one column does not expire and re-load every attribute of the ORM
    object on a call made once per phase. It also cannot flush the caller's
    pending rows (``autoflush=False`` in db/session.py already prevents that, but
    a scalar read makes it structural), so they stay staged for the caller to
    either commit or discard.
    """
    if session.scalar(select(Run.status).where(Run.id == run.id)) != "cancelled":
        return False
    # Only now sync the ORM object — the caller returns it, and every consumer
    # reads `run.status` off it. Doing this on the common (not-cancelled) path
    # would be the per-phase re-load this scalar read exists to avoid.
    session.refresh(run)
    return True


def discard_run_results(session: Session, run_id: uuid.UUID) -> None:
    """Drop every result row a run has written — staged **and** committed.

    Incremental persistence (#318) means an earlier phase's rows are already
    committed by the time a later phase raises or the run is cancelled. Before
    #318 the single end-of-run commit made "a run that did not complete has no
    results" true for free; this restores it explicitly, so the run's own
    surfaces (run detail, its lineage event) do not show a half-populated set for
    a run that never completed.

    It is the run path's hygiene, **not** the safety net: the readers that
    aggregate across runs are status-aware (`rollup.AGGREGATABLE_RUN_STATUSES`),
    so a row this fails to delete — or one the stuck-run reaper strands, since it
    flips a status without owning the transaction that wrote them — is already
    excluded from every score, histogram and dedup signature. That is deliberate:
    a compensating DELETE that can itself fail is a poor place to put an
    invariant, and the DELETE most likely to fail is the one issued right after
    the DB error that failed the run.

    Best-effort for that reason: if it fails, the caller must still get to record
    the terminal status, which is the more important of the two writes — a
    stranded row is now inert, whereas a run stuck in ``running`` is an incident.
    """
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
    """Run ``checks`` against ``table`` via ``runner`` and persist the outcome.

    ``run`` must already be persisted (it carries the id the results link to).
    ``index_columns`` (the suite's identifier column, #415) is requested from GX so
    failing rows are captured with a locator; ``None`` keeps the scalar-only sample.
    Returns the same `Run`, updated to ``succeeded`` or ``failed``.

    **Publishable results are committed per execution phase (#318)**, not once at
    the end, so `get_run_progress` observes a check as resolved as soon as it
    genuinely is — per check for comparison kinds, per batch for the atomic GX
    expectation group and the shared-frame scalar monitors. The stateful monitor
    kinds are deliberately excluded: they write `monitor_baselines` through this
    same session, so publishing their rows early would make those writes durable
    on a run that later fails. See `_run_outcome_phases` for both limits.

    The *terminal* contract is unchanged: a run that ends ``failed`` or
    ``cancelled`` has **no** result rows. `discard_run_results` deletes whatever
    earlier phases committed, and — because that DELETE can itself fail, and the
    reaper flips a status without owning this transaction at all — the readers
    that aggregate across runs additionally ignore any run that is not
    ``succeeded`` (`rollup.AGGREGATABLE_RUN_STATUSES`).
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
    # `discard_run_results` clears every result row — staged and already-committed
    # — before we record the failure, so the terminal contract survives #318's
    # per-phase commits.
    #
    # Only a boolean and a count are carried across phases, never the outcomes
    # themselves: a `CheckOutcome` holds the failing-row sample, and retaining
    # every one of them for the whole run to feed one log line is exactly the
    # worker memory the #595 guardrail exists to protect.
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
            # Cooperative cancellation, checked before every commit: if a cancel
            # committed (from the API session) while this phase ran, don't
            # overwrite it with more results — drop this phase's staged rows AND
            # any an earlier phase committed, and leave the run 'cancelled'.
            if _cancelled_mid_run(session, run):
                _discard_results_if_any(session, run, published=published)
                log.info("run_cancelled_during_execution", run_id=str(run.id))
                return run
            if not phase.publishable:
                # Its rows stay staged and ride the terminal commit, together with
                # the `monitor_baselines` writes the phase made through this same
                # session (#318 G1). Committing here would make those durable on a
                # run that may still fail.
                continue
            # The commit that makes this phase visible to the progress poll.
            session.commit()
            published = True
        # A cancel can still land between the last commit and the terminal flip —
        # two transactions — and the flip below is itself guarded against one that
        # lands after this check.
        if _cancelled_mid_run(session, run):
            _discard_results_if_any(session, run, published=published)
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        if not _mark_succeeded(session, run):
            # Lost the race: a cancel committed between the check above and this
            # UPDATE. The conditional WHERE is what makes the user's confirmed
            # cancel authoritative instead of silently overwritten (#318 G4).
            _discard_results_if_any(session, run, published=published)
            session.refresh(run)
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
    except Exception as exc:
        # Drops the staged phase AND any earlier phase's committed rows, so a
        # non-succeeded run still carries no results (see `discard_run_results`).
        _discard_results_if_any(session, run, published=published)
        # Same cooperative check on the failure path: a run the user cancelled
        # mid-flight that *also* errored stays 'cancelled', not masked as 'failed'.
        if _cancelled_mid_run(session, run):
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        run.status = "failed"
        run.finished_at = _now()
        # Redaction-safe reason (#605/#595): the ONE shared policy — a
        # `SafeMonitorError` (a DataQ-authored message, e.g. the scan-cap refusal
        # naming the target and the knob) surfaces verbatim; everything else is
        # classified into a fixed message, so the raw text (which can carry
        # DSN/credential/cell fragments) stays in the server log below and never
        # reaches the persisted reason. Shared with the monitor loop and the
        # dry-run preview so the three sinks cannot drift.
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
    """Flip a still-``running`` run to ``succeeded``; return whether it won (#318 G4).

    A conditional ``UPDATE ... WHERE status = 'running'`` rather than an ORM
    attribute set, so a cancel that commits between the last `_cancelled_mid_run`
    check and this statement loses instead of being silently overwritten. The
    check-then-set shape cannot close that window at any distance — only the
    predicate travelling with the write can — and the run's own docstring states
    the terminal contract absolutely, so the window has to be closed rather than
    narrowed.

    ``failure_reason`` is cleared in the same statement: a slow-but-alive worker
    whose run was already reaped (failed + REAPED_REASON) can still finish and win
    here, and must not surface as succeeded-with-a-failure-reason (#605).
    """
    finished = _now()
    result = session.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == "running")
        .values(status="succeeded", finished_at=finished, failure_reason=None)
    )
    # `rowcount` is on `CursorResult`, which is what a DML `execute` always
    # returns; the declared `Result` supertype does not carry it.
    won = cast("CursorResult[Any]", result).rowcount == 1
    session.commit()
    if not won:
        return False
    # Mirror the write onto the in-memory object: the Core UPDATE bypassed the
    # ORM and callers read `run.status` straight after this returns. These are the
    # values we just wrote, so the two cannot diverge, and it saves the re-SELECT
    # a `refresh` would cost on the hot path.
    run.status = "succeeded"
    run.finished_at = finished
    run.failure_reason = None
    return True


def _discard_results_if_any(session: Session, run: Run, *, published: bool) -> None:
    """`discard_run_results`, skipped when no phase ever committed.

    The common failure — an expectation-only suite whose GX batch raises — has
    published nothing, so the staged rows die with the rollback and a DELETE +
    COMMIT round trip would be pure overhead on the path that is already
    handling an error.
    """
    if published:
        discard_run_results(session, run.id)
    else:
        session.rollback()


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


class RunFilterInvalidError(DataQError):
    status_code = 422
    code = "run_filter_invalid"


def validate_read_filters(status: str | None = None) -> None:
    """422 on a `/runs` filter value outside its closed vocabulary.

    Mirrors `orchestration_service.validate_read_filters` (#306) and the
    `/incidents` `state` gate: an unrecognised ``status`` used to flow straight
    into the `WHERE`, so `?status=succeded` (or the wrong-case `Succeeded` —
    the column stores lower-case) answered `200 []` with `X-Total-Count: 0`,
    indistinguishable from "no runs are in that status". That is the
    confidently-empty-answer class (#828) this codebase guards everywhere else.
    ``None`` means "no filter" and is left alone; only a *supplied* value is
    checked.
    """
    if status is not None and status not in RUN_STATUSES:
        raise RunFilterInvalidError(
            f"invalid run status {status!r}", detail={"allowed": list(RUN_STATUSES)}
        )


def _run_filters(
    *,
    user_id: uuid.UUID,
    suite_id: uuid.UUID | None,
    status: str | None,
    include_all: bool,
) -> list[Any]:
    """The ONE `WHERE` chain shared by :func:`list_runs` and :func:`count_runs`.

    Derived once rather than hand-rolled twice, so a future filter cannot land on
    the list without the total — which would make `X-Total-Count` quietly
    disagree with the page it describes (#1108)."""
    conditions: list[Any] = [
        Run.suite_id.in_(suite_service.accessible_suite_ids(user_id, include_all=include_all))
    ]
    if suite_id is not None:
        conditions.append(Run.suite_id == suite_id)
    if status is not None:
        conditions.append(Run.status == status)
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
) -> list[Run]:
    """Runs for suites the user can access, newest first (`created_at` desc,
    `id` desc tie-break — the same total-order paging shape `/pipeline_runs`
    and `/incidents` use, since `created_at` alone ties within one transaction).

    Optionally narrowed to one ``suite_id`` and/or a ``status``. The accessible
    subquery is always applied, so passing a ``suite_id`` the user can't see
    yields an empty list (the API layer 404s that case up front via
    `require_permission`, but the filter keeps the service safe on its own).
    ``include_all`` spans every suite — the workspace-admin view (ADR 0027).
    """
    stmt = (
        select(Run)
        .where(
            *_run_filters(
                user_id=user_id, suite_id=suite_id, status=status, include_all=include_all
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
) -> int:
    """Total runs matching the SAME visibility + filters as :func:`list_runs`,
    unaffected by its `limit`/`offset` (#1108 — the `/assets` `X-Total-Count`
    shape: `/runs` had `limit` only and could not be paged at all). Shares
    :func:`_run_filters` with the list so the two cannot drift."""
    stmt = (
        select(func.count())
        .select_from(Run)
        .where(
            *_run_filters(
                user_id=user_id, suite_id=suite_id, status=status, include_all=include_all
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

    ``complete_runs_only`` forwards to `rollup.status_histograms` and is for
    callers presenting these numbers as a **suite's** standing rather than as one
    named run's outcome — the asset view passes it, the runs table does not (a
    live run legitimately reads ``3 / 7`` there).

    ``checks_total``/``checks_passed`` count **evaluated** checks — the four
    severity tiers (pass/warn/fail/critical) — and **exclude** operational
    ``skip``/``error`` (#122), so the X/Y matches the run-detail page's "Checks
    passed" denominator and an all-skip run reports total 0 (rendered ``—``, not a
    misleading green ``0/N``).

    Lets the runs list surface a run's *data-quality* outcome — distinct from the
    run's *execution* status, which is ``succeeded`` even when checks failed."""
    out: dict[uuid.UUID, tuple[int, int, str | None]] = {}
    # A fold over the ONE shared histogram query (#889) — this used to build the
    # same `{status: count}` mapping itself and then discard it, which is exactly
    # the shape the health score needs.
    for run_id, by_status in status_histograms(
        session, run_ids, complete_runs_only=complete_runs_only
    ).items():
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
    """The result rows for a run, ordered by the **check**, not by the result.

    The result's own ``created_at`` used to be a fine proxy: every row in a run
    was inserted in one transaction, and Postgres' ``now()`` is transaction-start,
    so they shared one identical value and the ordering was really the physical
    row order. Per-phase commits (#318) give those timestamps real
    *execution*-order meaning, which would silently re-sort the run-detail table
    by engine — a `schema_drift` check authored second now runs last, a
    comparison check first.

    `CHECK_ORDER` is the shared key, applied here and by `get_run_progress` and
    `check_service.list_checks`, so all three lists agree row for row and are
    **deterministic**, which none of them were before.

    What that order *is*, honestly: `CHECK_ORDER`'s leading term is the check's
    ``created_at``, so it is authoring order only where the checks were authored
    at distinguishable times. A suite created by import or by the demo seed
    inserts every check in one transaction, so they tie and the stable-but-
    arbitrary ``id`` tie-break decides. That is a real limitation rather than a
    hidden one, and giving checks an explicit ordinal is the fix —
    [#1334](https://github.com/TheurgicDuke771/DataQ/issues/1334).

    ``outerjoin`` deliberately: results are cascade-deleted with their check
    today, so an orphan should not exist — but a read path must not silently drop
    rows to enforce an invariant it doesn't own.
    """
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
    must read this together with the run's lifecycle ``status``, not in isolation)."""

    check_id: uuid.UUID
    name: str
    status: str | None


@dataclass(frozen=True)
class RunProgress:
    """A run's live progress: lifecycle status + per-check resolution + a status
    histogram + how long it has been going, the compact shape the live-progress
    UI polls."""

    run: Run
    total_checks: int
    completed_checks: int
    counts: dict[str, int]
    checks: list[CheckProgress]
    #: Milliseconds since the run started — measured against the *server's* clock
    #: (``finished_at`` once terminal, otherwise now), never the browser's. It is
    #: computed here rather than left to the client because a skewed client clock
    #: renders a negative or hours-long elapsed time on a run that started
    #: seconds ago, and this number exists precisely to reassure someone that a
    #: run which has resolved nothing yet is nonetheless alive (#318). ``None``
    #: while the run is still queued (no ``started_at``).
    elapsed_ms: int | None
    #: Whether any UNRESOLVED check belongs to a kind that resolves as a group
    #: rather than one at a time (#318 G6). Everything except ``comparison`` does:
    #: GX validates its expectations in one atomic batch, `run_monitors` evaluates
    #: the scalar monitors against one loaded frame, and the stateful kinds ride
    #: the terminal commit with their baseline writes.
    #:
    #: It exists so the UI can explain a stalled-looking `0 / N` **from the run's
    #: actual composition** instead of inferring a reason from the count. Inferred
    #: copy was wrong on every clause for a monitor-only or comparison-first suite
    #: that is merely slow — asserting a mechanism the reader can't verify is the
    #: same confidently-wrong shape this whole issue is about.
    batched_pending: bool


def get_run_progress(session: Session, run: Run) -> RunProgress:
    """Assemble a run's progress from the suite's checks + the run's results.

    DB-driven (not Celery task state): the worker writes the ``run.status``
    lifecycle (queued → running → succeeded/failed/cancelled) and the per-check
    ``Result`` rows, so the DB is the source of truth and this composes with the
    same suite-scoped authz the rest of the read API uses.

    Each suite check maps to its result's status, or ``None`` while pending.
    Checks are taken from the *current* suite definition; a result is matched to
    its check by id.

    **How incremental this actually is (#318).** `execute_run` commits per
    execution phase, so a comparison check resolves here on its own. It cannot go
    finer than the engine underneath — a suite of GX expectations is one atomic
    batch, so it goes 0 → N in a single step however many checks it holds — nor
    finer than durability allows, which is why the stateful monitor kinds ride the
    terminal commit (they write `monitor_baselines`, see `_run_outcome_phases`).

    `elapsed_ms` + `batched_pending` are the honest signals for those stretches: a
    consumer should show that the run is alive, how long it has been going, and —
    only when the composition supports the claim — that the remaining checks
    report together. Restructuring the GX batch into per-check validations would
    buy real per-check increments at the cost of re-reading the dataset per check,
    and is deliberately not done.
    """
    checks = list(
        session.scalars(select(Check).where(Check.suite_id == run.suite_id).order_by(*CHECK_ORDER))
    )
    # One result per (run_id, check_id) in v1 (each run writes one row per check);
    # keyed by check_id to join against the suite's current checks.
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
    """Server-measured run duration in ms, or ``None`` while still queued.

    Clamped at zero: ``started_at`` is written by the worker and ``now()`` is read
    here in the API process, so a small clock difference between the two must
    surface as "0 ms so far", never as a negative age. Naive timestamps are read
    as UTC — everything DataQ writes is tz-aware, but a read model must not raise
    on a row that isn't.
    """
    if run.started_at is None:
        return None
    started = as_utc(run.started_at)
    end = as_utc(run.finished_at) if run.finished_at is not None else _now()
    return max(int((end - started).total_seconds() * 1000), 0)


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

# Capture-time full-population value-signal summary (#1230) — `VALUE_SIGNAL_SUMMARY_KEY`
# is shared with `gx_runner` (the writer) via `datasources.base`, so a future rename
# can't silently desync the two sides; consumed below via `_redact_row`'s
# ``summary_by_column`` (the reader). Internal DataQ-derived metadata, not a
# rendered sample: consumed to improve classification, then dropped from the
# redacted output rather than re-emitted or run back through the show/mask logic.


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
    *,
    value_signal_summary: Mapping[str, Any] | None = None,
) -> bool:
    """Whether a column is **known** sensitive — a governance tag (floor), an explicit
    override, or an *affirmative* name/value PII signal (not the conservative default).
    Gates the **tested** and **identifier** columns: those are shown *unless* known
    sensitive (seeing the failing value / locating the row is the point).

    ``value_signal_summary`` (#1230), when given, is the capture-time full-population
    counts summary for THIS column — preferred over deriving the value signal from
    the (possibly capped) ``values`` alone; see `column_classification._value_signal`.
    """
    return (
        _tag_sensitive(column, tags)
        or _policy_pii(column, policy)
        or is_sensitive(column, values, value_signal_summary=value_signal_summary)
    )


def _may_show_incidental(
    column: str,
    values: Sequence[Any],
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None,
    *,
    value_signal_summary: Mapping[str, Any] | None = None,
) -> bool:
    """Whether an *incidental* column (not the tested / identifier one) may be shown:
    only when it's affirmatively an IDENTIFIER or SAFE value — everything else
    default-masks (#415), so security can't regress. A governance tag / override-PII
    always masks; an override-named identifier shows **unless it is affirmatively PII**
    (a designated locator can't un-mask a column whose name/values are direct PII —
    e.g. an ``EMAIL`` set as identifier, or a natural key holding emails).

    ``value_signal_summary`` (#1230): see `_known_sensitive`.
    """
    if _tag_sensitive(column, tags) or _policy_pii(column, policy):
        return False
    if _policy_identifier(column, policy):
        return not is_sensitive(column, values, value_signal_summary=value_signal_summary)
    return (
        classify_column(column, list(values), value_signal_summary=value_signal_summary)
        is not ColumnClass.PII
    )


def _values_by_column(rows: Sequence[Any]) -> dict[str, list[Any]]:
    """Gather each column's values across the sampled failing rows, so the classifier's
    value signal (emails, id-shape) sees the whole column, not one cell."""
    out: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict):
            for col, val in row.items():
                out[str(col)].append(val)
    return dict(out)


# Tri-state redaction summary the read API surfaces alongside `sample_failures`
# (#424 — the header used to unconditionally claim "values redacted", which lies
# once #415 started letting non-PII columns through).
RedactionState = Literal["full", "partial", "none"]


@dataclass
class _RedactionTracker:
    """Accumulates per-column show/mask decisions across one `redact_sample_failures`
    call, so the caller can report an honest summary instead of re-sniffing the
    redacted output for the `"<redacted>"` sentinel (fragile — a genuine cell value
    equal to the sentinel would misreport, and it can't see counts).

    ``column_state[name]`` is "was this column ever SHOWN anywhere in the sample" —
    a column can appear in more than one bucket (e.g. two comparison buckets, which
    render as separate tables but are all visible together in one view); OR-ing keeps
    the summary matching what a viewer actually saw.
    ``anonymous_masked`` covers the one path with no column name to attribute
    to: a masked `partial_unexpected_list` with no ``tested_column`` context — still
    real masking, so it must not silently vanish from the summary.

    The OR is only honest across buckets that are **all** on screen. It is *not*
    across `unexpected_index_list` and `partial_unexpected_list`, which are two
    renderings of the same failing rows where the UI shows exactly one (#1190) —
    see `_displayed_sample_key` and #1197 for why only the displayed one is fed in.
    """

    column_state: dict[str, bool] = field(default_factory=dict)
    anonymous_masked: bool = False

    def record(self, column: str | None, shown: bool) -> None:
        if column:
            self.column_state[column] = self.column_state.get(column, False) or shown
        elif not shown:
            self.anonymous_masked = True

    def summary(self) -> tuple[RedactionState | None, list[str]]:
        """`(state, redacted_columns)`. ``state`` is ``None`` when nothing
        data-bearing was seen (e.g. only aggregate counts, or an empty sample) —
        there is nothing true to claim either way, so the caller should omit any
        redaction label rather than guess. ``redacted_columns`` lists columns that
        were masked everywhere they appeared (never shown).

        Scope (#1197): the summary describes the failing-row list the run-detail
        table **renders**, not the whole `sample_failures` payload. When GX populated
        both `unexpected_index_list` and `partial_unexpected_list` — two renderings
        of the same failing rows — only the displayed one (`_displayed_sample_key`)
        is fed in, so the label matches the cells on screen. The redacted payload
        still ships both lists, each masked on its own merits, so a consumer reading
        the *other* list must judge it from its own values rather than this label."""
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
    """Which failing-row list a viewer actually sees, or ``None`` if neither renders.

    `unexpected_index_list` and `partial_unexpected_list` are two renderings of the
    same failing rows and can both be present in one `sample_failures` payload, but
    the run-detail table shows exactly **one** of them. This mirrors the frontend's
    rule (`RunDetail.tsx`, #1190/#1183) byte for byte: a non-empty, all-dict
    `unexpected_index_list` wins because its rows already carry the configured
    identifier column; otherwise a list-shaped `partial_unexpected_list` is the
    fallback; otherwise nothing renders.

    Only the winner feeds the `_RedactionTracker` (#1197). Each list is classified
    independently against its **own** values, and `column_classification._value_signal`
    is ratio-based, so the same column can legitimately come out "masked" against one
    list's sample and "shown" against the other's. `_RedactionTracker.record` ORs
    toward "shown", so accumulating both let a column that reads ``"<redacted>"`` in
    every displayed cell drop out of `redacted_columns` — the label understating the
    masking on screen. That is the display-honesty class #424/#1115 exists to close;
    the fix is to track the list the viewer is looking at, not the union.

    The all-dict test runs over the `SAMPLE_ROW_CAP`-truncated list, because that is
    the list the frontend receives (#1196 re-applies the cap on every read) and so
    the only one it can run its own test over. Judging the *uncapped* list instead
    would invert this fix's own bug on a payload whose first `SAMPLE_ROW_CAP` entries
    are dicts but which carries a non-dict later on: the backend would suppress the
    tracker on exactly the list the UI is rendering. Unreachable through `gx_runner`
    today (`_is_identifier_index_list` drops mixed lists at capture), so this keeps
    the mirror exact rather than fixing a live divergence.
    """
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

    ``summary_by_column`` (#1230) is the capture-time `value_signal_summary` — a
    ``{column: counts}`` map computed over the FULL pre-cap population, keyed the
    same way as ``values_by_column``. When a column has an entry, it is preferred
    over deriving the value signal from ``values_by_column``'s (possibly capped)
    values; a column with no entry (old rows with no summary at all, or a column
    the summary omitted for having no non-null values) falls back unchanged.
    """
    if not isinstance(row, dict):
        if tracker is not None:
            # No column identity for a malformed/non-dict row shape — still a real
            # mask, so it must count as anonymous (#1115 review), not vanish from
            # the summary.
            tracker.record(None, False)
        return _redact_sample_value(row)
    # Case-insensitive tested-column match: GX returns the warehouse's column casing
    # (Snowflake upper-cases), which need not match the check config's `column`.
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


# The two interchangeable renderings of the same failing rows (#1190/#1197): GX can
# populate both in one `sample_failures`, but the run-detail table shows exactly one.
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
        if tracker is not None:
            # No column identity for a malformed/non-dict row shape — still a real
            # mask, so it must count as anonymous (#1115 review), not vanish from
            # the summary.
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


def redact_observed_value(
    observed: dict[str, Any] | None,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Redact a result's `observed_value` for the read API (#989, #1229).

    Three shapes reach here, each from a different failure/aggregate mode:

    * an errored check stores ``{"error": <message>}`` — the message gets
      `strip_statement_echo` (#1203). `_build_result` already strips before
      persisting, so a row written from now on arrives clean; this second pass is
      for the rows ALREADY in the database — every SQL-engine check that errored
      between ADR 0019 and #1203 persisted the statement and its bound parameters
      verbatim, and `observed_value` has no retention sweep to age them out.
      Deriving at read time corrects that history for free, the same way the
      #1115 redaction state does. The strip is idempotent, so applying it on both
      sides costs nothing.
    * a monitor that choked on a target cell adds ``{"unparsed_value": <cell>,
      "column": <name>}`` — the cell is masked under the same authority the
      failing-sample path uses: show it unless the column is **known** sensitive
      (a governance tag or an explicit policy entry). Deliberately the *known*
      test, not the default-mask one used for incidental columns: this cell is
      from the column the user pointed the monitor at, the analogue of
      `partial_unexpected_list`'s tested column — the diagnostic is the whole
      point, and masking it by default would make every "your timestamp column
      has junk in it" error unactionable.
    * a **set-oriented** expectation (`expect_column_distinct_values_to_be_in_set`
      and siblings) reports ``{"observed_value": [...]}`` as the full observed
      distinct-value set (#1229) — raw cell values from the **tested** column.
      Same *known*-sensitive authority as `unparsed_value` above (shown unless
      ``tested_column`` is known sensitive; no ``tested_column`` → masked, the
      same safe default `partial_unexpected_list` uses with no tested column).
      `gx_runner._bounded_observed_value` already caps this list to
      `SAMPLE_ROW_CAP` at capture (#1196's fix, applied to this adjacent column),
      but the bound is re-applied here too — same reasoning as
      `redact_sample_failures`'s read-time re-cap: a result persisted before the
      capture-time cap existed must not keep shipping an unbounded payload.
      Classification runs over the **full** persisted list (bounding what is
      emitted must never widen what is examined), only the emitted value is
      capped.

    This function is what the run-detail API, the MCP tools and the alert
    builder all call, so a fix here covers all three sinks at once.

    ``None``/absent keys pass through unchanged.
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
    raw_observed_value = observed.get("observed_value")
    if isinstance(raw_observed_value, list):
        show = tested_column is not None and not _known_sensitive(
            tested_column, raw_observed_value, policy, tags
        )
        capped = raw_observed_value[:SAMPLE_ROW_CAP]
        return {**observed, "observed_value": capped if show else _redact_sample_value(capped)}
    return observed


def redact_sample_failures(
    sample: dict[str, Any] | None,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
    tracker: _RedactionTracker | None = None,
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

    Every list-shaped entry is also bounded to `SAMPLE_ROW_CAP` rows (#1196) — the
    same cap capture applies — so a result persisted *before* that cap existed
    (GX's pandas engine returned `unexpected_index_list` untruncated) stops shipping
    an unbounded payload on every read. The aggregate counts are never touched, so
    the reported failure total stays the real one, and the show/mask classification
    still runs over the **full** persisted list (see the inline note below) — the cap
    bounds what is emitted, never what is examined.

    For a result written *since* #1196's capture-time cap, though, "full persisted
    list" is only 20 rows — the cap is applied before the row ever reaches the
    database, so there is no larger list here to fall back to. `gx_runner` now also
    persists a compact `value_signal_summary` sub-key (#1230) — per-column counts
    computed over the FULL pre-cap population — precisely to cover that gap: per
    `unexpected_index_list` row, `_redact_row` prefers that summary's counts over
    deriving the value signal from the capped rows, when present. A result with no
    summary key (written before #1230, or from a non-pandas engine that never had a
    full population to begin with) classifies exactly as it always has.

    ``tracker`` is an optional accumulator (#424) — pass a fresh `_RedactionTracker`
    to also learn *which* columns were shown vs masked, via `tracker.summary()`.
    Internal (leading underscore); external callers use
    `redact_sample_failures_with_state` instead of touching the tracker directly.
    """
    if not sample:
        return None
    index_rows = sample.get("unexpected_index_list")
    index_vbc = _values_by_column(index_rows) if isinstance(index_rows, list) else {}
    # The capture-time full-population summary (#1230), when present — a result
    # written before this existed simply has no such key, and `.get` returns `None`,
    # so every classification call below falls back to `index_vbc`'s (capped) values
    # exactly as it did before this change.
    raw_summary = sample.get(VALUE_SIGNAL_SUMMARY_KEY)
    summary_by_column = raw_summary if isinstance(raw_summary, dict) else None
    # Of the two interchangeable failing-row lists, only the one the viewer is shown
    # feeds the tracker (#1197) — see `_displayed_sample_key`. Redaction itself still
    # runs over BOTH: whichever list a reader gets must have its own cells masked
    # correctly, so this narrows only what the *label* is derived from.
    displayed_key = _displayed_sample_key(sample)
    out: dict[str, Any] = {}
    for key, raw_value in sample.items():
        if key == VALUE_SIGNAL_SUMMARY_KEY:
            # Internal capture-time metadata (#1230), already consumed above as
            # `summary_by_column` — never re-emitted, and not a data-bearing key
            # (it carries counts, not cell values), so it must not register with
            # the tracker either.
            continue
        # The tracker for THIS key: suppressed on the failing-row list that LOSES to
        # the other one, so `summary()` describes the table on screen rather than the
        # union of two renderings of the same rows. Deliberately narrow — it applies
        # only when a winner exists, i.e. only to the both-lists-present case #1197
        # describes. With one list (or neither renderable, `displayed_key is None`)
        # nothing is suppressed and the #1115 semantics stand unchanged. Comparison
        # buckets are a different surface (they render together), so they always
        # accumulate.
        #
        # The `displayed_key is None` case (neither list renders) keeps reporting on
        # the payload, which can leave the label describing rows the table does not
        # draw. Assessed and left as is: reaching it needs a list-shaped but non-dict
        # `unexpected_index_list` with no `partial_unexpected_list` beside it, and
        # `gx_runner._extract_sample_failures` drops exactly that shape at capture
        # (`_is_identifier_index_list`), so no result the GX path has ever written
        # can carry it — and the comparison path uses different keys entirely.
        key_tracker = (
            None
            if displayed_key is not None and key in _FAILING_ROW_LIST_KEYS and key != displayed_key
            else tracker
        )
        # Re-apply the sample bound at READ time (#1196). Capture-time capping only
        # protects rows written from here on; every result persisted BEFORE it — a
        # pandas-backed check that failed thousands of rows under GX's uncapped
        # `unexpected_index_list` — would otherwise keep shipping the whole list on
        # every run-detail load and MCP read until the retention sweep clears it.
        # Same read-time-derivation reasoning as the #1115 redaction state: old rows
        # are corrected for free, nothing to backfill.
        #
        # `value` is what we EMIT; every show/mask decision below still reads
        # `raw_value` — the full persisted list. That split is load-bearing, not
        # tidiness: `column_classification._value_signal` is *ratio*-based (emails
        # ≥50%, id-shape ≥80% with distinct-ratio ≥80%), so judging a 5,000-row
        # legacy sample on its first 20 rows can flip a column from PII to shown and
        # unmask real values that were masked before the cap existed. Bounding the
        # payload must never widen what the payload reveals.
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
                        # `summary_by_column` (#1230) is sourced from the sibling
                        # `unexpected_index_list`'s full pre-cap population, keyed by
                        # column name — the same population these dict rows are drawn
                        # from, so it's valid evidence here too. Without this, a
                        # column judged PII via the summary on the other list stays
                        # unmasked here, reappearing through the sibling field.
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
                # Same reasoning as above: the summary describes the COLUMN, not
                # `partial_unexpected_list`'s own (always-capped-by-GX) contents, so
                # it's valid evidence for the tested column's scalar values too.
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
            # Comparison buckets (ADR 0015, #794): rows carry `<col>_src` /
            # `<col>_tgt` pairs plus unsuffixed key columns. Policy/classifier
            # matching runs on the SUFFIX-STRIPPED name so a `pii_columns`
            # entry like `email` masks both sides — while unknown columns keep
            # the default-mask posture.
            vbc = _values_by_column(raw_value)
            out[key] = [
                _redact_comparison_row(
                    row, policy=policy, tags=tags, values_by_column=vbc, tracker=tracker
                )
                for row in value
            ]
        else:
            # An unrecognized key is treated as data and fully masked (default-mask
            # posture) — but it has no column identity to attribute the mask to, so
            # it must still register as an anonymous mask (#1115 review): otherwise
            # `summary()` under-reports this masking as "nothing happened" instead
            # of "full"/"partial", the mirror image of the bug #424 fixed.
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
    """`redact_sample_failures` plus an honest redaction summary (#424).

    Returns ``(redacted_sample, state, redacted_columns)``:

    * ``state`` — ``"full"`` when every data-bearing column was masked, ``"none"``
      when every one was shown, ``"partial"`` when it's a mix, or ``None`` when the
      sample carried no data-bearing content at all (only aggregate counts, or
      empty) — there is nothing true to claim either way.
    * ``redacted_columns`` — the columns masked everywhere they appeared.

    Both describe the failing-row list the run-detail table renders, not the whole
    payload — see `_RedactionTracker.summary` and `_displayed_sample_key` (#1197).

    Redaction happens at **read time** (this is called from the API route on every
    GET, not from the run/write path), so it derives correctly for old, already
    persisted results too — there is nothing to backfill.
    """
    tracker = _RedactionTracker()
    redacted = redact_sample_failures(
        sample, tested_column=tested_column, policy=policy, tags=tags, tracker=tracker
    )
    state, redacted_columns = tracker.summary()
    return redacted, state, redacted_columns


def _is_safe_summary(key: str, value: Any) -> bool:
    """A passthrough-safe aggregate: an allowlisted key whose value is a plain
    number (``bool`` excluded — it's an ``int`` subclass but not a count)."""
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
    """Chunked UPDATEs against `results`, scoped to `created_at < cutoff` plus
    the caller's own column-specific guard (#1253 — shared by both halves of
    `purge_expired_sample_failures` so the two purges stay mechanically
    identical). Returns the total affected-row count across all chunks
    (`chunked_dml` — see its docstring for the loop/termination/EPQ contract).

    Fire-and-forget bulk DML on a fresh, short-lived worker session with no
    loaded Result identities — `synchronize_session=False` skips the ORM
    identity-map sync, which under the default 'auto'/'fetch' would emit an
    extra SELECT of every matching PK before the UPDATE (every caller's WHERE
    uses `jsonb_typeof`, so the in-Python 'evaluate' strategy can't apply
    anyway). Deliberately still two separate UPDATEs rather than one combined
    pass with per-column CASE/SET expressions: a single UPDATE's rowcount
    would tell us how many ROWS matched `cond1 OR cond2`, not how many
    *column values* were scrubbed on each side — the per-column visibility
    `purge_expired_sample_failures` reports (and logs) needs the two counts
    kept apart. Each caller's `extra_where` is deliberately built to be
    textually identical to its matching `#323` partial index predicate
    (`ix_results_unpurged_created` for `sample_failures`,
    `ix_results_unpurged_observed` for `observed_value`) so the planner can
    prove the query implies the index and use it — see the migration
    docstring for why this textual match is load-bearing, not cosmetic.

    The candidate subquery repeats `created_at < cutoff` on the OUTER
    UPDATE's own WHERE too (not just via `id IN (subquery)`) — #323 review
    finding F5: under READ COMMITTED, a row a concurrent transaction is
    updating gets EPQ (evaluate-plan-qual) rechecked against the UPDATE's own
    WHERE, and `id IN (subquery)` alone doesn't re-validate the cutoff/guard,
    only the fixed id membership — which could re-stamp `sample_failures_
    purged_at` with a later timestamp on a row an overlapping sweep already
    excluded. Ordered oldest-first (`ORDER BY created_at`, #323 review M2):
    free given the index is on `created_at`, it's the right order for a
    PII-retention sweep, and it removes a deadlock footgun if the
    single-embedded-beat assumption (today: at most one sweep in flight)
    ever changes.
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
    """Scrub `sample_failures` and list-shaped `observed_value` from results
    older than ``retention_days``.

    ``sample_failures`` and ``observed_value`` are the two result columns that
    can carry real (possibly PII-bearing) cell values: ``sample_failures`` from
    a failing-row sample, ``observed_value`` from a **set-oriented** expectation
    (`expect_column_distinct_values_to_be_in_set` and siblings) reporting its
    full observed distinct-value list (#1229/#1252). After the retention window
    both are nulled out (to a true SQL NULL) — ``sample_failures_purged_at`` is
    stamped so that half is auditable (see below for why ``observed_value``
    doesn't need its own stamp). The result row itself — and crucially
    ``metric_value`` — is **kept**, so dashboard trends / anomaly baselines
    survive the purge (ADR 0012); this is a PII-minimisation sweep, not a
    run-history delete. Returns the total number of column values scrubbed
    across both columns (a row whose ``sample_failures`` AND ``observed_value``
    are both scrubbed counts twice — they're independent UPDATEs over
    independent conditions, not one row-count).

    Only rows that actually hold a sample *object* are touched: the JSONB column
    stores Python ``None`` as SQL NULL (``JSONB(none_as_null=True)`` since #909),
    but rows written before that fix — or by any future writer that regresses
    it — could still carry a literal JSON ``'null'``, so the guard checks for
    both. ``jsonb_typeof`` excludes SQL NULL (→ NULL) and JSON ``'null'`` (→
    ``'null'``) alike, leaving only real ``object``/``array`` samples. Naturally
    idempotent (a scrubbed row is SQL NULL → typeof NULL → excluded); the
    ``purged_at IS NULL`` guard makes that intent explicit.

    ``observed_value``'s sweep (#1253) uses the identical scalar-vs-list
    distinction `redact_observed_value` already draws at read time: only a
    literal ``{"observed_value": [...]}`` shape — the one
    `gx_runner._bounded_observed_value` produces for set-oriented expectations
    — has an *array* at that JSON path. Every other shape this column takes
    (a scalar aggregate — a row count, a mean — from an ordinary expectation or
    any monitor kind's own payload; ``{"error": ...}``; ``{"unparsed_value":
    ..., "column": ...}``; ``{"reason": ...}`` for a skip) either has no
    ``observed_value`` key at all or a non-list value there, so
    ``jsonb_typeof(observed_value -> 'observed_value') = 'array'`` isolates
    exactly the PII-bearing case and leaves every scalar metric untouched — the
    thing this sweep must never destroy (a scalar `observed_value` is presumed
    non-PII and `metric_value` is the durable trend/anomaly-baseline mirror of
    it, ADR 0012).

    No dedicated ``observed_value_purged_at`` column (no migration needed):
    nulling the *whole* column makes ``observed_value -> 'observed_value'``
    itself SQL NULL on the next sweep, so the same typeof check is naturally
    idempotent without a stamp — unlike ``sample_failures``, which still needs
    ``sample_failures_purged_at`` because a row that starts with no sample at
    all (JSON/SQL null) is deliberately never touched, so nulling alone can't
    distinguish "already purged" from "never had one" for that column.

    ``retention_days <= 0`` disables the sweep (returns 0 without touching the DB)
    — a clean off-switch rather than purging everything. The cutoff is anchored on
    ``Result.created_at`` (when the result landed ≈ when the run completed).

    Each column's sweep runs as bounded, individually-committed ``chunk_size``
    batches rather than one unbounded UPDATE (#323) — see `_purge_column`'s
    docstring for why, and each has its own supporting partial index
    (`ix_results_unpurged_created` / `ix_results_unpurged_observed`) so
    neither half full-scans `results` per batch. Steady state (a day's worth
    of newly-expired rows) is one or two batches, so this is unobservable
    there; it only matters for a first-run or post-outage catch-up over a
    large backlog.

    Logs (and, on an exception, still logs — #323 review M1) the running
    per-column totals via `on_batch`, not just the two functions' return
    values: without this, a crash partway through either column's batch loop
    would leave every already-committed purge in this call with no
    accounting anywhere — the totals this function would otherwise return
    are lost along with the exception, but the DB writes themselves are not
    (each batch commits independently).
    """
    if retention_days <= 0:
        return 0
    moment = now or _now()
    cutoff = moment - timedelta(days=retention_days)

    sample_progress = 0
    observed_progress = 0

    def _on_sample_batch(n: int) -> None:
        nonlocal sample_progress
        sample_progress += n

    def _on_observed_batch(n: int) -> None:
        nonlocal observed_progress
        observed_progress += n

    def _log_progress() -> None:
        # `purged` names the historical (sample_failures-only) field for anyone
        # already keying an alert/dashboard off this event's shape; the new
        # per-column + total fields disambiguate the now-two-column sweep so
        # nothing downstream has to infer which column moved a count.
        log.info(
            "sample_failures_purged",
            purged=sample_progress,
            sample_failures_purged=sample_progress,
            observed_value_purged=observed_progress,
            total_purged=sample_progress + observed_progress,
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

        # #1253: observed_value's sibling half of the same PII-minimisation gap —
        # see the docstring above for why only the list-shaped case is touched
        # and why no *_purged_at stamp/migration is needed here.
        observed_inner_typeof = func.jsonb_typeof(Result.observed_value["observed_value"])
        _purge_column(
            session,
            cutoff=cutoff,
            extra_where=[observed_inner_typeof == "array"],
            values={"observed_value": null()},
            chunk_size=chunk_size,
            on_batch=_on_observed_batch,
        )
    finally:
        _log_progress()

    return sample_progress + observed_progress


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
    # Runs that had begun executing may hold result rows an incremental phase
    # committed (#318). The aggregate readers already ignore them
    # (`rollup.AGGREGATABLE_RUN_STATUSES`), so this is hygiene rather than the
    # invariant — but a reaped run's OWN detail page would otherwise show a
    # half-populated set for a run that never completed, and the terminal lineage
    # event below would carry it.
    discard_ids = [run.id for run in stuck if run.status == "running"]
    # A `running` run emitted an OpenLineage START (the worker got that far before
    # dying); a `queued` one never did. Capture the started set before the flip so
    # the post-commit terminal emit only fires for runs that actually opened a run
    # in Marquez — a queued reap gets none (there's no dangling START to close).
    started_ids = list(discard_ids)
    for run in stuck:
        # Canonical terminal-failed shape, one shared `moment` across the batch.
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
        # Close the dangling START for each reaped-`running` run with a terminal FAIL
        # (ADR 0034, #758) — a worker death otherwise leaves a permanently-RUNNING run
        # in Marquez. Emitted per-run after the status flip commits (so the event maps
        # to FAIL). Lazy import breaks the lineage↔run_service cycle; dark-by-default +
        # fail-open (dispatch never raises), so this is a no-op when emission is off.
        from backend.app.lineage import dispatch as lineage_dispatch

        for run_id in started_ids:
            lineage_dispatch.emit_run_lineage_terminal(session, run_id=run_id)
    return stuck


def fail_run_worker_lost(
    session: Session, *, run_id: uuid.UUID, now: datetime | None = None
) -> bool:
    """Drive a single run to ``failed`` after its worker process died (#755).

    Called from the **parent** worker process via Celery's ``task_failure`` signal.
    That indirection is the whole point: when the OOM killer SIGKILLs a prefork
    child mid-``run_suite``, no code inside the task runs — not the ``except``, not
    the ``finally`` — so the run row can only be closed by something that outlived
    the child.

    Without this the run sits ``running`` until :func:`reap_stuck_runs` catches it
    ``stuck_run_threshold_minutes`` later (default **60**), with a reason that says
    only "did not complete in time". Here Celery has already told us the child was
    lost, so the failure is immediate and the reason names memory as the likely
    cause instead of leaving the user to guess.

    Idempotent and non-clobbering: a run that already reached a terminal status is
    left alone and ``False`` is returned. This matters because the signal can fire
    for a task whose run was cancelled, or which finished and died afterwards —
    neither should be rewritten to ``failed``.
    """
    run = session.get(Run, run_id)
    if run is None or run.status not in _NON_TERMINAL_STATUSES:
        return False
    moment = now or _now()
    was_running = run.status == "running"
    run_dispatch.mark_dispatch_failed(run, at=moment, reason=run_dispatch.WORKER_LOST_REASON)
    session.commit()
    if was_running:
        # The SIGKILLed child ran no `except` and no `finally`, so nothing cleared
        # the phases it had already committed (#318). Same reasoning as the
        # reaper: the aggregates already exclude them, this keeps the run's own
        # surfaces honest.
        discard_run_results(session, run_id)
    log.warning("run_failed_worker_lost", run_id=str(run_id), was_running=was_running)
    # Same contract as the reaper: a `running` run emitted an OpenLineage START that
    # would otherwise dangle forever; a `queued` one never did. Lazy import breaks
    # the lineage<->run_service cycle; fail-open and dark by default.
    if was_running:
        from backend.app.lineage import dispatch as lineage_dispatch

        lineage_dispatch.emit_run_lineage_terminal(session, run_id=run_id)
    return True
