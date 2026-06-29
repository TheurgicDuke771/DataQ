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
from collections.abc import Sequence
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
from backend.app.datasources.monitors import MONITOR_KINDS
from backend.app.db.models import RESULT_STATUSES, Check, Result, Run
from backend.app.services import suite_service
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
    runner: CheckRunner, *, table: str, schema: str | None, checks: list[Check]
) -> list[CheckOutcome]:
    """Run a suite's checks, dispatching by `check.kind` (ADR 0012), and return one
    outcome per check in the **same order** (so they zip 1:1 onto result rows).

    * ``expectation`` kind → the GX `CheckRunner.run_checks`.
    * ``freshness``/``volume`` (monitor kinds) → the `MonitorRunner.run_monitors`
      SQL path — only when the runner is a SQL datasource (Snowflake/UC). A monitor
      check on a flat-file runner raises (gated here, not silently mis-run).
    * any other reserved kind (`schema_drift`/`anomaly`/`comparison`) has no runner
      yet → `NotImplementedError`.

    This composes with the connection-type runner selection (ADR 0011): `kind`
    chooses the *monitor*, `connection.type` chose the *adapter* (the runner)."""
    expectation_idx = [i for i, c in enumerate(checks) if c.kind == _EXPECTATION_KIND]
    monitor_idx = [i for i, c in enumerate(checks) if c.kind in MONITOR_KINDS]
    unsupported = sorted(
        {c.kind for c in checks if c.kind != _EXPECTATION_KIND and c.kind not in MONITOR_KINDS}
    )
    if unsupported:
        raise NotImplementedError(f"no run path for check kind(s) {', '.join(unsupported)}")

    outcomes: list[CheckOutcome | None] = [None] * len(checks)
    if expectation_idx:
        specs = [
            CheckSpec(expectation_type=checks[i].expectation_type, kwargs=dict(checks[i].config))
            for i in expectation_idx
        ]
        suite_outcome = runner.run_checks(table=table, schema=schema, checks=specs)
        for i, oc in zip(expectation_idx, suite_outcome.checks, strict=True):
            outcomes[i] = oc
    if monitor_idx:
        if not isinstance(runner, MonitorRunner):
            raise NotImplementedError(
                f"{type(runner).__name__} does not support monitor checks — "
                "freshness/volume need a SQL datasource (Snowflake / Unity Catalog)"
            )
        monitors = [
            MonitorSpec(kind=checks[i].kind, config=dict(checks[i].config)) for i in monitor_idx
        ]
        monitor_outcomes = runner.run_monitors(table=table, schema=schema, monitors=monitors)
        for i, oc in zip(monitor_idx, monitor_outcomes, strict=True):
            outcomes[i] = oc

    # Every index is filled: expectation_idx + monitor_idx together cover all checks
    # once the unsupported-kind guard above has run.
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
) -> Run:
    """Run ``checks`` against ``table`` via ``runner`` and persist the outcome.

    ``run`` must already be persisted (it carries the id the results link to).
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
        outcomes = _run_outcomes(runner, table=table, schema=schema, checks=checks)
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
        session.commit()
    except Exception:
        session.rollback()
        # Same cooperative check on the failure path: a run the user cancelled
        # mid-flight that *also* errored stays 'cancelled', not masked as 'failed'.
        if _cancelled_mid_run(session, run):
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        run.status = "failed"
        run.finished_at = _now()
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
) -> list[Run]:
    """Runs for suites the user can access, newest first (`created_at` desc).

    Optionally narrowed to one ``suite_id`` and/or a ``status``. The accessible
    subquery is always applied, so passing a ``suite_id`` the user can't see
    yields an empty list (the API layer 404s that case up front via
    `require_permission`, but the filter keeps the service safe on its own).
    """
    accessible = suite_service.accessible_suite_ids(user_id)
    stmt = (
        select(Run).where(Run.suite_id.in_(accessible)).order_by(Run.created_at.desc()).limit(limit)
    )
    if suite_id is not None:
        stmt = stmt.where(Run.suite_id == suite_id)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    return list(session.scalars(stmt))


# Severity tiers (ADR 0005), worst last — for the "worst check outcome" a run
# carries. Operational statuses (skip/error) aren't failures, so they don't rank.
_SEVERITY_RANK: dict[str, int] = {"warn": 1, "fail": 2, "critical": 3}


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
        worst, worst_rank = None, 0
        for tier, rank in _SEVERITY_RANK.items():
            if by_status.get(tier) and rank > worst_rank:
                worst, worst_rank = tier, rank
        # Evaluated checks only: pass + the three failing tiers (skip/error excluded).
        total = passed + sum(by_status.get(tier, 0) for tier in _SEVERITY_RANK)
        out[run_id] = (total, passed, worst)
    return out


def get_run(session: Session, run_id: uuid.UUID) -> Run | None:
    """Fetch a run by id (no authz — the API layer gates on the run's suite)."""
    return session.get(Run, run_id)


_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


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


# Column-name tokens that mark a column as PII for sample redaction — the
# name-heuristic layer below datasource classification/tags (#415). A column
# matches if its name *contains* any token (so `customer_email`, `home_address`
# match). The authoritative layers (warehouse classification, the suite policy's
# explicit `pii_columns`) sit above this; unclassified columns still default-mask.
_PII_COLUMN_TOKENS = frozenset(
    {
        "email",
        "phone",
        "mobile",
        "ssn",
        "passport",
        "name",
        "address",
        "street",
        "city",
        "zip",
        "postal",
        "dob",
        "birth",
        "credit",
        "card",
        "iban",
        "account_number",
    }
)


def _is_pii_column(column: str, policy: dict[str, Any] | None) -> bool:
    """Whether a column must be masked in a sample: explicitly in the suite
    policy's ``pii_columns``, or matched by the column-name heuristic."""
    name = column.strip().lower()
    if policy:
        listed = {str(c).strip().lower() for c in (policy.get("pii_columns") or [])}
        if name in listed:
            return True
    return any(token in name for token in _PII_COLUMN_TOKENS)


def _safe_sample_columns(tested_column: str | None, policy: dict[str, Any] | None) -> set[str]:
    """Columns whose raw values may be surfaced in a sample: the tested column +
    the suite policy's identifier column, each only if **not** PII (policy
    ``pii_columns`` / name heuristic). Empty when nothing is classified → the
    blanket mask (default-redact, so security never regresses)."""
    safe: set[str] = set()
    if tested_column and not _is_pii_column(tested_column, policy):
        safe.add(tested_column)
    identifier = (policy or {}).get("identifier_column")
    if identifier and not _is_pii_column(str(identifier), policy):
        safe.add(str(identifier))
    return safe


def _redact_row(row: Any, safe_columns: set[str]) -> Any:
    """Mask a failing-row dict per-column: **default-redact** — a column's value
    is kept only when it's in ``safe_columns`` (the identifier + tested column),
    every other column (PII or unclassified) is masked. Non-dict rows fall back to
    full masking."""
    if not isinstance(row, dict):
        return _redact_sample_value(row)
    return {
        col: (val if str(col) in safe_columns else _redact_sample_value(val))
        for col, val in row.items()
    }


def redact_sample_failures(
    sample: dict[str, Any] | None,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Redact a result's `sample_failures` for safe surfacing on the read API.

    `sample_failures` carries aggregate counts plus `partial_unexpected_list` —
    the failing values of the **tested column** — and (when a future runner records
    it) an `unexpected_index_list` of failing rows. Suite-level ``view`` authz lets
    share-recipients read a suite's results, so PII must not cross that boundary
    unredacted (CLAUDE.md PII rule; purged on the retention sweep below).

    Column-aware policy (#415) — surgical, not blanket:
    * numeric summary keys (`unexpected_count` / `unexpected_percent`) always pass;
    * `partial_unexpected_list` (the tested column's failing values) passes when
      ``tested_column`` is **not** PII — per the suite ``policy.pii_columns`` or the
      name heuristic — so a non-PII breach (e.g. a bad ``LINE_TOTAL``) is finally
      *visible*, while a PII tested column (``email``) stays masked;
    * `unexpected_index_list` row-dicts are masked **per column** (identifier +
      non-PII shown, PII masked);
    * everything else default-masks (unclassified → redacted, so security can't
      regress).

    With neither ``tested_column`` nor ``policy`` the legacy **blanket mask** is
    kept (the safe default for callers that don't know the column) — only the
    summary keys survive. ``None`` sample passes through unchanged.
    """
    if not sample:
        return None
    safe = _safe_sample_columns(tested_column, policy)
    show_tested = tested_column is not None and tested_column in safe
    out: dict[str, Any] = {}
    for key, value in sample.items():
        if _is_safe_summary(key, value):
            out[key] = value
        elif key == "partial_unexpected_list" and show_tested:
            out[key] = value  # the tested column's failing values — non-PII, surfaced
        elif key == "unexpected_index_list" and isinstance(value, list):
            out[key] = [_redact_row(row, safe) for row in value]
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
