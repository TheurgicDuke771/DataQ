"""anomaly monitor kind — the stateful z-score engine (#593, ADR 0012)."""

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
from backend.app.core.timeutil import as_utc
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import (
    ANOMALY,
    ANOMALY_DEGENERATE_Z,
    COLUMN_PROFILE_MAX_COLUMNS,
    COLUMN_PROFILE_METRIC,
    FRESHNESS,
    ROW_COUNT_METRIC,
    VOLUME,
    AnomalyParams,
    MonitorConfigError,
    anomaly_params,
    build_monitor_statement,
    column_distinct_probe,
    column_profile_statement,
    freshness_age_hours,
    monitor_expectation_type,
    monitor_outcome,
    qualified_table,
    row_count_from_scalar,
)
from backend.app.db.models import Check, Connection, MonitorBaseline
from backend.app.services.failure_classifier import safe_failure_reason
from backend.app.services.monitor_baseline import get_baseline, insert_baseline_if_absent
from backend.app.services.profile_service import (
    ProfileUnsupportedError,
    _open_connection,
    build_columns_query,
    resolve_effective_schema,
)

log = get_logger(__name__)

# Bump only alongside a real payload-shape change; an unrecognised version is treated as "no usable
# history" (cold start) rather than mis-read — a wrong baseline is worse than a slow one.
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
    """This run's raw measurement of the configured ``target_metric``."""
    try:
        with _open_connection(connection, secret_store) as conn:
            dialect = conn.dialect if catalog is not None else None
            if params.target_metric == ROW_COUNT_METRIC:
                statement = build_monitor_statement(
                    VOLUME, table=table, schema=schema, catalog=catalog, config={}, dialect=dialect
                )
            else:
                statement = build_monitor_statement(
                    FRESHNESS,
                    table=table,
                    schema=schema,
                    catalog=catalog,
                    config={"column": params.column},
                    dialect=dialect,
                )
            scalar = conn.execute(statement).scalar()
    except ProfileUnsupportedError as exc:
        raise MonitorConfigError(
            f"anomaly monitors need a SQL datasource, not {connection.type!r}"
        ) from exc
    if params.target_metric == ROW_COUNT_METRIC:
        return float(row_count_from_scalar(scalar))
    source = f"MAX({params.column})"
    if scalar is None:
        # An empty table (or an all-NULL column) has no age.
        raise MonitorConfigError(f"{source} is unavailable, anomaly can't measure freshness age")
    return freshness_age_hours(scalar, now=now, source=source, column=params.column)


# ───────────────────────── column profile (ADR 0047) ─────────────────────────

NULL_PCT = "null_pct"
DISTINCT = "distinct"
_SERIES_SEP = "\x1f"
#: Spread floors, so a steady series is not flagged for a trivial change: null % never below
#: 1 percentage point, distinct count never below 10% of its mean.
_NULL_PCT_STDDEV_FLOOR = 1.0
_DISTINCT_STDDEV_FLOOR_RATIO = 0.1
#: Deviations named in the result, largest first.
_COLUMN_PROFILE_REPORTED = 10


@dataclass(frozen=True)
class ColumnProfile:
    row_count: int
    series: dict[str, float]
    columns_measured: int
    columns_total: int
    #: Columns with no distinct count: the engine can't cast the type to a string (Redshift
    #: GEOMETRY, BOOLEAN), or the cast dropped values (Redshift SUPER objects cast to NULL),
    #: which would undercount.
    distinct_unavailable: tuple[str, ...] = ()


def series_key(column: str, metric: str) -> str:
    return f"{column}{_SERIES_SEP}{metric}"


