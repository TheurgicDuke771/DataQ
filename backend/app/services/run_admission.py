"""Admission control: what a run will materialise, and whether the worker has room (#1998).

`RUN_MAX_SCAN_BYTES` / `RUN_MAX_SCAN_ROWS` bound ONE run's read. Nothing bounded the SUM
across the prefork children of one worker container, which is the measured failure mode:
four overlapping 1M-row flat-file runs want ~3.1 GiB of child resident memory against a
2 GiB worker (docs/site/architecture/perf-baseline.md).

A run reserves its estimate before materialising anything, and the estimate comes from the
size probe the read path already has to do — so nothing is guessed from the data. Lanes that
push work down to the warehouse hold no dataset in the worker and bypass admission entirely.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.memory_budget import Admission, MemoryBudget, get_memory_budget
from backend.app.core.secrets import get_secret_store
from backend.app.datasources.base import ResolvedTarget
from backend.app.db.models import Check, Connection, Run, Suite
from backend.app.services import run_target

log = get_logger(__name__)

#: `runs.queued_reason` while a run is waiting for worker memory. A queued run is otherwise
#: waiting on the broker, which is a different thing to tell a user (or an LLM).
AWAITING_MEMORY = "awaiting_worker_memory"

#: Datasource types that push their work down and hold no dataset in the worker. A bypass
#: here is a claim about the runner, not a gap — see `_estimate` for the unmetered case.
PUSHDOWN_TYPES: frozenset[str] = frozenset({"snowflake"})


@dataclass(frozen=True)
class MemoryEstimate:
    """How much resident memory a run is expected to want, and on what evidence.

    ``exclusive`` means the size is not knowable up front, so the run may only start when
    nothing else holds budget — never "admit and hope".
    """

    bytes: int
    basis: str
    exclusive: bool = False


def _settings_expansion(path: str) -> float:
    settings = get_settings()
    lowered = path.lower()
    if lowered.endswith(".parquet"):
        return settings.run_admission_expansion_parquet
    if lowered.endswith((".csv", ".tsv", ".txt")):
        return settings.run_admission_expansion_csv
    return settings.run_admission_expansion_default


def _sample_bytes(target: ResolvedTarget) -> int | None:
    """What a sampled read materialises — far less than the object, so it must not be
    throttled as if it were a full one.
    """
    if target.sampling is None:
        return None
    return target.sampling.rows * get_settings().run_admission_row_bytes


def _flat_file_estimate(connection: Connection, target: ResolvedTarget) -> MemoryEstimate | None:
    from backend.app.datasources.flatfile import file_stat

    if not connection.secret_ref:
        return None
    sampled = _sample_bytes(target)
    if sampled is not None:
        # A sample is bounded by the sample whatever the object is, so it needs no probe at all.
        return MemoryEstimate(bytes=sampled, basis="flat_file_sample")
    if target.batch is not None:
        # Resolving a batch target means LISTING the store, which the run path does again
        # moments later — paying for it twice, on every re-queue, is exactly the cost
        # admission exists to avoid. The scan cap is the bound that read is guaranteed to
        # respect, so bound the estimate by the cap rather than by a second listing.
        cap = get_settings().run_max_scan_bytes
        if cap <= 0:
            return MemoryEstimate(bytes=0, basis="flat_file_batch_uncapped", exclusive=True)
        return MemoryEstimate(
            bytes=int(cap * get_settings().run_admission_expansion_default),
            basis="flat_file_batch_cap",
        )
    # A non-batch flat-file target carries the concrete path already, so this is one metadata
    # call and no listing.
    path = target.table
    stat = file_stat(
        conn_type=connection.type,
        config=dict(connection.config),
        path=path,
        secret=get_secret_store().get(connection.secret_ref),
    )
    if stat.size is None:
        # The store would not say. Treat it as unknown, not as zero.
        return MemoryEstimate(bytes=0, basis="flat_file_size_unknown", exclusive=True)
    return MemoryEstimate(bytes=int(stat.size * _settings_expansion(path)), basis="flat_file_size")


def _unity_catalog_estimate(
    connection: Connection, target: ResolvedTarget, checks: list[Check]
) -> MemoryEstimate | None:
    from backend.app.datasources.unity_catalog import frame_lane_required

    sampled = _sample_bytes(target)
    if sampled is not None:
        return MemoryEstimate(bytes=sampled, basis="uc_sample")
    types = [c.expectation_type for c in checks if c.kind == "expectation"]
    if not frame_lane_required(types):
        return None
    row_cap = get_settings().run_max_scan_rows
    if row_cap <= 0:
        # The row cap is what bounds this lane; without it the frame is unbounded.
        return MemoryEstimate(bytes=0, basis="uc_frame_unbounded", exclusive=True)
    return MemoryEstimate(
        bytes=row_cap * get_settings().run_admission_row_bytes, basis="uc_frame_row_cap"
    )


def estimate_run_memory(session: Session, run: Run) -> MemoryEstimate | None:
    """What this run will hold in the worker, or ``None`` when admission does not apply.

    ``None`` covers three cases, all deliberate: a pushdown lane (holds nothing), a run whose
    graph will not execute anyway (``_run_suite`` produces the real error), and a datasource
    with no estimator yet — Iceberg, whose cheap `scan().count()` probe lands separately. The
    last one is logged, so an unmetered materialising runner is visible rather than silent.
    """
    suite = session.get(Suite, run.suite_id)
    connection = session.get(Connection, suite.connection_id) if suite is not None else None
    if suite is None or connection is None:
        return None
    if connection.type in PUSHDOWN_TYPES:
        return None
    try:
        target = run_target.resolve_target(connection.type, suite.target)
    except Exception:
        return None
    checks = list(session.scalars(select(Check).where(Check.suite_id == suite.id)))
    try:
        if connection.type in {"adls_gen2", "s3"}:
            return _flat_file_estimate(connection, target)
        if connection.type == "unity_catalog":
            return _unity_catalog_estimate(connection, target, checks)
    except Exception:
        # A probe failure must not fail the run — the read path raises its own classified
        # error moments later, with a better message than anything available here.
        log.warning("run_admission_estimate_failed", run_id=str(run.id), exc_info=True)
        return None
    log.info(
        "run_admission_no_estimator",
        run_id=str(run.id),
        connection_type=connection.type,
    )
    return None


@dataclass(frozen=True)
class AdmissionDecision:
    """What the caller should do next with a run."""

    #: Re-queue and try again — the worker has no room right now.
    defer: bool
    #: Admitted only because the wait budget ran out, not because room appeared.
    timed_out: bool = False


def _reserve(budget: MemoryBudget, run_id: uuid.UUID, estimate: MemoryEstimate) -> Admission:
    amount = budget.budget_bytes if estimate.exclusive else estimate.bytes
    return budget.reserve(str(run_id), amount)


def admit(
    session: Session, *, run: Run, estimate: MemoryEstimate | None, waited_out: bool
) -> AdmissionDecision:
    """Try to claim room for ``run``. Never fails the run: it defers, or it admits."""
    if estimate is None:
        return AdmissionDecision(defer=False)
    budget = get_memory_budget()
    if budget is None:
        return AdmissionDecision(defer=False)
    outcome = _reserve(budget, run.id, estimate)
    if outcome.admitted:
        log.info(
            "run_admission_granted",
            run_id=str(run.id),
            estimate_bytes=estimate.bytes,
            basis=estimate.basis,
            exclusive=estimate.exclusive,
            used_bytes=outcome.used_bytes,
            degraded=outcome.degraded,
        )
        return AdmissionDecision(defer=False)
    if waited_out:
        log.warning(
            "run_admission_timeout",
            run_id=str(run.id),
            estimate_bytes=estimate.bytes,
            basis=estimate.basis,
            used_bytes=outcome.used_bytes,
        )
        return AdmissionDecision(defer=False, timed_out=True)
    log.info(
        "run_admission_deferred",
        run_id=str(run.id),
        estimate_bytes=estimate.bytes,
        basis=estimate.basis,
        used_bytes=outcome.used_bytes,
    )
    return AdmissionDecision(defer=True)


def release(run_id: uuid.UUID) -> None:
    """Hand a run's reservation back. Safe to call when nothing was reserved."""
    budget = get_memory_budget()
    if budget is not None:
        budget.release(str(run_id))


def set_queued_reason(session: Session, *, run: Run, reason: str | None) -> None:
    """Record WHY a run is sitting in ``queued`` — or clear it once it is not."""
    if run.queued_reason == reason:
        return
    run.queued_reason = reason
    session.commit()


def carry(estimate: MemoryEstimate | None) -> dict[str, Any] | None:
    """The estimate as retry kwargs, so a deferred run does not re-probe the store."""
    if estimate is None:
        return None
    return {"bytes": estimate.bytes, "basis": estimate.basis, "exclusive": estimate.exclusive}


def from_carry(carried: dict[str, Any] | None) -> MemoryEstimate | None:
    if not carried:
        return None
    return MemoryEstimate(
        bytes=int(carried["bytes"]),
        basis=str(carried["basis"]),
        exclusive=bool(carried["exclusive"]),
    )
