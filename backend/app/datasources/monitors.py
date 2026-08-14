"""Monitor kinds — freshness & volume (ADR 0012), the non-GX computed monitors.

A monitor isn't a GX expectation: it runs a single **scalar SQL aggregate** against
the target table and turns the result into a badness ``metric_value`` that the
severity layer bands (higher = worse, ADR 0016), exactly like a GX check's
unexpected-%. This module is the pure, datasource-agnostic core:

* :func:`build_monitor_statement` — the aggregate query a SQL runner executes;
* :func:`monitor_outcome` — scalar result + check config → ``CheckOutcome``.

The per-datasource *execution* (build an engine/URL, own its lifecycle) lives in
the SQL runners; everything here up to `run_monitors_over_engine` is connection-
free and fully unit-tested, and that one helper — the engine → one connection →
scalar loop the SQL runners share (#428) — is handed an already-built engine and
never constructs one. v1 monitors are SQL-datasource only (Snowflake / Unity
Catalog) plus the Iceberg runner's native scan scalars.

Semantics (locked):
* **freshness** — config ``{"column": <timestamp col>}``; metric = **age in hours**
  of ``MAX(column)`` vs now (higher = staler = worse). Banded by the check's
  warn/fail/critical thresholds (e.g. warn 24h, fail 48h).
* **volume** — config ``{"min_rows": N, "max_rows": M}``; metric = **% deviation**
  of ``COUNT(*)`` *outside* ``[N, M]`` (either direction; 0 when in range). Banded
  by the thresholds, so a drop *or* a spike past tolerance escalates.
* **schema_drift** — stateful; metric = **drifted-column count** vs the stored
  baseline (`services/schema_drift.py` owns the store).
* **anomaly** (#593) — stateful; config
  ``{"target_metric": "row_count" | "freshness_age_hours", "column": <ts col>,
  "window": 14, "min_points": 7, "seasonality": false}``; metric = the **z-score**
  (absolute deviations-from-the-mean, higher = worse) of this run's raw
  measurement against the check's own rolling observation history. The history
  and the scoring live in `services/anomaly.py`; this module only bands the
  payload it computes. Below ``min_points`` of usable history the outcome is a
  per-check ``skip``, never a fabricated pass/fail.
"""

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
from backend.app.datasources.base import CheckOutcome, MonitorSpec
from backend.app.datasources.sql import core_table, folding_identifier, is_sql_identifier
from backend.app.services.failure_classifier import safe_failure_reason

# `SafeMonitorError` lives in `core.errors` since #595 — the marker is an
# error-POLICY contract, and anchoring it there is what lets one
# `safe_failure_reason` serve the monitor loop, the run path and the dry-run
# preview instead of three isinstance branches that drift.

FRESHNESS = "freshness"
VOLUME = "volume"
SCHEMA_DRIFT = "schema_drift"
ANOMALY = "anomaly"

# A monitor's `expectation_type` slot records the kind (the column is GX-shaped but
# monitors aren't GX); `monitor:<kind>` keeps it self-describing on the result row.
_EXPECTATION_PREFIX = "monitor:"


def monitor_expectation_type(kind: str) -> str:
    """The canonical ``expectation_type`` for a monitor kind — ``monitor:<kind>``.

    The single source of truth shared by the run path (stamps it on result rows),
    the author path (asserts the stored check's type matches its kind), and the
    frontend catalog — so the kind↔type pairing can't drift."""
    return f"{_EXPECTATION_PREFIX}{kind}"


class MonitorConfigError(SafeMonitorError, ValueError):
    """A monitor check's config is missing/invalid (bad column, range, or kind).

    Safe-marked: these messages name the user's own config (a column name, a
    numeric range) and are the actionable half of a failed monitor.

    May also carry ``unparsed_value`` — the target cell that provoked the error.
    It is deliberately **not** interpolated into the message: the message is
    persisted verbatim and rendered in the UI, alerts and MCP output, none of
    which consult the suite's column policy, whereas the cell is target DATA and
    belongs behind it. Keeping the two apart is what lets the read layer redact
    one without having to locate it inside prose (#989).
    """

    def __init__(
        self, message: str, *, unparsed_value: object = None, column: str | None = None
    ) -> None:
        super().__init__(message)
        self.unparsed_value = unparsed_value
        self.column = column


