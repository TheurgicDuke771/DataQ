"""Monitor kinds (ADR 0012) — the pure, datasource-agnostic core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.engine.interfaces import Dialect
    from sqlalchemy.sql import Select, TableClause

from backend.app.core.errors import SafeMonitorError
from backend.app.core.timeutil import as_utc
from backend.app.datasources.base import CheckOutcome, MonitorSpec
from backend.app.datasources.sql import core_table, folding_identifier, is_sql_identifier
from backend.app.services.failure_classifier import safe_failure_reason

FRESHNESS = "freshness"
VOLUME = "volume"
SCHEMA_DRIFT = "schema_drift"
ANOMALY = "anomaly"

_EXPECTATION_PREFIX = "monitor:"


def monitor_expectation_type(kind: str) -> str:
    """The canonical ``expectation_type`` for a monitor kind — ``monitor:<kind>``;
    single source of truth for run path, author path and frontend catalog.
    """
    return f"{_EXPECTATION_PREFIX}{kind}"


class MonitorConfigError(SafeMonitorError, ValueError):
    """A monitor check's config is missing/invalid (safe-marked: messages name
    only the user's own config). ``unparsed_value`` — a target-DATA cell — is
    deliberately never interpolated into the persisted message (#989).
    """

    def __init__(
        self, message: str, *, unparsed_value: object = None, column: str | None = None
    ) -> None:
        super().__init__(message)
        self.unparsed_value = unparsed_value
        self.column = column


def _ident(name: object, *, what: str) -> str:
    """Validate a user-authored SQL identifier (no bound-param slot exists for
    identifiers) via the shared `datasources.sql` allowlist (#428).
    """
    if not isinstance(name, str) or not is_sql_identifier(name):
        raise MonitorConfigError(f"invalid {what} identifier: {name!r}")
    return name


def qualified_table(
    *, table: str, schema: str | None, catalog: str | None, dialect: Dialect | None = None
) -> TableClause:
    """An identifier-validated Core table clause for a monitor's target."""
    if catalog is not None and schema is None:
        raise MonitorConfigError(
            f"monitor target {table!r} has a catalog but no schema — "
            "a catalog needs a schema (else catalog.table misresolves as schema.table)"
        )
    for part, label in ((catalog, "catalog"), (schema, "schema"), (table, "table")):
        if part is not None:
            _ident(part, what=label)
    return core_table(table=table, schema=schema, catalog=catalog, dialect=dialect)


def build_monitor_statement(
    kind: str,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    config: dict[str, Any],
    dialect: Dialect | None = None,
) -> Select[Any]:
    """The scalar-aggregate query a SQL runner executes for this monitor."""
    strategy = _strategy(kind)
    if strategy.build_statement is None:
        raise MonitorConfigError(f"monitor kind {kind!r} has no scalar-SQL form")
    target = qualified_table(table=table, schema=schema, catalog=catalog, dialect=dialect)
    return strategy.build_statement(target, config)


def _freshness_age_hours(max_timestamp: datetime, now: datetime) -> float:
    """Hours between ``MAX(column)`` and now (clamped at 0 — a clock-skew future
    timestamp isn't 'negatively stale').
    """
    return max((now - max_timestamp).total_seconds() / 3600.0, 0.0)


def _as_aware_datetime(scalar: object, source: str, *, column: str | None = None) -> datetime:
    """Normalise a freshness scalar to a tz-aware datetime for the age math."""
    if isinstance(scalar, datetime):
        ts = scalar
    elif isinstance(scalar, date):  # a plain date (datetime is a date subclass — checked first)
        ts = datetime.combine(scalar, time.min)
    elif isinstance(scalar, str):
        try:
            ts = datetime.fromisoformat(scalar)
        except ValueError:
            # The value (target data) is NOT in the safe-marked message (#989);
            # it travels structurally so the read layer can redact it.
            raise MonitorConfigError(
                f"freshness value from {source} is not a parseable timestamp",
                unparsed_value=scalar,
                column=column,
            ) from None
    else:
        raise MonitorConfigError(
            f"freshness value from {source} is not a date/timestamp "
            f"(got {type(scalar).__name__})"
        )
    return as_utc(ts)


def freshness_age_hours(
    scalar: Any, *, now: datetime, source: str, column: str | None = None
) -> float:
    """A ``MAX(timestamp)`` scalar → hours of staleness, the ONE age computation."""
    return _freshness_age_hours(_as_aware_datetime(scalar, source, column=column), now)


def row_count_from_scalar(scalar: Any) -> int:
    """A ``COUNT(*)`` scalar → int — driver boundary (Snowflake `Decimal`,
    Databricks `int`); shared by `volume` and `anomaly` so spellings can't drift.
    """
    try:
        return int(scalar)
    except (TypeError, ValueError) as exc:
        raise MonitorConfigError(f"COUNT(*) is not an integer: {scalar!r}") from exc


def _volume_deviation_pct(row_count: int, *, min_rows: int, max_rows: int) -> float:
    """Percent the row count falls **outside** ``[min_rows, max_rows]`` (0 in range)."""
    if row_count < min_rows:
        return (min_rows - row_count) / min_rows * 100.0 if min_rows else 100.0
    if row_count > max_rows:
        return (row_count - max_rows) / max_rows * 100.0 if max_rows else 100.0
    return 0.0


def _volume_bounds(config: dict[str, Any]) -> tuple[int, int]:
    """Validate the ``min_rows``/``max_rows`` range from a volume check's config."""
    try:
        min_rows = int(config["min_rows"])
        max_rows = int(config["max_rows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MonitorConfigError(f"volume needs integer min_rows/max_rows: {config!r}") from exc
    if min_rows < 0 or max_rows < min_rows:
        raise MonitorConfigError(f"volume range must be 0 <= min_rows <= max_rows: {config!r}")
    return min_rows, max_rows


def validate_monitor_config(kind: str, config: dict[str, Any]) -> None:
    """Static (DB-free) structural validation of a monitor check's ``config``;
    raises :class:`MonitorConfigError`. Config-shape only — threshold policy and
    the SQL-datasource gate live in the service layer.
    """
    _strategy(kind).validate_config(config)


def monitor_outcome(
    kind: str, *, scalar: Any, config: dict[str, Any], now: datetime
) -> CheckOutcome:
    """Turn a monitor's scalar aggregate result into a ``CheckOutcome``."""
    return _strategy(kind).outcome(scalar, config, now)


# ───────────────────── per-kind strategies (#726) ─────────────────────
# Adding a kind = one MONITOR_KIND_REGISTRY entry, never a parallel if-chain.
# Nothing builds SQL strings — quoting is the dialect's job at execution (#476).


def freshness_column(config: dict[str, Any]) -> str | None:
    """The freshness column, or ``None`` for **arrival-time** freshness (#520)."""
    column = config.get("column")
    return None if column is None else _ident(column, what="freshness column")


def _validate_freshness(config: dict[str, Any]) -> None:
    freshness_column(config)


def _freshness_statement(target: TableClause, config: dict[str, Any]) -> Select[Any]:
    from sqlalchemy import column as sql_column
    from sqlalchemy import func, select

    # Required: a SQL table has no arrival time to fall back to.
    name = _ident(config.get("column"), what="freshness column")
    return select(func.max(sql_column(folding_identifier(name)))).select_from(target)


def _freshness_outcome(scalar: Any, config: dict[str, Any], now: datetime) -> CheckOutcome:
    expectation_type = monitor_expectation_type(FRESHNESS)
    column = freshness_column(config)
    source = f"MAX({column})" if column is not None else "file arrival time"
    expected: dict[str, Any] = {"monitor": FRESHNESS}
    expected["column" if column is not None else "source"] = column or "file_modified_time"
    if scalar is None:
        return CheckOutcome(
            expectation_type=expectation_type,
            success=False,
            errored=True,
            error_message=f"{source} is unavailable, freshness can't be assessed",
            expected_value=expected,
        )
    max_ts = _as_aware_datetime(scalar, source, column=column)
    age_hours = _freshness_age_hours(max_ts, now)
    # NOTE: "stale" is only defined by a threshold, so a freshness check without
    # one always resolves pass — the check-create path MUST require a threshold.
    return CheckOutcome(
        expectation_type=expectation_type,
        success=True,  # binary fallback when no thresholds; thresholds band the age
        metric_value=age_hours,
        observed_value={"max_timestamp": max_ts.isoformat(), "age_hours": round(age_hours, 3)},
        expected_value=expected,
    )


def _validate_volume(config: dict[str, Any]) -> None:
    _volume_bounds(config)


def _volume_statement(target: TableClause, config: dict[str, Any]) -> Select[Any]:
    from sqlalchemy import func, select

    return select(func.count()).select_from(target)


def _volume_outcome(scalar: Any, config: dict[str, Any], now: datetime) -> CheckOutcome:
    min_rows, max_rows = _volume_bounds(config)
    row_count = row_count_from_scalar(scalar)
    deviation = _volume_deviation_pct(row_count, min_rows=min_rows, max_rows=max_rows)
    return CheckOutcome(
        expectation_type=monitor_expectation_type(VOLUME),
        success=deviation == 0.0,  # in range → pass; thresholds band the deviation
        metric_value=deviation,
        observed_value={"row_count": row_count, "deviation_pct": round(deviation, 3)},
        expected_value={"monitor": VOLUME, "min_rows": min_rows, "max_rows": max_rows},
    )


def _validate_schema_drift(config: dict[str, Any]) -> None:
    """No required config; optional ``ignore_columns`` must be plain identifiers
    (never interpolated into SQL — the allowlist just keeps garbage out early).
    """
    ignore = config.get("ignore_columns")
    if ignore is None:
        return
    if not isinstance(ignore, list):
        raise MonitorConfigError(f"ignore_columns must be a list of column names: {ignore!r}")
    for name in ignore:
        _ident(name, what="ignored column")


def _schema_drift_outcome(scalar: Any, config: dict[str, Any], now: datetime) -> CheckOutcome:
    """Band a schema diff (#592); ``scalar`` is the payload from
    `services/schema_drift.py`. ``metric_value`` = drifted-column count.
    """
    if not isinstance(scalar, dict):
        raise MonitorConfigError(f"schema_drift expects a diff payload dict: {scalar!r}")
    expectation_type = monitor_expectation_type(SCHEMA_DRIFT)
    if scalar.get("baseline_captured"):
        return CheckOutcome(
            expectation_type=expectation_type,
            success=True,  # nothing to compare yet — the baseline is the reference
            metric_value=0.0,
            observed_value=dict(scalar),
            expected_value={"monitor": SCHEMA_DRIFT},
        )
    added = list(scalar.get("added", ()))
    removed = list(scalar.get("removed", ()))
    type_changed = list(scalar.get("type_changed", ()))
    drifted = len(added) + len(removed) + len(type_changed)
    return CheckOutcome(
        expectation_type=expectation_type,
        success=drifted == 0,  # binary fallback; thresholds band the count
        metric_value=float(drifted),
        observed_value=dict(scalar),
        expected_value={"monitor": SCHEMA_DRIFT, "drifted_columns": 0},
    )


# ───────────────────────── anomaly (#593) ─────────────────────────
# Deliberately simple/explainable: rolling z-score over the check's own history,
# optional day-of-week seasonality; every input to the verdict is in observed_value.

ROW_COUNT_METRIC = "row_count"
FRESHNESS_AGE_METRIC = "freshness_age_hours"
# RAW quantities, not other monitors' banded metrics — an anomaly check is
# self-contained and never depends on a sibling check.
ANOMALY_TARGET_METRICS = (ROW_COUNT_METRIC, FRESHNESS_AGE_METRIC)

_ANOMALY_DEFAULT_WINDOW = 14
_ANOMALY_DEFAULT_MIN_POINTS = 7
_ANOMALY_MIN_WINDOW = 3
_ANOMALY_MAX_WINDOW = 90
# Every observation the baseline may retain, seasonal case: `window` per weekday.
_ANOMALY_SEASONAL_WEEKDAYS = 7

# Z reported when the history has zero spread and this run differs.
ANOMALY_DEGENERATE_Z = 99.0


@dataclass(frozen=True)
class AnomalyParams:
    """A validated `anomaly` check config."""

    target_metric: str
    column: str | None
    window: int
    min_points: int
    seasonality: bool

    @property
    def retained_observations(self) -> int:
        """Raw observations kept: ``window``, or ``window * 7`` seasonal."""
        return self.window * (_ANOMALY_SEASONAL_WEEKDAYS if self.seasonality else 1)


def _anomaly_int(config: dict[str, Any], key: str, default: int, *, low: int, high: int) -> int:
    """One bounded integer from an anomaly config. ``bool`` rejected (int
    subclass — ``True`` would pass as 1); integral floats accepted (JSON clients).
    """
    raw = config.get(key, default)
    if isinstance(raw, bool):
        raise MonitorConfigError(f"anomaly {key} must be an integer, not a boolean")
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if not isinstance(raw, int):
        raise MonitorConfigError(f"anomaly {key} must be an integer: {raw!r}")
    if not low <= raw <= high:
        raise MonitorConfigError(f"anomaly {key} must be between {low} and {high}: {raw}")
    return raw


def anomaly_params(config: dict[str, Any]) -> AnomalyParams:
    """Parse + validate an `anomaly` config (single parse shared by author gate,
    run executor and dry-run — a config that saves is a config that runs).
    """
    target_metric = config.get("target_metric")
    if target_metric not in ANOMALY_TARGET_METRICS:
        raise MonitorConfigError(
            f"anomaly target_metric must be one of {', '.join(ANOMALY_TARGET_METRICS)}: "
            f"{target_metric!r}"
        )
    column = config.get("column")
    if target_metric == FRESHNESS_AGE_METRIC:
        # Required — SQL tables have no arrival-time fallback; missing column
        # must 422 at author time, not error every night.
        column = _ident(column, what="anomaly freshness column")
    elif column is not None:
        # Known key, inapplicable metric — silently ignoring would mislead the
        # author about what is watched.
        raise MonitorConfigError(
            f"anomaly column applies only to target_metric={FRESHNESS_AGE_METRIC!r}; "
            f"{ROW_COUNT_METRIC!r} measures COUNT(*) and takes no column"
        )
    else:
        column = None
    window = _anomaly_int(
        config, "window", _ANOMALY_DEFAULT_WINDOW, low=_ANOMALY_MIN_WINDOW, high=_ANOMALY_MAX_WINDOW
    )
    # Upper-bounded by window: a higher min_points would skip forever.
    min_points = _anomaly_int(
        config,
        "min_points",
        min(_ANOMALY_DEFAULT_MIN_POINTS, window),
        low=_ANOMALY_MIN_WINDOW,
        high=window,
    )
    seasonality = config.get("seasonality", False)
    if not isinstance(seasonality, bool):
        raise MonitorConfigError(f"anomaly seasonality must be true or false: {seasonality!r}")
    return AnomalyParams(
        target_metric=str(target_metric),
        column=column,
        window=window,
        min_points=min_points,
        seasonality=seasonality,
    )


def _validate_anomaly(config: dict[str, Any]) -> None:
    anomaly_params(config)


def _anomaly_outcome(scalar: Any, config: dict[str, Any], now: datetime) -> CheckOutcome:
    """Band an anomaly score (#593); ``scalar`` from `services/anomaly.py`."""
    if not isinstance(scalar, dict):
        raise MonitorConfigError(f"anomaly expects a score payload dict: {scalar!r}")
    params = anomaly_params(config)
    expectation_type = monitor_expectation_type(ANOMALY)
    expected: dict[str, Any] = {
        "monitor": ANOMALY,
        "target_metric": params.target_metric,
        "window": params.window,
        "min_points": params.min_points,
        "seasonality": params.seasonality,
    }
    if params.column is not None:
        expected["column"] = params.column
    if scalar.get("insufficient_history"):
        return CheckOutcome(
            expectation_type=expectation_type,
            success=True,  # not a verdict — `skipped` is what the status reads
            skipped=True,
            observed_value=dict(scalar),
            expected_value=expected,
        )
    z_score = scalar.get("z_score")
    if not isinstance(z_score, (int, float)) or isinstance(z_score, bool):
        raise MonitorConfigError(f"anomaly payload has no numeric z_score: {z_score!r}")
    return CheckOutcome(
        expectation_type=expectation_type,
        success=True,  # like freshness: "anomalous" is defined only by a threshold
        metric_value=float(z_score),
        observed_value=dict(scalar),
        expected_value=expected,
    )


@dataclass(frozen=True)
class MonitorKindStrategy:
    """One monitor kind's behavior behind the #726 registry; ``build_statement``
    is ``None`` for the stateful kinds (#592/#593, own evaluation path).
    """

    kind: str
    validate_config: Callable[[dict[str, Any]], None]
    outcome: Callable[[Any, dict[str, Any], datetime], CheckOutcome]
    build_statement: Callable[[TableClause, dict[str, Any]], Select[Any]] | None


MONITOR_KIND_REGISTRY: dict[str, MonitorKindStrategy] = {
    FRESHNESS: MonitorKindStrategy(
        FRESHNESS, _validate_freshness, _freshness_outcome, _freshness_statement
    ),
    VOLUME: MonitorKindStrategy(VOLUME, _validate_volume, _volume_outcome, _volume_statement),
    # Stateful (#592): routed via services/schema_drift.py, never run_monitors.
    SCHEMA_DRIFT: MonitorKindStrategy(
        SCHEMA_DRIFT, _validate_schema_drift, _schema_drift_outcome, None
    ),
    # Stateful (#593): services/anomaly.py measures, scores, hands payload here.
    ANOMALY: MonitorKindStrategy(ANOMALY, _validate_anomaly, _anomaly_outcome, None),
}

# Derived, never hand-maintained.
MONITOR_KINDS = tuple(MONITOR_KIND_REGISTRY)
# Run-path partition (#592): scalar kinds → runners' run_monitors; stateful
# kinds → the session-aware executor (they need the baseline store).
SCALAR_MONITOR_KINDS = tuple(
    k for k, s in MONITOR_KIND_REGISTRY.items() if s.build_statement is not None
)
STATEFUL_MONITOR_KINDS = tuple(
    k for k, s in MONITOR_KIND_REGISTRY.items() if s.build_statement is None
)


def _strategy(kind: str) -> MonitorKindStrategy:
    strategy = MONITOR_KIND_REGISTRY.get(kind)
    if strategy is None:
        raise MonitorConfigError(f"unknown monitor kind: {kind!r}")
    return strategy


def run_monitor_specs(
    scalar_for: Callable[[MonitorSpec], Any],
    *,
    monitors: list[MonitorSpec],
    now: datetime,
) -> list[CheckOutcome]:
    """Band monitors via a per-spec scalar source, one ``CheckOutcome`` each, in order."""
    outcomes: list[CheckOutcome] = []
    for spec in monitors:
        try:
            outcomes.append(
                monitor_outcome(spec.kind, scalar=scalar_for(spec), config=spec.config, now=now)
            )
        except Exception as exc:  # one bad monitor errors, never its siblings
            # Safe-marked messages persist verbatim; everything else is CLASSIFIED (#900) — the
            # logger scrubber never sees DB columns.
            observed: dict[str, Any] | None = None
            unparsed = getattr(exc, "unparsed_value", None)
            if unparsed is not None:
                observed = {"unparsed_value": unparsed, "column": getattr(exc, "column", None)}
            outcomes.append(
                CheckOutcome(
                    expectation_type=monitor_expectation_type(spec.kind),
                    success=False,
                    errored=True,
                    error_message=safe_failure_reason(exc),
                    observed_value=observed,
                )
            )
    return outcomes


def evaluate_monitors(
    fetch_scalar: Callable[[Select[Any]], Any],
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    monitors: list[MonitorSpec],
    dialect: Dialect | None = None,
) -> list[CheckOutcome]:
    """Run monitors over an already-open connection, scalars from SQL aggregates."""
    now = datetime.now(UTC)

    def scalar_for(spec: MonitorSpec) -> Any:
        statement = build_monitor_statement(
            spec.kind,
            table=table,
            schema=schema,
            catalog=catalog,
            config=spec.config,
            dialect=dialect,
        )
        return fetch_scalar(statement)

    return run_monitor_specs(scalar_for, monitors=monitors, now=now)


def run_monitors_over_engine(
    engine: Engine,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    monitors: list[MonitorSpec],
) -> list[CheckOutcome]:
    """Run monitor checks over ONE connection from ``engine`` (#428)."""
    with engine.connect() as conn:
        return evaluate_monitors(
            lambda statement: conn.execute(statement).scalar(),
            table=table,
            schema=schema,
            catalog=catalog,
            monitors=monitors,
            dialect=engine.dialect,
        )
