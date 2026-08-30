"""The deterministic incident evidence card (ADR 0034 decision 4 / Theme-2 layer 1)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Asset, Check, PipelineRun, Result, Run
from backend.app.lineage.edges import downstream_assets

log = get_logger(__name__)

# How many recent metric readings the trend layer carries, and how many prior
# pipeline runs the delay-vs-history baseline averages over.
_TREND_LIMIT = 10
_PIPELINE_HISTORY_LIMIT = 10

# The list-valued, sample-row-bearing keys of `gx_runner._SAMPLE_KEYS` — the two that carry raw cell
# values (vs. the scalar `unexpected_count`/`unexpected_percent` aggregates).
_SAMPLE_LIST_KEYS = frozenset({"partial_unexpected_list", "unexpected_index_list"})


def _layer(name: str, fn: Callable[[], Any]) -> Any:
    """Run one evidence layer best-effort: any failure logs a structured warning
    and degrades that layer to ``None`` — never poisoning the card (and through
    it the whole run's incident sync).
    """
    try:
        return fn()
    except Exception:
        log.warning("incident_evidence_layer_failed", layer=name)
        return None


def _num(value: Decimal | None) -> float | None:
    """Widen a NUMERIC metric to a JSON-friendly float (``None`` stays ``None``)."""
    return float(value) if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def build_evidence(
    session: Session,
    *,
    run: Run,
    result: Result,
    check: Check | None,
    asset: Asset | None,
) -> dict[str, Any]:
    """Assemble the layer-1 evidence card for a breaching ``result`` on ``run``."""
    return {
        "generated_at": _utc_now_iso(),
        "check": _layer("check", lambda: _check_layer(check)),
        "asset": _layer("asset", lambda: _asset_layer(asset)),
        "failing_result": _layer("failing_result", lambda: _failing_result_layer(result)),
        "metric_trend": _layer(
            "metric_trend", lambda: _metric_trend_layer(session, check_id=result.check_id)
        ),
        "sibling_checks": _layer(
            "sibling_checks",
            lambda: _sibling_checks_layer(session, run=run, exclude_check_id=result.check_id),
        ),
        "upstream_pipeline_run": _layer(
            "upstream_pipeline_run", lambda: _upstream_pipeline_layer(session, run=run)
        ),
        "downstream_blast_radius": _layer(
            "downstream_blast_radius", lambda: _blast_radius_layer(session, asset=asset)
        ),
        # Needs a live datasource profile of both batches — not existing data, not
        # cheap. Documented null placeholder (see module docstring).
        "profile_diff": None,
    }


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _check_layer(check: Check | None) -> dict[str, Any] | None:
    if check is None:
        return None
    return {
        "id": str(check.id),
        "name": check.name,
        "expectation_type": check.expectation_type,
        "kind": check.kind,
    }


def _asset_layer(asset: Asset | None) -> dict[str, Any] | None:
    if asset is None:
        return None
    return {
        "id": str(asset.id),
        "namespace": asset.namespace,
        "name": asset.name,
        "env": asset.env,
    }


def _failing_result_layer(result: Result) -> dict[str, Any]:
    """The breaching result — status + metric + GX aggregates. **No sample rows.**
    ``sample_failures`` is never read.
    """
    return {
        "status": result.status,
        "metric_value": _num(result.metric_value),
        "observed_value": _strip_sample_lists(result.observed_value),
        "expected_value": result.expected_value,
    }


def _strip_sample_lists(observed: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop the list-valued sample-bearing keys from an ``observed_value`` dict
    (see ``_SAMPLE_LIST_KEYS``); non-dict / ``None`` shapes pass through.
    """
    if not isinstance(observed, dict):
        return observed
    return {key: val for key, val in observed.items() if key not in _SAMPLE_LIST_KEYS}


def _metric_trend_layer(session: Session, *, check_id: uuid.UUID) -> list[dict[str, Any]]:
    """The last ``_TREND_LIMIT`` readings for the check (newest first) — the
    ``metric_value`` trend that distinguishes a sudden break from a slow drift.
    """
    rows = session.execute(
        select(Result.status, Result.metric_value, Result.created_at, Result.run_id)
        .where(Result.check_id == check_id)
        .order_by(Result.created_at.desc())
        .limit(_TREND_LIMIT)
    ).all()
    return [
        {
            "status": status,
            "metric_value": _num(metric_value),
            "created_at": _iso(created_at),
            "run_id": str(run_id),
        }
        for status, metric_value, created_at, run_id in rows
    ]


def _sibling_checks_layer(
    session: Session, *, run: Run, exclude_check_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The other checks' outcomes in the same run (context: is the asset broadly
    unhealthy or is this one check the outlier?). Names via a single join.
    """
    rows = session.execute(
        select(Check.name, Result.status)
        .join(Check, Check.id == Result.check_id)
        .where(Result.run_id == run.id, Result.check_id != exclude_check_id)
        .order_by(Check.name)
    ).all()
    return [{"check_name": name, "status": status} for name, status in rows]


def _upstream_pipeline_layer(session: Session, *, run: Run) -> dict[str, Any] | None:
    """The orchestration pipeline run that triggered this suite run, + its delay
    vs. that pipeline's own history.
    """
    marker = run.triggered_by
    if not marker:
        return None
    provider, sep, _rest = marker.partition(":")
    if not sep or provider not in ORCHESTRATION_PROVIDERS:
        return None
    # Reconstruct-and-compare, not parse-and-split: `provider_run_id` (e.g. an
    # Airflow DAG run's default `run_id`, "manual__2026-08-08T01:30:00+00:00")
    # can itself contain colons, so there is no reliable place to cut the
    # "<provider>:<pipeline_or_dag_id>:<provider_run_id>" marker to recover it
    # (a trailing `rpartition(":")` used to truncate it to a few characters,
    # #1713). `orchestration_service._trigger_suites` builds the marker as
    # `f"{provider}:{pipeline_or_dag_id}:{provider_run_id}"`, so inverting it by
    # equality against the stored PipelineRun columns recovers the SAME row the
    # marker was built from. It does NOT prove that row is the only one that
    # could produce this string: dbt's `pipeline_or_dag_id` (job_name) is
    # free-form webhook input with no colon restriction, so two distinct rows
    # can reconstruct to an identical marker if a colon lands differently
    # across their pipeline/run-id boundary. `.all()` + the count check below
    # catches that rather than letting `.first()` silently attribute the
    # incident's evidence to whichever row Postgres happens to return first.
    candidates = session.scalars(
        select(PipelineRun).where(
            PipelineRun.provider == provider,
            func.concat(
                PipelineRun.provider,
                ":",
                PipelineRun.pipeline_or_dag_id,
                ":",
                PipelineRun.provider_run_id,
            )
            == marker,
        )
    ).all()
    if len(candidates) != 1:
        if candidates:
            log.warning(
                "upstream_pipeline_marker_ambiguous",
                marker=marker,
                provider=provider,
                candidate_count=len(candidates),
            )
        return None
    pipeline_run = candidates[0]
    return {
        "provider": pipeline_run.provider,
        "pipeline_or_dag_id": pipeline_run.pipeline_or_dag_id,
        "provider_run_id": pipeline_run.provider_run_id,
        "status": pipeline_run.status,
        "started_at": _iso(pipeline_run.started_at),
        "finished_at": _iso(pipeline_run.finished_at),
        "duration_seconds": _duration_seconds(pipeline_run),
        "delay_seconds_vs_history": _delay_vs_history(session, pipeline_run),
    }


def _duration_seconds(pipeline_run: PipelineRun) -> float | None:
    if pipeline_run.started_at is None or pipeline_run.finished_at is None:
        return None
    return (pipeline_run.finished_at - pipeline_run.started_at).total_seconds()


def _delay_vs_history(session: Session, pipeline_run: PipelineRun) -> float | None:
    """This pipeline run's duration minus the average of its recent prior succeeded
    runs — positive = slower than usual. ``None`` when either duration or the
    baseline (needs ≥1 prior completed run) is unavailable (skip gracefully).
    """
    this_duration = _duration_seconds(pipeline_run)
    if this_duration is None:
        return None
    prior = session.execute(
        select(PipelineRun.started_at, PipelineRun.finished_at)
        .where(
            PipelineRun.provider == pipeline_run.provider,
            PipelineRun.pipeline_or_dag_id == pipeline_run.pipeline_or_dag_id,
            PipelineRun.id != pipeline_run.id,
            PipelineRun.status == "succeeded",
            PipelineRun.started_at.is_not(None),
            PipelineRun.finished_at.is_not(None),
            PipelineRun.created_at < pipeline_run.created_at,
        )
        .order_by(PipelineRun.created_at.desc())
        .limit(_PIPELINE_HISTORY_LIMIT)
    ).all()
    durations = [float((fin - start).total_seconds()) for start, fin in prior]
    if not durations:
        return None
    baseline = sum(durations) / len(durations)
    return this_duration - baseline


def _blast_radius_layer(session: Session, *, asset: Asset | None) -> list[dict[str, Any]]:
    """The downstream assets reachable from the failing one (lineage §2) — the
    "what breaks downstream" answer. Empty when the asset is unknown or a lineage
    leaf; ``downstream_assets`` is itself depth-capped + cycle-safe.
    """
    if asset is None:
        return []
    return [
        {"id": str(a.id), "namespace": a.namespace, "name": a.name, "env": a.env}
        for a in downstream_assets(session, asset.id)
    ]