def _ident(name: object, *, what: str) -> str:
    """Validate a SQL identifier (so it's safe to interpolate) and return it.

    Monitor config is user-authored, so the column/table/schema must be validated
    before they touch a query string (no bound-param slot for an identifier). The
    allowlist itself is the shared `datasources.sql` one (#428) — one source of
    truth with the profiler's validator."""
    if not isinstance(name, str) or not is_sql_identifier(name):
        raise MonitorConfigError(f"invalid {what} identifier: {name!r}")
    return name


def qualified_table(
    *, table: str, schema: str | None, catalog: str | None, dialect: Dialect | None = None
) -> TableClause:
    """An identifier-validated Core table clause for a monitor's target.

    A ``catalog`` with no ``schema`` is rejected: skipping the None ``schema`` would
    emit a 2-part ``catalog.table``, which Databricks/Unity Catalog resolves as
    ``schema.table`` (wrong object), not the intended 3-part name. So a catalog
    requires a schema — a misqualified-name footgun raised as a clear config error
    rather than a confusing "table not found" at query time.

    ``dialect`` is required whenever ``catalog`` is given (#936) — the 3-part form
    quotes the catalog/schema itself via the dialect's own identifier preparer,
    see `datasources.sql.core_table`. Validation happens here (for the
    `MonitorConfigError` message); construction is the shared
    `datasources.sql.core_table`, so the dialect does the quoting."""
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
    """The scalar-aggregate query a SQL runner executes for this monitor.

    ``freshness`` → ``SELECT MAX(<column>) ...``; ``volume`` → ``SELECT COUNT(*) ...``.

    Returns a **SQLAlchemy Core statement, not a SQL string** (#476). Identifiers
    have no bind slot, so the pre-Core version interpolated the validated name
    directly — which silently folded a quoted mixed-case column (``"Amount"`` was
    emitted bare and resolved as ``AMOUNT``, i.e. not found). Core hands the
    quoting decision to the dialect: lower-case names stay bare and fold exactly as
    they always did, anything else is quoted. Hand-rolled quoting could not fix
    this, because the quote character differs per dialect (Snowflake ``"`` vs
    Databricks backticks).

    ``dialect`` is only needed for a ``catalog``-qualified (3-part) target (#936);
    every caller with a ``catalog`` has a live dialect close at hand — the SQL
    runner's engine (`run_monitors_over_engine`) or the anomaly measurement's open
    connection (`services.anomaly.measure_metric`).

    A bad column/table raises :class:`MonitorConfigError` rather than building a
    wrong query. Dispatch is the #726 registry — a kind with no scalar form (the
    stateful kinds) refuses here.
    """
    strategy = _strategy(kind)
    if strategy.build_statement is None:
        raise MonitorConfigError(f"monitor kind {kind!r} has no scalar-SQL form")
    target = qualified_table(table=table, schema=schema, catalog=catalog, dialect=dialect)
    return strategy.build_statement(target, config)


def _freshness_age_hours(max_timestamp: datetime, now: datetime) -> float:
    """Hours between ``MAX(column)`` and now (clamped at 0 — a clock-skew future
    timestamp isn't 'negatively stale')."""
    return max((now - max_timestamp).total_seconds() / 3600.0, 0.0)


def _as_aware_datetime(scalar: object, source: str, *, column: str | None = None) -> datetime:
    """Normalise a freshness scalar to a tz-aware datetime for the age math.

    Accepts a ``datetime``, a ``date`` (a DATE column's MAX is a ``date`` — e.g.
    Snowflake ``SIGNUP_DATE`` → ``datetime.date``; midnight is used), **or an
    ISO-8601 string**. A naive value (Snowflake ``TIMESTAMP_NTZ`` returns no
    tzinfo) is assumed UTC, so subtracting a UTC ``now`` never raises
    offset-naive-vs-aware.

    The string case is not hypothetical: the **Databricks SQL connector returns a
    TIMESTAMP column's MAX as a str**, so every Unity Catalog freshness monitor
    errored with "is not a date/timestamp (got str)" — a documented-supported
    feature that had never once worked (found by running one against live UC;
    no unit test could see it, because the type comes from the driver).

    Parsed with ``fromisoformat`` rather than a general date parser on purpose:
    a permissive parser would also accept junk, and this is the same trap as the
    flat-file epoch case — a confident wrong instant is worse than a clear error.
    """
    if isinstance(scalar, datetime):
        ts = scalar
    elif isinstance(scalar, date):  # a plain date (datetime is a date subclass — checked first)
        ts = datetime.combine(scalar, time.min)
    elif isinstance(scalar, str):
        try:
            ts = datetime.fromisoformat(scalar)
        except ValueError:
            # The value is NOT in the message (#989). It is target data, and this
            # message is safe-marked — persisted verbatim and rendered wherever a
            # result is shown, none of which consults the suite's column policy.
            # It travels structurally instead, so the read layer can redact it the
            # same way it already redacts a failing sample row.
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
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def freshness_age_hours(
    scalar: Any, *, now: datetime, source: str, column: str | None = None
) -> float:
    """A ``MAX(timestamp)`` scalar → hours of staleness, the one age computation.

    Public because the `anomaly` kind (#593) measures the same quantity to feed
    its baseline: the driver-typed normalisation (`_as_aware_datetime` — a str
    from Databricks, a `date` from a Snowflake DATE column, a naive
    `TIMESTAMP_NTZ`) is exactly the part that must not be re-implemented per
    caller. #953 shipped for weeks because one datasource's driver returned a
    type the age math didn't accept.
    """
    return _freshness_age_hours(_as_aware_datetime(scalar, source, column=column), now)


