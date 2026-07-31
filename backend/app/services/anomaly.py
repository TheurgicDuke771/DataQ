"""anomaly monitor kind — the stateful z-score engine (#593, ADR 0012).

The model is **deliberately simple and explainable** (issue #593): a rolling
window of the check's own raw measurements, a mean/stddev over it, and the
absolute z-score of this run's value as the ADR-0016-banded ``metric_value``.
No ML dependency, no opaque state — every number behind a verdict is written to
``observed_value``, which is what makes the learned band drawable (and arguable
with) on the metric-trend view (#594).

The pieces:

* :func:`measure_metric` — the raw scalar this run observed. It reuses the
  freshness/volume Core statement builders and their driver-boundary
  normalisation, so an anomaly over row count measures exactly what a volume
  monitor counts, quoted by the connection's own dialect (#476/#937).
* :func:`eligible_values` / :func:`score` / :func:`build_score_payload` — the
  pure scoring, unit-testable with no datasource and no DB.
* :func:`build_anomaly_executor` — the per-run closure the worker injects into
  ``run_service._run_outcomes`` (the #592/#794 pattern): it owns the session and
  the baseline row. Runners never see stateful kinds — they have no DB.

**Self-contained by design.** The measurement is taken by this executor against
the suite's target, *not* read from a sibling freshness/volume check's result.
Coupling to another check would make the anomaly depend on that check existing,
still being enabled, and having already run within the same run — a within-run
ordering hazard for a value the executor can simply measure itself.

**Cold start is a `skip`, never a verdict.** Below ``min_points`` usable prior
observations there is nothing to compare against; the check reports the
operational ``skip`` status (#122) with the point count, so "not watching yet" is
visible instead of being laundered into a green pass.

Baseline payload (JSONB, `monitor_baselines.baseline`), version 1::

    {"version": 1, "target_metric": "row_count", "window": 14,
     "seasonality": false,
     "observations": [{"ts": "2026-07-30T02:00:00+00:00", "value": 32840.0}, ...]}

Observations are raw measurements in chronological order, trimmed on every
update to `AnomalyParams.retained_observations`. They are metadata about a
measurement, never row data — no PII, no retention-sweep involvement.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import (
    ANOMALY,
    ANOMALY_DEGENERATE_Z,
    FRESHNESS,
    ROW_COUNT_METRIC,
    VOLUME,
    AnomalyParams,
    MonitorConfigError,
    SafeMonitorError,
    anomaly_params,
    build_monitor_statement,
    freshness_age_hours,
    monitor_expectation_type,
    monitor_outcome,
    row_count_from_scalar,
)
from backend.app.db.models import Check, Connection, MonitorBaseline
from backend.app.services.failure_classifier import classify_failure_reason
from backend.app.services.monitor_baseline import get_baseline, insert_baseline_if_absent
from backend.app.services.profile_service import ProfileUnsupportedError, _open_connection

log = get_logger(__name__)

# Bump only alongside a real payload-shape change; an unrecognised version is
# treated as "no usable history" (cold start) rather than mis-read — a wrong
# baseline is worse than a slow one.
BASELINE_VERSION = 1


@dataclass(frozen=True)
class Observation:
    """One recorded measurement: when it was taken (UTC) and what it was."""

    ts: datetime
    value: float


# ───────────────────────── measurement ─────────────────────────


def measure_metric(
    connection: Connection,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    params: AnomalyParams,
    secret_store: SecretStore,
    now: datetime,
) -> float:
    """This run's raw measurement of the configured ``target_metric``.

    ``row_count`` reuses the volume kind's ``SELECT COUNT(*)``; ``freshness_age_hours``
    reuses the freshness kind's ``SELECT MAX(<column>)`` plus the shared age math.
    Both come back as a **Core statement**, executed uncompiled so the connection's
    own dialect quotes the identifiers (#476/#937) — hand-rolling the SQL here would
    re-introduce the mixed-case fold bug on Snowflake and the wrong quote character
    on Unity Catalog.

    SQL datasources only, which is why `check_service.ANOMALY_CAPABLE_TYPES` is
    narrower than `MONITOR_CAPABLE_TYPES`. Iceberg and flat files compute their
    monitor scalars natively *inside their runners*, and a stateful kind never
    reaches a runner — extending anomaly to them is a per-datasource measurement
    seam on this function, not a widened allowlist. The defence-in-depth check
    below restates that as the check's own error rather than letting a
    non-SQL connection surface as an unexplained classified failure.
    """
    if params.target_metric == ROW_COUNT_METRIC:
        statement = build_monitor_statement(
            VOLUME, table=table, schema=schema, catalog=catalog, config={}
        )
    else:
        statement = build_monitor_statement(
            FRESHNESS, table=table, schema=schema, catalog=catalog, config={"column": params.column}
        )
    try:
        with _open_connection(connection, secret_store) as conn:
            scalar = conn.execute(statement).scalar()
    except ProfileUnsupportedError as exc:
        raise MonitorConfigError(
            f"anomaly monitors need a SQL datasource, not {connection.type!r}"
        ) from exc
    if params.target_metric == ROW_COUNT_METRIC:
        return float(row_count_from_scalar(scalar))
    source = f"MAX({params.column})"
    if scalar is None:
        # An empty table (or an all-NULL column) has no age. Same call as the
        # freshness kind makes, and for the same reason: silently scoring a
        # missing measurement would poison the baseline with a fabricated point.
        raise MonitorConfigError(f"{source} is unavailable, anomaly can't measure freshness age")
    return freshness_age_hours(scalar, now=now, source=source, column=params.column)


# ───────────────────────── baseline payload ─────────────────────────


def load_observations(row: MonitorBaseline | None, params: AnomalyParams) -> list[Observation]:
    """The usable prior observations from a stored baseline row.

    Returns ``[]`` — i.e. a fresh cold start — when the row is absent, was written
    by a future payload version, or was recorded for a **different**
    ``target_metric``. That last case matters: row counts and staleness hours are
    different quantities in different units, so scoring one against the other's
    history would produce a confident number about nothing. Editing the check's
    target metric therefore restarts learning, which is the honest behaviour.

    Malformed or non-finite entries are dropped individually rather than failing
    the check: the surviving history is still a valid baseline, and a monitor that
    errors because one JSONB row is odd is worse than one that learns from the rest.
    """
    if row is None:
        return []
    payload = row.baseline if isinstance(row.baseline, dict) else {}
    if payload.get("version") != BASELINE_VERSION:
        return []
    if payload.get("target_metric") != params.target_metric:
        return []
    raw = payload.get("observations")
    if not isinstance(raw, list):
        return []
    out: list[Observation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if not math.isfinite(value):
            continue
        try:
            ts = datetime.fromisoformat(str(entry.get("ts")))
        except (TypeError, ValueError):
            continue
        out.append(Observation(ts=ts if ts.tzinfo else ts.replace(tzinfo=UTC), value=float(value)))
    return out


def dump_baseline(observations: list[Observation], params: AnomalyParams) -> dict[str, Any]:
    """The JSONB payload for a set of observations (already trimmed by the caller)."""
    return {
        "version": BASELINE_VERSION,
        "target_metric": params.target_metric,
        "window": params.window,
        "seasonality": params.seasonality,
        "observations": [{"ts": o.ts.isoformat(), "value": o.value} for o in observations],
    }


def trim(observations: list[Observation], params: AnomalyParams) -> list[Observation]:
    """Keep only the most recent `retained_observations` (chronological order in)."""
    retained = params.retained_observations
    return observations[-retained:] if retained else []


# ───────────────────────── scoring (pure) ─────────────────────────


def eligible_values(
    observations: list[Observation], *, now: datetime, params: AnomalyParams
) -> list[float]:
    """The prior values this run is scored against, newest-last.

    With ``seasonality`` on, only observations sharing ``now``'s **UTC weekday**
    count — so a Monday-morning load that is always three times Sunday's isn't
    reported as an anomaly every Monday. The window then means "the last N
    same-weekday observations", which is why the retained history is seven times
    larger in that mode. UTC (not a local zone) because every stored ``ts`` is
    UTC and the alternative — a per-check timezone — is config nobody asked for.
    """
    considered = (
        [o for o in observations if o.ts.weekday() == now.weekday()]
        if params.seasonality
        else list(observations)
    )
    return [o.value for o in considered[-params.window :]]


def score(value: float, priors: list[float]) -> tuple[float, float, float, bool]:
    """``(z_score, mean, stddev, degenerate)`` for a value against its priors.

    The **sample** standard deviation (n-1) — the priors are a sample of the
    metric's behaviour, not its whole population — which is why the config floor
    on ``min_points`` is 3 rather than 1.

    ``stddev == 0`` (every prior identical) has no defined z. It is resolved
    explicitly, not by arithmetic: an identical value is ``0.0`` (nothing moved),
    and any other value is :data:`ANOMALY_DEGENERATE_Z`, a documented finite
    sentinel. Dividing would raise, and ``inf``/``nan`` would be dropped by
    ``severity.extract_metric`` as "no bandable metric" — i.e. maximal deviation
    silently resolving to ``pass``.
    """
    if len(priors) < 2:
        raise MonitorConfigError(
            f"anomaly scoring needs at least 2 prior points, got {len(priors)}"
        )
    mean = statistics.fmean(priors)
    stddev = statistics.stdev(priors)
    if stddev == 0.0:
        return (0.0 if value == mean else ANOMALY_DEGENERATE_Z), mean, stddev, True
    return abs(value - mean) / stddev, mean, stddev, False


def build_score_payload(value: float, priors: list[float], params: AnomalyParams) -> dict[str, Any]:
    """The `observed_value` payload the registry's outcome strategy bands.

    Everything the verdict rests on is in it — the measurement, the learned
    mean/stddev, how many points were used and whether seasonality filtered them
    — so a surprising `critical` can be explained from the stored result alone.
    """
    payload: dict[str, Any] = {
        "target_metric": params.target_metric,
        "value": value,
        "points": len(priors),
        "window": params.window,
        "min_points": params.min_points,
        "seasonality": params.seasonality,
    }
    if len(priors) < params.min_points:
        payload["insufficient_history"] = True
        payload["reason"] = "insufficient_history"
        return payload
    z_score, mean, stddev, degenerate = score(value, priors)
    payload.update(
        {
            "z_score": round(z_score, 6),
            "mean": round(mean, 6),
            "stddev": round(stddev, 6),
            "deviation": round(value - mean, 6),
            "degenerate_stddev": degenerate,
        }
    )
    return payload


# ───────────────────────── run executor ─────────────────────────


def build_anomaly_executor(
    session: Session,
    *,
    connection: Connection,
    target_table: str,
    target_schema: str | None,
    target_catalog: str | None,
    secret_store: SecretStore,
    persist: bool = True,
) -> Callable[[Check], CheckOutcome]:
    """A per-run executor for `anomaly` checks (the #592/#794 stateful pattern).

    Each call measures the target, scores it against the check's own rolling
    history, appends the measurement, and upserts the baseline. First runs (and
    any run still short of ``min_points``) report `skip` while still **recording**
    the observation — that is how the history accrues.

    ``persist=False`` is the dry-run mode: measure and score, write nothing.
    A measurement or config failure is the CHECK's operational error (#122),
    never the run's — one unreachable target must not fail sibling checks.
    """

    def executor(check: Check) -> CheckOutcome:
        config = dict(check.config)
        now = datetime.now(UTC)
        try:
            params = anomaly_params(config)
            value = measure_metric(
                connection,
                table=target_table,
                schema=target_schema,
                catalog=target_catalog,
                params=params,
                secret_store=secret_store,
                now=now,
            )
        except Exception as exc:
            # Safe-marked messages (bad config, an unparseable timestamp cell)
            # name the user's own mistake and persist verbatim; anything else —
            # a driver/SDK exception, which can carry a DSN or a SAS-signed URL
            # (#828/#900) — is classified before it reaches a result row.
            log.warning(
                "anomaly_measurement_failed",
                check_id=str(check.id),
                connection_type=connection.type,
                error_type=type(exc).__name__,
            )
            return CheckOutcome(
                expectation_type=monitor_expectation_type(ANOMALY),
                success=False,
                errored=True,
                error_message=(
                    str(exc) if isinstance(exc, SafeMonitorError) else classify_failure_reason(exc)
                ),
            )
        row = get_baseline(session, check.id)
        observations = load_observations(row, params)
        priors = eligible_values(observations, now=now, params=params)
        payload = build_score_payload(value, priors, params)
        if row is not None:
            payload["baseline_captured_at"] = row.captured_at.isoformat()
        if persist:
            kept = trim([*observations, Observation(ts=now, value=value)], params)
            baseline = dump_baseline(kept, params)
            if row is None:
                insert_baseline_if_absent(
                    session, check_id=check.id, kind=ANOMALY, baseline=baseline
                )
            else:
                row.baseline = baseline
        else:
            payload["dry_run"] = True
        return monitor_outcome(ANOMALY, scalar=payload, config=config, now=now)

    return executor
