"""The deterministic incident evidence card (ADR 0034 decision 4 / Theme-2 layer 1)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import (
    ORCHESTRATION_PROVIDERS,
    Asset,
    Check,
    PipelineRun,
    Result,
    Run,
    Suite,
)
from backend.app.lineage.edges import downstream_assets
from backend.app.orchestration import markers
from backend.app.services import run_service
from backend.app.services.rollup import AGGREGATABLE_RUN_STATUSES

log = get_logger(__name__)

# How many recent metric readings the trend layer carries, and how many prior
# pipeline runs the delay-vs-history baseline averages over.
_TREND_LIMIT = 10
_PIPELINE_HISTORY_LIMIT = 10
# Cross-suite same-asset siblings (#1635): how far back a sibling's latest result
# still counts as live context, and how many distinct checks the layer carries.
_SAME_ASSET_SIBLING_WINDOW = timedelta(days=7)
_SAME_ASSET_SIBLING_LIMIT = 20

# The list-valued, sample-row-bearing keys of `gx_runner._SAMPLE_KEYS` — the two that carry raw cell
# values (vs. the scalar `unexpected_count`/`unexpected_percent` aggregates).
_SAMPLE_LIST_KEYS = frozenset({"partial_unexpected_list", "unexpected_index_list"})

#: The `check.kind` values `_kind_detail_layer` dispatches on — the single source of truth other
#: modules (e.g. `llm_rca`) import rather than re-spelling this set independently (#1633 review).
MONITOR_KINDS = frozenset({"freshness", "volume", "schema_drift", "anomaly"})


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
    now = datetime.now(UTC)
    return {
        "generated_at": now.isoformat(),
        "check": _layer("check", lambda: _check_layer(check)),
        "asset": _layer("asset", lambda: _asset_layer(asset)),
        "failing_result": _layer(
            "failing_result",
            lambda: _failing_result_layer(
                session, run=run, result=result, check=check, asset=asset
            ),
        ),
        "kind_detail": _layer("kind_detail", lambda: _kind_detail_layer(check, result)),
        "metric_trend": _layer(
            "metric_trend", lambda: _metric_trend_layer(session, check_id=result.check_id)
        ),
        "sibling_checks": _layer(
            "sibling_checks",
            lambda: _sibling_checks_layer(session, run=run, exclude_check_id=result.check_id),
        ),
        "same_asset_siblings": _layer(
            "same_asset_siblings",
            lambda: _same_asset_siblings_layer(
                session, asset=asset, exclude_check_id=result.check_id, now=now
            ),
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


def _failing_result_layer(
    session: Session, *, run: Run, result: Result, check: Check | None, asset: Asset | None
) -> dict[str, Any]:
    """The breaching result — status + metric + GX aggregates. **No sample rows.**
    ``sample_failures`` is never read.

    ``observed_value`` can itself carry a literal warehouse cell value (or a
    list of them) for an expectation-kind check's min/max/mean-style rules —
    real target data, not just GX metadata. This card is built ONCE here and
    stored on the `Incident` row (workspace-true, like `same_asset_siblings`
    below) — it is the ONLY place that ever computes it, so the G3 governance
    floor (`run_service.redact_observed_value`, the same column-aware ladder
    every other results surface applies) has to run HERE or nowhere: neither
    `get_incident` (REST/MCP) nor the RCA narrative prompt (#1633) re-derive
    this card from the raw `Result` row, they only ever read this stored copy
    (#1772 — found via `rca_narrative`, but the same unmasked value was
    already reaching every `get_incident` caller with suite view access).
    """
    observed = _strip_sample_lists(result.observed_value)
    if check is not None:
        tested_column, expectation_type = run_service.historical_check_context(
            session, [result], {check.id: check}
        ).get(result.id, (None, None))
        suite = session.get(Suite, run.suite_id)
        tags = asset.column_tags if asset is not None else None
        observed = run_service.redact_observed_value(
            observed,
            tested_column=tested_column,
            expectation_type=expectation_type,
            policy=suite.column_policy if suite is not None else None,
            tags=tags,
        )
    return {
        "status": result.status,
        "metric_value": _num(result.metric_value),
        "observed_value": observed,
        "expected_value": result.expected_value,
    }


def _strip_sample_lists(observed: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop the list-valued sample-bearing keys from an ``observed_value`` dict
    (see ``_SAMPLE_LIST_KEYS``); non-dict / ``None`` shapes pass through.
    """
    if not isinstance(observed, dict):
        return observed
    return {key: val for key, val in observed.items() if key not in _SAMPLE_LIST_KEYS}


def _kind_detail_layer(check: Check | None, result: Result) -> dict[str, Any] | None:
    """The monitor-kind-shaped fields lifted out of the raw ``observed_value``
    JSONB (#1635), so a consumer doesn't need to know ``MONITOR_KINDS``' four
    different shapes to answer "how stale" / "how anomalous" / "what changed".
    ``None`` for ``expectation``/``comparison`` checks, where
    ``failing_result.observed_value`` already **is** the shape — and whenever
    ``observed_value`` isn't the dict a monitor-kind check always produces (an
    operational error, most often).
    """
    if check is None or not isinstance(result.observed_value, dict):
        return None
    observed = result.observed_value
    if check.kind == "freshness":
        return {
            "age_hours": observed.get("age_hours"),
            "max_timestamp": observed.get("max_timestamp"),
        }
    if check.kind == "volume":
        return {
            "row_count": observed.get("row_count"),
            "deviation_pct": observed.get("deviation_pct"),
        }
    if check.kind == "schema_drift":
        # `added`/`removed`/`type_changed` are absent (not empty) on the run that
        # captures the very first baseline — `.get(..., [])` would otherwise read
        # identically to "compared, nothing changed" (the #828 empty-reads-as-
        # clean class), so `baseline_captured` is carried alongside them.
        return {
            "added": observed.get("added", []),
            "removed": observed.get("removed", []),
            "type_changed": observed.get("type_changed", []),
            "baseline_captured": observed.get("baseline_captured", False),
        }
    if check.kind == "anomaly":
        return {
            "z_score": observed.get("z_score"),
            "mean": observed.get("mean"),
            "stddev": observed.get("stddev"),
            "insufficient_history": observed.get("insufficient_history", False),
        }
    return None


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


def _same_asset_siblings_layer(
    session: Session, *, asset: Asset | None, exclude_check_id: uuid.UUID, now: datetime
) -> list[dict[str, Any]]:
    """The latest known outcome of every OTHER check targeting this asset, across
    ALL suites — not just this run's (#1635). ``_sibling_checks_layer`` above only
    sees this one run's own suite; a volume/schema-drift break on the SAME asset
    from a DIFFERENT suite ("the table also dropped 40% of its rows this
    morning") is the most common real root-cause signal and was invisible to it.

    Window-bounded to ``_SAME_ASSET_SIBLING_WINDOW`` so a check that hasn't run
    in months doesn't read as live context, and capped at
    ``_SAME_ASSET_SIBLING_LIMIT``. Only ``succeeded`` runs count — a `failed`
    run's results are an operational failure, not a complete account (mirrors
    ``rollup.AGGREGATABLE_RUN_STATUSES``).

    **Workspace-true** (ADR 0037), like the asset rollup: every suite's latest
    result on this asset is captured here regardless of the incident's own
    caller, because this layer is built once at sync time with no caller in
    scope. The read surface (``incident_service.evidence_for_caller``) redacts
    the entries a specific caller has no suite grant for before the card ever
    reaches a response — this function does not gate anything.
    """
    if asset is None:
        return []
    window_start = now - _SAME_ASSET_SIBLING_WINDOW
    latest_per_check = (
        select(
            Result.check_id,
            Result.status,
            Result.metric_value,
            Result.created_at,
        )
        .join(Run, Run.id == Result.run_id)
        .where(
            Run.asset_id == asset.id,
            Run.status.in_(AGGREGATABLE_RUN_STATUSES),
            Result.check_id != exclude_check_id,
            Result.created_at >= window_start,
        )
        .order_by(Result.check_id, Result.created_at.desc(), Result.id.desc())
        .distinct(Result.check_id)
        .subquery()
    )
    rows = session.execute(
        select(
            latest_per_check.c.check_id,
            Check.name,
            Check.kind,
            Check.suite_id,
            latest_per_check.c.status,
            latest_per_check.c.metric_value,
            latest_per_check.c.created_at,
        )
        .join(Check, Check.id == latest_per_check.c.check_id)
        .order_by(latest_per_check.c.created_at.desc())
        .limit(_SAME_ASSET_SIBLING_LIMIT)
    ).all()
    return [
        {
            "check_id": str(check_id),
            "check_name": name,
            "kind": kind,
            "suite_id": str(suite_id),
            "status": status,
            "metric_value": _num(metric_value),
            "created_at": _iso(created_at),
        }
        for check_id, name, kind, suite_id, status, metric_value, created_at in rows
    ]


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
    # Reconstruct-and-compare, fail-closed on a collision (#1713/#1714) — shared with
    # every other marker reader (#1728).
    pipeline_run = markers.unambiguous_pipeline_run(session, marker)
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