def row_count_from_scalar(scalar: Any) -> int:
    """A ``COUNT(*)`` scalar → int, the one row-count normalisation.

    Also a driver boundary: Snowflake hands back a `Decimal`, Databricks an
    `int`, and a mocked test whatever the fixture invented. Shared by `volume`
    and by `anomaly`'s ``row_count`` target metric so both accept exactly the
    same set of driver spellings.
    """
    try:
        return int(scalar)
    except (TypeError, ValueError) as exc:
        raise MonitorConfigError(f"COUNT(*) is not an integer: {scalar!r}") from exc


def _volume_deviation_pct(row_count: int, *, min_rows: int, max_rows: int) -> float:
    """Percent the row count falls **outside** ``[min_rows, max_rows]`` (0 in range).

    Below the floor → shortfall vs the floor; above the ceiling → excess vs the
    ceiling. Symmetric so a drop and a spike both escalate. Guards a zero bound."""
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
    """Static (DB-free) validation of a monitor check's ``config`` — the *structural*
    checks that don't need a live query: a valid ``column`` identifier (freshness) or
    a well-formed ``min_rows``/``max_rows`` range (volume). Raises
    :class:`MonitorConfigError` on a bad/missing config or unknown kind.

    Shared by the **check-authoring** path (reject a malformed monitor at create/update
    time with a 422, not silently at the next run) and implicitly by the run path
    (`build_monitor_sql`/`monitor_outcome` re-derive the same checks). This is only the
    config-shape gate; threshold policy (e.g. freshness *requires* a threshold) and the
    SQL-datasource gate live in the service layer, which owns the Check + connection."""
    _strategy(kind).validate_config(config)


def monitor_outcome(
    kind: str, *, scalar: Any, config: dict[str, Any], now: datetime
) -> CheckOutcome:
    """Turn a monitor's scalar aggregate result into a ``CheckOutcome``.

    ``scalar`` is what ``build_monitor_sql`` selected: the ``MAX(column)`` timestamp
    (freshness) or the ``COUNT(*)`` (volume). The returned outcome carries a direct
    ``metric_value`` (age-hours / deviation-%) for the severity layer to band, plus
    a human ``observed_value``/``expected_value`` (no row data → no sample/PII). A
    freshness check on an empty table (``MAX`` is NULL) can't be assessed, so it's an
    operational ``error`` (#122), not a silent pass.
    """
    return _strategy(kind).outcome(scalar, config, now)


# ───────────────────── per-kind strategies (#726) ─────────────────────
#
# Adding a monitor kind = one strategy entry in MONITOR_KIND_REGISTRY below —
# never a third parallel if-chain. `build_statement` receives the already-validated
# target as a Core table clause and returns a Core `Select`; nothing here builds a
# SQL string, so identifier quoting is the dialect's job at execution time (#476)
# and there is no interpolation left for Bandit's S608/B608 to flag.


def freshness_column(config: dict[str, Any]) -> str | None:
    """The freshness column, or ``None`` for **arrival-time** freshness (#520).

    Omitting ``column`` means "measure when the data last *landed*" rather than
    the newest timestamp *inside* it. Only datasources with a native arrival time
    can answer that — a flat file has one (the object's last-modified), a
    warehouse table does not — so the SQL builder still demands a column and
    `check_service` gates the column-less form to flat-file connections at author
    time rather than letting it fail at run time.

    The two measure genuinely different things and a flat file wants both
    available: an in-file ``MAX(load_ts)`` misses "the producer stopped sending
    files entirely" (the newest file is old but its rows look fine), while
    arrival time misses "files keep landing but the rows inside are stale".
    """
    column = config.get("column")
    return None if column is None else _ident(column, what="freshness column")