def measure_column_profile(
    connection: Connection,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    secret_store: SecretStore,
) -> ColumnProfile:
    """Every column's null % and distinct count, in one query. If the engine refuses to cast
    some column to a string, a zero-row probe per column finds it and the query runs again
    without that column's distinct count."""
    effective_schema = resolve_effective_schema(connection, schema)
    try:
        with _open_connection(connection, secret_store) as conn:
            dialect = conn.dialect if catalog is not None else None
            columns_all = list(
                conn.execute(build_columns_query(effective_schema, table, catalog, dialect)).keys()
            )
            columns = columns_all[:COLUMN_PROFILE_MAX_COLUMNS]
            target = qualified_table(
                table=table, schema=effective_schema, catalog=catalog, dialect=dialect
            )
            castable = list(columns)
            try:
                row = conn.execute(
                    column_profile_statement(target, columns, distinct=castable)
                ).one()
            except Exception:
                conn.rollback()
                castable = _castable_columns(conn, target, columns)
                try:
                    row = conn.execute(
                        column_profile_statement(target, columns, distinct=castable)
                    ).one()
                except Exception:
                    conn.rollback()
                    castable = []
                    row = conn.execute(
                        column_profile_statement(target, columns, distinct=castable)
                    ).one()
    except ProfileUnsupportedError as exc:
        raise MonitorConfigError(
            f"anomaly monitors need a SQL datasource, not {connection.type!r}"
        ) from exc
    values = iter(row)
    row_count = int(row_count_from_scalar(next(values)))
    series: dict[str, float] = {}
    unavailable: list[str] = []
    counted = set(castable)
    for name in columns:
        non_null = int(next(values) or 0)
        if row_count > 0:
            series[series_key(name, NULL_PCT)] = round(
                100.0 * (row_count - non_null) / row_count, 6
            )
        if name not in counted:
            unavailable.append(name)
            continue
        cast_non_null, distinct = int(next(values) or 0), next(values)
        if cast_non_null == non_null:
            series[series_key(name, DISTINCT)] = float(distinct or 0)
        else:
            unavailable.append(name)
    return ColumnProfile(
        row_count=row_count,
        series=series,
        columns_measured=len(columns),
        columns_total=len(columns_all),
        distinct_unavailable=tuple(unavailable),
    )


def _castable_columns(conn: Any, target: Any, columns: list[str]) -> list[str]:
    castable: list[str] = []
    for name in columns:
        try:
            conn.execute(column_distinct_probe(target, name)).all()
        except Exception:
            conn.rollback()
            continue
        castable.append(name)
    return castable


def load_profile_observations(
    row: MonitorBaseline | None,
) -> list[tuple[datetime, dict[str, float]]]:
    if row is None:
        return []
    payload = row.baseline if isinstance(row.baseline, dict) else {}
    if payload.get("version") != BASELINE_VERSION:
        return []
    if payload.get("target_metric") != COLUMN_PROFILE_METRIC:
        return []
    out: list[tuple[datetime, dict[str, float]]] = []
    for entry in payload.get("observations") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("values"), dict):
            continue
        try:
            ts = as_utc(datetime.fromisoformat(str(entry.get("ts"))))
        except (TypeError, ValueError):
            continue
        values = {
            str(k): float(v)
            for k, v in entry["values"].items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        }
        out.append((ts, values))
    return out


def score_column_profile(
    profile: ColumnProfile,
    observations: list[tuple[datetime, dict[str, float]]],
    *,
    now: datetime,
    params: AnomalyParams,
) -> dict[str, Any]:
    """Score each series against its own history. Null % counts in both directions; a distinct
    count only when it falls (growth is normal; a collapse is not)."""
    considered = (
        [o for o in observations if o[0].weekday() == now.weekday()]
        if params.seasonality
        else observations
    )
    deviations: list[dict[str, Any]] = []
    scored = 0
    for key, value in profile.series.items():
        priors = [vals[key] for _, vals in considered if key in vals][-params.window :]
        if len(priors) < params.min_points:
            continue
        scored += 1
        column, _, metric = key.partition(_SERIES_SEP)
        mean = statistics.fmean(priors)
        stddev = statistics.stdev(priors) if len(priors) > 1 else 0.0
        if metric == NULL_PCT:
            spread = max(stddev, _NULL_PCT_STDDEV_FLOOR)
            z = abs(value - mean) / spread
        else:
            spread = max(stddev, _DISTINCT_STDDEV_FLOOR_RATIO * abs(mean))
            z = (mean - value) / spread if spread > 0 and value < mean else 0.0
        deviations.append(
            {
                "column": column,
                "metric": metric,
                "value": round(value, 6),
                "mean": round(mean, 6),
                "z_score": round(z, 6),
            }
        )
    deviations.sort(key=lambda d: d["z_score"], reverse=True)
    payload: dict[str, Any] = {
        "target_metric": COLUMN_PROFILE_METRIC,
        "row_count": profile.row_count,
        "columns_measured": profile.columns_measured,
        "columns_total": profile.columns_total,
        "distinct_unavailable": list(profile.distinct_unavailable),
        "series_scored": scored,
        "window": params.window,
        "min_points": params.min_points,
        "seasonality": params.seasonality,
    }
    if scored == 0:
        payload["insufficient_history"] = True
        payload["reason"] = "insufficient_history"
        return payload
    payload["z_score"] = deviations[0]["z_score"]
    payload["deviations"] = deviations[:_COLUMN_PROFILE_REPORTED]
    return payload


