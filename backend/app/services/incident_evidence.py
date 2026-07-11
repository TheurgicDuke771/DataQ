"""The deterministic incident evidence card (ADR 0034 decision 4 / Theme-2 layer 1).

Assembled from **existing data only** — no LLM, no live datasource query — so it is
cheap enough to snapshot at incident open and refresh on every occurrence. Five
layers, each best-effort (a missing layer degrades to ``None``/empty, never fails):

* ``check`` / ``asset`` — the incident's anchor identity.
* ``failing_result`` — the breaching result's status + metric + GX aggregates.
* ``metric_trend`` — the last N ``metric_value`` readings for the pair (sudden vs.
  drift), read straight off ``results`` (ADR 0012's SQL-aggregatable scalar).
* ``upstream_pipeline_run`` — the orchestration run that triggered the suite run
  (via ``run.triggered_by`` correlation) + its delay vs. that pipeline's history.
* ``sibling_checks`` — the other checks' outcomes in the same run.
* ``downstream_blast_radius`` — the lineage-derived downstream assets (§2).

**PII rule is absolute: the card NEVER carries ``sample_failures`` content** — only
counts/aggregates and identity. ``profile_diff`` (failing-vs-last-passing batch) is
deliberately omitted: it needs a live datasource introspection, which is not
"existing data" and not cheap; it is a null placeholder the card documents.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Check, PipelineRun, Result, Run
from backend.app.lineage.edges import downstream_assets

log = get_logger(__name__)

# How many recent metric readings the trend layer carries, and how many prior
# pipeline runs the delay-vs-history baseline averages over.
_TREND_LIMIT = 10
_PIPELINE_HISTORY_LIMIT = 10


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
    """Assemble the layer-1 evidence card for a breaching ``result`` on ``run``.

    Every layer is best-effort — this must never raise into the incident engine
    (which itself never raises into the run path). ``check``/``asset`` may be
    ``None`` (a since-deleted check, an unresolved asset) and degrade gracefully.
    """
    return {
        "generated_at": _utc_now_iso(),
        "check": _check_layer(check),
        "asset": _asset_layer(asset),
        "failing_result": _failing_result_layer(result),
        "metric_trend": _metric_trend_layer(session, check_id=result.check_id),
        "sibling_checks": _sibling_checks_layer(session, run=run, exclude_check_id=result.check_id),
        "upstream_pipeline_run": _upstream_pipeline_layer(session, run=run),
        "downstream_blast_radius": _blast_radius_layer(session, asset=asset),
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

    ``observed_value``/``expected_value`` are GX aggregates (already sanitized at
    write time and surfaced by the alert card); ``sample_failures`` is deliberately
    excluded — it is the only result column that can carry raw (PII-bearing) rows.
    """
    return {
        "status": result.status,
        "metric_value": _num(result.metric_value),
        "observed_value": result.observed_value,
        "expected_value": result.expected_value,
    }


def _metric_trend_layer(session: Session, *, check_id: uuid.UUID) -> list[dict[str, Any]]:
    """The last ``_TREND_LIMIT`` readings for the check (newest first) — the
    ``metric_value`` trend that distinguishes a sudden break from a slow drift."""
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
    unhealthy or is this one check the outlier?). Names via a single join."""
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

    Correlation via ``run.triggered_by`` = ``<provider>:<pipeline>:<provider_run_id>``
    (CLAUDE.md §10). A manual/scheduled/probe run has no upstream pipeline → ``None``.
    """
    parsed = _parse_orchestration_marker(run.triggered_by)
    if parsed is None:
        return None
    provider, _pipeline, provider_run_id = parsed
    pipeline_run = session.scalars(
        select(PipelineRun).where(
            PipelineRun.provider == provider,
            PipelineRun.provider_run_id == provider_run_id,
        )
    ).first()
    if pipeline_run is None:
        return None
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


def _parse_orchestration_marker(marker: str | None) -> tuple[str, str, str] | None:
    """``<provider>:<pipeline_or_dag_id>:<provider_run_id>`` → its parts, or ``None``.

    Only the three orchestration providers correlate; ``manual:``/``schedule:…``/
    ``probe`` markers (and a bare/absent one) return ``None``. The pipeline id itself
    may contain ``:`` — split off the leading provider and the trailing run id.
    """
    if not marker:
        return None
    provider, sep, rest = marker.partition(":")
    if not sep or provider not in ("adf", "airflow", "dbt"):
        return None
    pipeline, sep2, provider_run_id = rest.rpartition(":")
    if not sep2 or not pipeline or not provider_run_id:
        return None
    return provider, pipeline, provider_run_id


def _duration_seconds(pipeline_run: PipelineRun) -> float | None:
    if pipeline_run.started_at is None or pipeline_run.finished_at is None:
        return None
    return (pipeline_run.finished_at - pipeline_run.started_at).total_seconds()


def _delay_vs_history(session: Session, pipeline_run: PipelineRun) -> float | None:
    """This pipeline run's duration minus the average of its recent prior succeeded
    runs — positive = slower than usual. ``None`` when either duration or the
    baseline (needs ≥1 prior completed run) is unavailable (skip gracefully)."""
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
    leaf; ``downstream_assets`` is itself depth-capped + cycle-safe."""
    if asset is None:
        return []
    return [
        {"id": str(a.id), "namespace": a.namespace, "name": a.name, "env": a.env}
        for a in downstream_assets(session, asset.id)
    ]