def _validate_freshness(config: dict[str, Any]) -> None:
    freshness_column(config)


def _freshness_statement(target: TableClause, config: dict[str, Any]) -> Select[Any]:
    from sqlalchemy import column as sql_column
    from sqlalchemy import func, select

    # Required here, not optional: a SQL table has no arrival time to fall back to.
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
    # NOTE: freshness has no in-config bound (unlike volume's min/max_rows), so
    # the binary fallback is unconditionally `success=True` — "stale" is only
    # defined by a threshold. A freshness check WITHOUT a fail/critical age
    # threshold therefore always resolves `pass` no matter how stale (the metric
    # is computed but never banded). The check-create path (the monitor-authoring
    # slice) MUST require a freshness threshold so this never ships as silent green.
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
    """schema_drift needs no required config; ``ignore_columns`` (optional) must
    be a list of plain identifiers — they're compared against introspected names,
    never interpolated into SQL, but the allowlist keeps garbage out early."""
    ignore = config.get("ignore_columns")
    if ignore is None:
        return
    if not isinstance(ignore, list):
        raise MonitorConfigError(f"ignore_columns must be a list of column names: {ignore!r}")
    for name in ignore:
        _ident(name, what="ignored column")


def _schema_drift_outcome(scalar: Any, config: dict[str, Any], now: datetime) -> CheckOutcome:
    """Band a schema diff (#592). ``scalar`` is the diff payload the stateful
    executor computed (`services/schema_drift.py` — it owns the baseline store and
    introspection; this stays DB-free): either a first-run capture notice or the
    added/removed/type_changed detail. ``metric_value`` = drifted-column count,
    banded by the check's ADR-0016 thresholds like every other monitor metric."""
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
#
# The model is deliberately simple and explainable (issue #593): a rolling-window
# z-score over the check's OWN measurement history, with optional day-of-week
# seasonality. No ML dependency, no hidden state — every number that produced a
# verdict is in `observed_value`, which is what makes the band debuggable from
# the metric-trend view (#594) instead of being an oracle.

ROW_COUNT_METRIC = "row_count"
FRESHNESS_AGE_METRIC = "freshness_age_hours"
# What an anomaly monitor can measure. Both are RAW quantities, not other
# monitors' banded metrics: an anomaly check is self-contained, so it never
# depends on a sibling check existing, being enabled, or having run first.
ANOMALY_TARGET_METRICS = (ROW_COUNT_METRIC, FRESHNESS_AGE_METRIC)

_ANOMALY_DEFAULT_WINDOW = 14
_ANOMALY_DEFAULT_MIN_POINTS = 7
_ANOMALY_MIN_WINDOW = 3
_ANOMALY_MAX_WINDOW = 90
# Every observation the baseline may retain, seasonal case: `window` per weekday.
_ANOMALY_SEASONAL_WEEKDAYS = 7

# The z-score reported when the history has ZERO spread (every prior observation
# identical) and this run's value differs. The true z is +inf there, and infinity
# is not a usable answer: `severity.extract_metric` drops a non-finite Decimal as
# "no bandable metric", which would silently resolve the check to `pass` —
# maximal deviation reported as clean. A large finite sentinel bands as critical
# under any sane threshold and stores/trends without special-casing NUMERIC.
ANOMALY_DEGENERATE_Z = 99.0


@dataclass(frozen=True)
class AnomalyParams:
    """A validated `anomaly` check config.

    ``window`` is how many prior observations are scored against (and, in the
    seasonal case, how many *per weekday*); ``min_points`` is the cold-start
    floor below which the check reports `skip` rather than a verdict.
    """

    target_metric: str
    column: str | None
    window: int
    min_points: int
    seasonality: bool

    @property
    def retained_observations(self) -> int:
        """How many raw observations the baseline keeps.

        Non-seasonal: exactly ``window`` — the scoring set is the window. Seasonal:
        ``window * 7``, because the scoring set is "the last ``window`` observations
        that share today's weekday" and a daily schedule delivers roughly one
        matching observation per seven. Deliberately a raw-observation ring rather
        than seven per-weekday buckets: one list keeps the payload inspectable and
        lets a schedule change (daily → hourly) re-fill the window naturally
        instead of stranding six empty buckets.
        """
        return self.window * (_ANOMALY_SEASONAL_WEEKDAYS if self.seasonality else 1)