def dump_profile_baseline(
    observations: list[tuple[datetime, dict[str, float]]], params: AnomalyParams
) -> dict[str, Any]:
    return {
        "version": BASELINE_VERSION,
        "target_metric": COLUMN_PROFILE_METRIC,
        "window": params.window,
        "seasonality": params.seasonality,
        "observations": [
            {"ts": ts.isoformat(), "values": values}
            for ts, values in observations[-params.retained_observations :]
        ],
    }


# ───────────────────────── baseline payload ─────────────────────────


def load_observations(row: MonitorBaseline | None, params: AnomalyParams) -> list[Observation]:
    """The usable prior observations from a stored baseline row."""
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
        out.append(Observation(ts=as_utc(ts), value=float(value)))
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
    """The prior values this run is scored against, newest-last."""
    considered = (
        [o for o in observations if o.ts.weekday() == now.weekday()]
        if params.seasonality
        else list(observations)
    )
    return [o.value for o in considered[-params.window :]]


def score(value: float, priors: list[float]) -> tuple[float, float, float, bool]:
    """``(z_score, mean, stddev, degenerate)`` for a value against its priors."""
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
    """The `observed_value` payload the registry's outcome strategy bands."""
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
    """A per-run executor for `anomaly` checks (the #592/#794 stateful pattern)."""

    def executor(check: Check) -> CheckOutcome:
        config = dict(check.config)
        now = datetime.now(UTC)
        try:
            params = anomaly_params(config)
            if params.target_metric == COLUMN_PROFILE_METRIC:
                return _column_profile_outcome(check, config, params, now)
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
            # Safe-marked messages (bad config, an unparseable timestamp cell) name the user's own
            # mistake and persist verbatim; anything else — a driver/SDK exception.
            log.warning(
                "anomaly_measurement_failed",
                check_id=str(check.id),
                connection_type=connection.type,
                error_type=type(exc).__name__,
            )
            # The offending CELL travels structurally, never inside the message (#989) — the message
            # is persisted verbatim and rendered wherever a result is shown.
            observed: dict[str, Any] | None = None
            unparsed = getattr(exc, "unparsed_value", None)
            if unparsed is not None:
                observed = {"unparsed_value": unparsed, "column": getattr(exc, "column", None)}
            return CheckOutcome(
                expectation_type=monitor_expectation_type(ANOMALY),
                success=False,
                errored=True,
                error_message=(safe_failure_reason(exc)),
                observed_value=observed,
            )
        # Read-modify-write: the observation list this run appends to must not be read by a
        # concurrent run of the same check, or one measurement is silently lost.
        row = get_baseline(session, check.id, for_update=persist)
        observations = load_observations(row, params)
        priors = eligible_values(observations, now=now, params=params)
        payload = build_score_payload(value, priors, params)
        if row is not None:
            # BOTH timestamps, because they answer different questions and only one of them moves:
            # `captured_at` is when learning STARTED (the row's first capture — no `onupdate`,
            # never bumped), `updated_at` is when the history THIS run was scored against was last
            # written.
            payload["baseline_captured_at"] = row.captured_at.isoformat()
            payload["baseline_updated_at"] = row.updated_at.isoformat()
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

    def _column_profile_outcome(
        check: Check, config: dict[str, Any], params: AnomalyParams, now: datetime
    ) -> CheckOutcome:
        profile = measure_column_profile(
            connection,
            table=target_table,
            schema=target_schema,
            catalog=target_catalog,
            secret_store=secret_store,
        )
        row = get_baseline(session, check.id, for_update=persist)
        observations = load_profile_observations(row)
        payload = score_column_profile(profile, observations, now=now, params=params)
        if row is not None:
            payload["baseline_captured_at"] = row.captured_at.isoformat()
            payload["baseline_updated_at"] = row.updated_at.isoformat()
        if persist:
            baseline = dump_profile_baseline([*observations, (now, profile.series)], params)
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
