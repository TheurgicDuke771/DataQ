"""anomaly monitor kind — the stateful z-score engine (#593, ADR 0012)."""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
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
    aggregate_value,
    anomaly_params,
    build_monitor_statement,
    column_distinct_probe,
    column_profile_statement,
    freshness_age_hours,
    history_day,
    monitor_expectation_type,
    monitor_outcome,
    period_history_statement,
    period_statement,
    qualified_table,
    row_count_from_scalar,
)
from backend.app.db.models import Check, Connection, MonitorBaseline, Run
from backend.app.services import seasonal, seasonal_period_service
from backend.app.services.coverage_settings_service import CheckSettings, SuiteCoverage
from backend.app.services.failure_classifier import safe_failure_reason
from backend.app.services.monitor_baseline import get_baseline, insert_baseline_if_absent
from backend.app.services.profile_service import (
    ProfileUnsupportedError,
    _open_connection,
    build_columns_query,
    resolve_effective_schema,
)
from backend.app.services.severity import derive_status

log = get_logger(__name__)

# Bump only alongside a real payload-shape change; an unrecognised version is treated as "no usable
# history" (cold start) rather than mis-read — a wrong baseline is worse than a slow one.
BASELINE_VERSION = 1
#: The ``source`` of a stored point that was seeded from the table, not measured by a run.
BACKFILLED = "backfilled"


@dataclass(frozen=True)
class Observation:
    """One recorded measurement: when it was taken (UTC) and what it was."""

    ts: datetime
    value: float
    #: False for a point seeded from the table's own rows (ADR 0048): what the table says
    #: today about the past, not what it held when a run looked.
    observed: bool = True


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
    settled: tuple[date, date] | None = None,
) -> float:
    """This run's raw measurement of the configured ``target_metric``. A per-period metric
    measures ``settled``, the period the caller will record the point under (ADR 0048); left
    out, it is the most recent settled period as of ``now``."""
    try:
        with _open_connection(connection, secret_store) as conn:
            dialect = conn.dialect if catalog is not None else None
            if params.period is not None:
                start, end = settled or params.period.settled(now)
                statement = period_statement(
                    qualified_table(table=table, schema=schema, catalog=catalog, dialect=dialect),
                    params.period,
                    start=start,
                    end=end,
                )
            elif params.target_metric == ROW_COUNT_METRIC:
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
    if params.period is not None:
        if params.period.value_column is None:
            return float(row_count_from_scalar(scalar))
        # SUM over no rows is NULL: nothing was sold in the period, which is a zero.
        summed = aggregate_value(scalar, source=f"SUM({params.period.value_column})")
        return 0.0 if summed is None else summed
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
    # Points bucketed another way (a different date column, days against weeks) are not this
    # check's history.
    if payload.get("period") != _period_signature(params):
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
        out.append(
            Observation(
                ts=as_utc(ts), value=float(value), observed=entry.get("source") != BACKFILLED
            )
        )
    return out