def _anomaly_int(config: dict[str, Any], key: str, default: int, *, low: int, high: int) -> int:
    """One bounded integer out of an anomaly config.

    ``bool`` is rejected explicitly — it is an ``int`` subclass, so ``True`` would
    otherwise sail through as a window of 1. An integral ``float`` (``14.0``, what
    a JSON client can send for a whole number) is accepted; a fractional one is not.
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
    """Parse + validate an `anomaly` check's config, or raise `MonitorConfigError`.

    The single parse shared by the author-time gate, the run executor and the
    dry-run preview, so a config that saves is a config that runs.
    """
    target_metric = config.get("target_metric")
    if target_metric not in ANOMALY_TARGET_METRICS:
        raise MonitorConfigError(
            f"anomaly target_metric must be one of {', '.join(ANOMALY_TARGET_METRICS)}: "
            f"{target_metric!r}"
        )
    column = config.get("column")
    if target_metric == FRESHNESS_AGE_METRIC:
        # Required, not optional: the underlying measurement is `MAX(<column>)`,
        # and a SQL table has no arrival time to fall back on (the flat-file
        # column-less form of `freshness` does not reach this kind — see
        # `check_service.ANOMALY_CAPABLE_TYPES`). Demanding it here makes a
        # missing column a 422 at author time instead of an error every night.
        column = _ident(column, what="anomaly freshness column")
    elif column is not None:
        # Known key, inapplicable metric. Silently ignoring it would leave the
        # author believing the anomaly watches that column when it watches
        # COUNT(*) — the same class of quiet-wrong the #476 fold produced.
        raise MonitorConfigError(
            f"anomaly column applies only to target_metric={FRESHNESS_AGE_METRIC!r}; "
            f"{ROW_COUNT_METRIC!r} measures COUNT(*) and takes no column"
        )
    else:
        column = None
    window = _anomaly_int(
        config, "window", _ANOMALY_DEFAULT_WINDOW, low=_ANOMALY_MIN_WINDOW, high=_ANOMALY_MAX_WINDOW
    )
    # Upper-bounded by the window: a min_points above it could never be reached,
    # so the check would skip forever while looking configured.
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
    """Band an anomaly score (#593). ``scalar`` is the payload the stateful
    executor computed (`services/anomaly.py` — it owns the measurement and the
    baseline store; this stays DB-free).

    Two shapes:

    * ``insufficient_history`` → a **skip** outcome. Not a pass: a monitor that
      hasn't learned anything yet has made no assertion about the data, and a
      fake green would count toward the health score and hide that.
    * otherwise → ``metric_value`` = the z-score, banded by the check's ADR-0016
      thresholds exactly like every other monitor metric (higher = worse).
    """
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
    """One monitor kind's behavior behind the #726 registry.

    ``validate_config`` is the DB-free structural gate; ``outcome`` bands the
    scalar; ``build_statement`` renders the scalar-aggregate as a Core `Select`
    over an already-validated target — ``None`` for kinds with no scalar-SQL form
    (the stateful kinds, #592/#593, evaluate through their own path)."""

    kind: str
    validate_config: Callable[[dict[str, Any]], None]
    outcome: Callable[[Any, dict[str, Any], datetime], CheckOutcome]
    build_statement: Callable[[TableClause, dict[str, Any]], Select[Any]] | None


MONITOR_KIND_REGISTRY: dict[str, MonitorKindStrategy] = {
    FRESHNESS: MonitorKindStrategy(
        FRESHNESS, _validate_freshness, _freshness_outcome, _freshness_statement
    ),
    VOLUME: MonitorKindStrategy(VOLUME, _validate_volume, _volume_outcome, _volume_statement),
    # Stateful (#592): no scalar-SQL form — the run path routes it through the
    # baseline-diff executor in `services/schema_drift.py`, never run_monitors.
    SCHEMA_DRIFT: MonitorKindStrategy(
        SCHEMA_DRIFT, _validate_schema_drift, _schema_drift_outcome, None
    ),
    # Stateful (#593): the executor in `services/anomaly.py` takes its own raw
    # measurement (reusing the freshness/volume statement builders), scores it
    # against the rolling baseline, and hands the payload here to be banded.
    ANOMALY: MonitorKindStrategy(ANOMALY, _validate_anomaly, _anomaly_outcome, None),
}

# Derived, never hand-maintained: the authoring allowlist (check_service) and the
# run-path partition (run_service) both key off this, so registering a kind above
# is the ONLY step that widens them. Registration is IMPORT-TIME ONLY — an entry
# in the dict literal above (the #592/#593 pattern), never a runtime mutation:
# every derived value (this tuple, the authoring allowlist, runners' advertised
# capability sets) snapshots at import, so a late registration would be half
# visible (dispatchable but unauthorable/unroutable). Tests may monkeypatch the
# registry for isolation; production code must not.
MONITOR_KINDS = tuple(MONITOR_KIND_REGISTRY)
# The run-path partition (#592): scalar kinds go to the runners' `run_monitors`
# (gated by their advertised capability, #429); stateful kinds go to the
# session-aware executor the worker injects (they need the baseline store).
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
    """Band a list of monitors given a per-spec scalar source, one ``CheckOutcome``
    each, in order. ``scalar_for`` returns the monitor's scalar (``MAX(column)`` /
    ``COUNT(*)``) — the only datasource-specific bit: a SQL runner builds+runs a
    query (`evaluate_monitors`), the Iceberg runner computes it natively
    (``scan().count()`` / a column ``MAX``). DB-free and unit-testable.

    A monitor that can't be evaluated — bad column/range (config error) or its
    scalar source raised (e.g. unknown column) — yields an ``errored`` outcome for
    *that* check only; its siblings still run (mirrors `CheckRunner`'s per-check
    `error`, #122). The scalar source must **not** swallow a datasource-establishment
    failure (open connection / load catalog): callers do that before the loop so it
    propagates and fails the whole run."""
    outcomes: list[CheckOutcome] = []
    for spec in monitors:
        try:
            outcomes.append(
                monitor_outcome(spec.kind, scalar=scalar_for(spec), config=spec.config, now=now)
            )
        except Exception as exc:  # one bad monitor errors, never its siblings
            # Safe-marked messages persist verbatim; everything else is CLASSIFIED
            # (#900). This message lands in `results.observed_value` -> the
            # run-detail API -> the UI, a sink the logger-level scrubber never sees
            # (CLAUDE.md §10 protects logs, not DB columns), and Azure storage
            # exceptions carry the full SAS-signed URL in their text (#828) — so a
            # raw `str(exc)` here would persist a live credential. Every sibling
            # path already classified before persisting; this was the one that
            # did not. See `SafeMonitorError` for why this isn't blanket.
            # A cell the error is *about* travels in `observed_value`, never in
            # the message (#989) — the message is persisted verbatim, while this
            # field passes through the read layer's column-policy redaction.
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
    """Run a list of monitors over an already-open connection via `run_monitor_specs`,
    with the scalar sourced from a SQL aggregate. ``fetch_scalar`` executes a Core
    statement and returns its scalar — the runner closes over its connection, so this
    stays DB-free and unit-testable. Connection *establishment* failure is the runner's
    concern (it opens the connection before calling this).

    The statement stays uncompiled all the way to the connection so the **connection's
    own dialect** renders it (#476) — that is what makes identifier quoting correct
    per warehouse instead of guessed here. ``dialect`` is the same connection's
    dialect, needed only for a ``catalog``-qualified (3-part) target (#936) — it
    picks the quote character for the catalog/schema, which Core's ``schema=``
    slot can't do on its own for a pre-assembled namespace."""
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
    """Run monitor checks over ONE connection from ``engine``, one outcome each.

    The execution edge the SQL runners (Snowflake / Unity Catalog) share (#428):
    opens a single connection and sources every monitor's scalar from it via
    `evaluate_monitors` (a bad monitor errors only itself; a connection-level
    failure propagates and fails the whole run — the open happens before the
    per-monitor loop). The engine's lifecycle (build + dispose) belongs to the
    caller — the seam #427 threads a per-run shared engine through.

    ``engine.dialect`` is threaded through unconditionally as `evaluate_monitors`'s
    ``dialect`` (#936) — cheap to pass, and it's what makes a Unity Catalog
    ``catalog``-qualified target quote-correct; Snowflake's ``catalog=None`` never
    reaches the code path that uses it.
    """
    with engine.connect() as conn:
        return evaluate_monitors(
            lambda statement: conn.execute(statement).scalar(),
            table=table,
            schema=schema,
            catalog=catalog,
            monitors=monitors,
            dialect=engine.dialect,
        )