def dump_baseline(
    observations: list[Observation], params: AnomalyParams, seed: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The JSONB payload for a set of observations (already trimmed by the caller). ``seed``
    is what seeding a per-period check's history came to (``stored_seed``)."""
    return {
        "version": BASELINE_VERSION,
        "target_metric": params.target_metric,
        "window": params.window,
        "seasonality": params.seasonality,
        **({"period": signature} if (signature := _period_signature(params)) else {}),
        **({"seed": seed} if seed is not None else {}),
        "observations": [
            {
                "ts": o.ts.isoformat(),
                "value": o.value,
                # Written only on a seeded point, so every other baseline keeps its shape.
                **({} if o.observed else {"source": BACKFILLED}),
            }
            for o in observations
        ],
    }


#: Seeding is tried again on later runs after a failure, this many times in all: a transient
#: timeout should not cost the check its history, and a table that can never be scanned
#: should not be scanned on every run for ever.
SEED_ATTEMPTS = 3


def stored_seed(row: MonitorBaseline | None, params: AnomalyParams) -> dict[str, Any] | None:
    """What seeding came to for the history this check holds now: ``status`` (``done``,
    ``off`` or ``failed``), and for a ``done`` the ``points`` and where they start ``from``.
    ``None`` when seeding has not been tried for points bucketed this way."""
    if row is None or not isinstance(row.baseline, dict):
        return None
    payload = row.baseline
    if payload.get("version") != BASELINE_VERSION:
        return None
    if payload.get("target_metric") != params.target_metric:
        return None
    if payload.get("period") != _period_signature(params):
        return None
    seed = payload.get("seed")
    return dict(seed) if isinstance(seed, dict) else None


def seeding_due(params: AnomalyParams, seed: dict[str, Any] | None) -> bool:
    """Whether this run should read the table's past: never tried, tried and failed fewer
    than ``SEED_ATTEMPTS`` times, or switched on after having been off."""
    spec = params.period
    if spec is None or not spec.backfill:
        return False
    if seed is None or seed.get("status") == "off":
        return True
    return seed.get("status") == "failed" and int(seed.get("attempts", 1)) < SEED_ATTEMPTS


def _period_signature(params: AnomalyParams) -> dict[str, Any] | None:
    """What a per-period check's stored points were bucketed by; ``None`` for other metrics
    (absent from their payload, so baselines written before this stay readable). The time
    zone and settle delay are not part of it: they decide when a period is judged, not which
    rows are in it."""
    spec = params.period
    if spec is None:
        return None
    return {
        "date_column": spec.date_column,
        "unit": spec.unit,
        "value_column": spec.value_column,
    }


def seed_history(
    connection: Connection,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    params: AnomalyParams,
    secret_store: SecretStore,
    before: date,
) -> list[Observation]:
    """The periods before ``before`` as the table's own rows describe them today, oldest
    first, each marked as backfilled (ADR 0048). One scan, over at most the years kept.

    History starts at the period after the first one that has rows: that first period is
    partial unless the table's first row happened to open it (a table that began on a
    Thursday has a four-day first week), and a short point would widen the spread every
    later run is scored against. A period with no rows after that counts as zero, as it
    would have had a run judged it, and nothing is invented before the table began.
    """
    spec = params.period
    if spec is None:
        return []
    start = spec.start_of(before) - spec.history_periods * spec.length
    with _open_connection(connection, secret_store) as conn:
        dialect = conn.dialect if catalog is not None else None
        rows = conn.execute(
            period_history_statement(
                qualified_table(table=table, schema=schema, catalog=catalog, dialect=dialect),
                spec,
                start=start,
                end=before,
            )
        ).all()
    totals: dict[date, float] = {}
    for day_value, measure in rows:
        period = spec.start_of(history_day(day_value))
        if not start <= period < before:
            continue  # a session-zone edge can put a boundary row a day outside the range
        if spec.value_column is None:
            amount = float(row_count_from_scalar(measure))
        else:
            amount = aggregate_value(measure, source=f"SUM({spec.value_column})") or 0.0
        totals[period] = totals.get(period, 0.0) + amount
    if not totals:
        return []
    out: list[Observation] = []
    period = min(totals) + spec.length
    while period < before:
        out.append(
            Observation(
                ts=datetime.combine(period, time.min, tzinfo=UTC),
                value=totals.get(period, 0.0),
                observed=False,
            )
        )
        period += spec.length
    return out


def observation_time(
    params: AnomalyParams, now: datetime, settled: tuple[date, date] | None = None
) -> tuple[datetime, dict[str, Any]]:
    """When this run's point is dated, and the period fields its result carries. A per-period
    point is dated at the start of its period (a calendar label, stored as midnight UTC)."""
    spec = params.period
    if spec is None:
        return now, {}
    start, end = settled or spec.settled(now)
    return datetime.combine(start, time.min, tzinfo=UTC), {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "unit": spec.unit,
            "timezone": spec.timezone,
            "settle_hours": spec.settle_hours,
        }
    }


def trim(observations: list[Observation], params: AnomalyParams) -> list[Observation]:
    """Keep only the most recent `retained_observations` (chronological order in)."""
    retained = params.retained_observations
    return observations[-retained:] if retained else []


# ───────────────────────── scoring (pure) ─────────────────────────


def _considered(
    observations: list[Observation], now: datetime, params: AnomalyParams
) -> list[Observation]:
    """The prior points this run is scored against, newest-last."""
    considered = (
        [o for o in observations if o.ts.weekday() == now.weekday()]
        if params.seasonality
        else list(observations)
    )
    return considered[-params.window :]


def eligible_values(
    observations: list[Observation], *, now: datetime, params: AnomalyParams
) -> list[float]:
    """The prior values this run is scored against, newest-last."""
    return [o.value for o in _considered(observations, now, params)]


@dataclass(frozen=True)
class _Seeding:
    """What seeding produced, and what the result says about it."""

    points: list[Observation]
    report: dict[str, Any]


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


def _banded(check: Check, z_score: float | None) -> str:
    """The tier ``z_score`` lands in under the check's own thresholds."""
    if z_score is None:
        return "skipped"
    return derive_status(
        success=True,
        metric_value=Decimal(str(round(z_score, 6))),
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
    )


def declared_payload(
    check: Check,
    compared: seasonal.YearlyScore,
    *,
    without: seasonal.YearlyScore,
    ordinary_z: float | None,
    calendar: seasonal.Calendar,
) -> dict[str, Any]:
    """``observed_value.declared``: which entry the judged period fell in and how it was
    matched, how many earlier points the calendar kept out of the level, and the z-score and
    tier the run would have had with no calendar attached."""
    z_without = without.z_score if without.applied else ordinary_z
    z_with = compared.z_score if compared.applied and compared.banded else None
    with_status = _banded(check, z_with) if compared.applied else _banded(check, ordinary_z)
    without_status = _banded(check, z_without)
    return {
        "attached": [str(e.id) for e in calendar.entries],
        "missing": [str(i) for i in calendar.missing],
        "inside": compared.declared.as_dict() if compared.declared is not None else None,
        "excluded_from_level": compared.excluded_from_level,
        "z_score_without": None if z_without is None else round(z_without, 6),
        "verdict_without": without_status,
        "verdict_changed": with_status != without_status,
    }


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
    coverage: SuiteCoverage | None = None,
    run: Run | None = None,  # the shared builder signature; unused here
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Callable[[Check], CheckOutcome]:
    """A per-run executor for `anomaly` checks (the #592/#794 stateful pattern).

    ``coverage`` resolves the shape an inheriting automatic check runs on (ADR 0047); without
    it every check runs on its own stored config.
    """

    def executor(check: Check) -> CheckOutcome:
        inherited = coverage.for_check(check) if coverage is not None else None
        outcome = _measure_and_score(check, inherited)
        if inherited is None or outcome.expected_value is None:
            return outcome
        # The result names what it was scored against: the stored config is not it.
        return replace(outcome, expected_value={**outcome.expected_value, **inherited.provenance()})

    def _measure_and_score(check: Check, inherited: CheckSettings | None) -> CheckOutcome:
        config = dict(check.config)
        if inherited is not None:
            config = inherited.config(config)
        now = clock()
        try:
            params = anomaly_params(config)
            if inherited is not None:
                params = replace(params, retain_seasonal_horizon=True)
            if params.target_metric == COLUMN_PROFILE_METRIC:
                return _column_profile_outcome(check, config, params, now)
            # Resolved once: the period measured is the period the point is stored under.
            settled = params.period.settled(now) if params.period is not None else None
            value = measure_metric(
                connection,
                table=target_table,
                schema=target_schema,
                catalog=target_catalog,
                params=params,
                secret_store=secret_store,
                now=now,
                settled=settled,
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
        at, period_fields = observation_time(params, now, settled)
        # The three-year scan runs BEFORE the row is locked: holding the lock across it would
        # make a concurrent run of this check time out waiting. Decided on an unlocked read;
        # the locked read below settles who keeps what.
        attempt = None
        if persist and settled is not None:
            seen = stored_seed(get_baseline(session, check.id), params)
            if seeding_due(params, seen):
                attempt = _seed(check, params, settled[0], seen)
        # Read-modify-write: the observation list this run appends to must not be read by a
        # concurrent run of the same check, or one measurement is silently lost.
        row = get_baseline(session, check.id, for_update=persist)
        observations = load_observations(row, params)
        seed = stored_seed(row, params)
        seeded_now = attempt is not None and seeding_due(params, seed)
        if attempt is not None and seeded_now:
            # Still wanted now the row is ours (a concurrent run may have seeded meanwhile).
            # Periods a run already judged keep their observed point.
            held = {o.ts for o in observations}
            added = [o for o in attempt.points if o.ts not in held and o.ts < at]
            observations = sorted([*observations, *added], key=lambda o: o.ts)
            seed = dict(attempt.report)
            if seed["status"] == "done":
                seed["points"] = len(added)  # what was kept, not what the scan returned
        elif params.period is not None and seed is None and persist:
            seed = {"status": "off"}  # backfill is switched off and has never run
        # A per-period metric is about a period, not about when the run happened: its point
        # is stamped with the period, scored against earlier periods only, and written once.
        dated = params.period is not None
        earlier = [o for o in observations if o.ts < at] if dated else observations
        already_recorded = params.period is not None and any(o.ts == at for o in observations)
        considered = _considered(earlier, at, params)
        payload = {
            **build_score_payload(value, [o.value for o in considered], params),
            **period_fields,
        }
        if params.yearly:
            by_day = {o.ts.date(): o.value for o in earlier}
            calendar = (
                seasonal_period_service.calendar_for(session, params.seasonal_periods)
                if params.seasonal_periods
                else None
            )
            compared = seasonal.History(by_day, params, calendar).score(at.date(), value)
            payload["yearly"] = compared.as_dict()
            if calendar is not None:
                # What the calendar did to this run, beside what the run would have scored
                # without it, so a reader can tell when the calendar changed the verdict.
                payload["declared"] = declared_payload(
                    check,
                    compared,
                    without=seasonal.History(by_day, params).score(at.date(), value),
                    ordinary_z=payload.get("z_score"),
                    calendar=calendar,
                )
            if compared.applied and not compared.banded:
                # A first-year declared period: a rise was expected and its size unknown.
                payload["within_declared_period"] = True
                payload["reason"] = "within_declared_period"
            if compared.applied and compared.centre is not None:
                # `mean` and `stddev` are what the run was scored against, in the metric's
                # own units, so `|value - mean| / stddev` is the z-score for any reader.
                payload.update(
                    {
                        "z_score": round(compared.z_score or 0.0, 6),
                        "mean": round(compared.centre, 6),
                        "stddev": round(compared.spread or 0.0, 6),
                        "deviation": round(value - compared.centre, 6),
                        "degenerate_stddev": compared.degenerate,
                    }
                )
        if row is not None:
            # BOTH timestamps, because they answer different questions and only one of them moves:
            # `captured_at` is when learning STARTED (the row's first capture — no `onupdate`,
            # never bumped), `updated_at` is when the history THIS run was scored against was last
            # written.
            payload["baseline_captured_at"] = row.captured_at.isoformat()
            payload["baseline_updated_at"] = row.updated_at.isoformat()
        if params.period is not None:
            # How much of what this run was scored against the table said about its own
            # past, as opposed to what a run saw at the time; and what seeding came to, on
            # every result, so a check that started empty says why long after its first run.
            payload["history"] = {
                "backfilled": sum(1 for o in considered if not o.observed),
                "observed": sum(1 for o in considered if o.observed),
                "seeding": seed["status"] if seed else None,
            }
            if seeded_now:
                payload["seeded"] = seed
            # False on a second run inside the same settled period: measured again for this
            # result, but the stored point is the one written when the period was first judged.
            payload["recorded"] = persist and not already_recorded
        changed = seeded_now or not already_recorded
        if persist and changed:
            points = (
                observations
                if already_recorded
                else [*observations, Observation(ts=at, value=value)]
            )
            if dated:
                # A changed settle delay can judge an older period after a newer one; scoring
                # and trimming both take the list as oldest first.
                points = sorted(points, key=lambda o: o.ts)
            baseline = dump_baseline(trim(points, params), params, seed)
            if row is None:
                insert_baseline_if_absent(
                    session, check_id=check.id, kind=ANOMALY, baseline=baseline
                )
            else:
                row.baseline = baseline
        elif not persist:
            payload["dry_run"] = True
        return monitor_outcome(ANOMALY, scalar=payload, config=config, now=now)

    def _seed(
        check: Check, params: AnomalyParams, before: date, earlier: dict[str, Any] | None
    ) -> _Seeding:
        """Read the table's past for a per-period check (ADR 0048)."""
        attempts = int((earlier or {}).get("attempts", 0)) + 1
        try:
            points = seed_history(
                connection,
                table=target_table,
                schema=target_schema,
                catalog=target_catalog,
                params=params,
                secret_store=secret_store,
                before=before,
            )
        except Exception as exc:
            # The check still judges this period; it starts with no history and says why.
            # Tried again on later runs, up to SEED_ATTEMPTS in all.
            log.warning(
                "anomaly_history_seed_failed",
                check_id=str(check.id),
                connection_type=connection.type,
                error_type=type(exc).__name__,
                attempt=attempts,
            )
            return _Seeding(
                [],
                {
                    "status": "failed",
                    "reason": safe_failure_reason(exc),
                    "attempts": attempts,
                    "will_retry": attempts < SEED_ATTEMPTS,
                },
            )
        report: dict[str, Any] = {"status": "done", "points": len(points), "attempts": attempts}
        if points:
            report["from"] = points[0].ts.date().isoformat()
        return _Seeding(points, report)

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
