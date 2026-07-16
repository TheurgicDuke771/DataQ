"""Monitor kinds — freshness & volume (ADR 0012), the non-GX computed monitors.

A monitor isn't a GX expectation: it runs a single **scalar SQL aggregate** against
the target table and turns the result into a badness ``metric_value`` that the
severity layer bands (higher = worse, ADR 0016), exactly like a GX check's
unexpected-%. This module is the pure, datasource-agnostic core:

* :func:`build_monitor_sql` — the aggregate query a SQL runner executes;
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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from backend.app.datasources.base import CheckOutcome, MonitorSpec
from backend.app.datasources.sql import is_sql_identifier

FRESHNESS = "freshness"
VOLUME = "volume"

# A monitor's `expectation_type` slot records the kind (the column is GX-shaped but
# monitors aren't GX); `monitor:<kind>` keeps it self-describing on the result row.
_EXPECTATION_PREFIX = "monitor:"


def monitor_expectation_type(kind: str) -> str:
    """The canonical ``expectation_type`` for a monitor kind — ``monitor:<kind>``.

    The single source of truth shared by the run path (stamps it on result rows),
    the author path (asserts the stored check's type matches its kind), and the
    frontend catalog — so the kind↔type pairing can't drift."""
    return f"{_EXPECTATION_PREFIX}{kind}"


class MonitorConfigError(ValueError):
    """A monitor check's config is missing/invalid (bad column, range, or kind)."""


def _ident(name: object, *, what: str) -> str:
    """Validate a SQL identifier (so it's safe to interpolate) and return it.

    Monitor config is user-authored, so the column/table/schema must be validated
    before they touch a query string (no bound-param slot for an identifier). The
    allowlist itself is the shared `datasources.sql` one (#428) — one source of
    truth with the profiler's validator."""
    if not isinstance(name, str) or not is_sql_identifier(name):
        raise MonitorConfigError(f"invalid {what} identifier: {name!r}")
    return name


def qualified_table(*, table: str, schema: str | None, catalog: str | None) -> str:
    """A dotted, identifier-validated ``[catalog.][schema.]table`` for a monitor query.

    A ``catalog`` with no ``schema`` is rejected: skipping the None ``schema`` would
    emit a 2-part ``catalog.table``, which Databricks/Unity Catalog resolves as
    ``schema.table`` (wrong object), not the intended 3-part name. So a catalog
    requires a schema — a misqualified-name footgun raised as a clear config error
    rather than a confusing "table not found" at query time."""
    if catalog is not None and schema is None:
        raise MonitorConfigError(
            f"monitor target {table!r} has a catalog but no schema — "
            "a catalog needs a schema (else catalog.table misresolves as schema.table)"
        )
    parts = [
        _ident(part, what=label)
        for part, label in ((catalog, "catalog"), (schema, "schema"), (table, "table"))
        if part is not None
    ]
    return ".".join(parts)


def build_monitor_sql(
    kind: str, *, table: str, schema: str | None, catalog: str | None, config: dict[str, Any]
) -> str:
    """The scalar-aggregate SQL a SQL runner executes for this monitor.

    ``freshness`` → ``SELECT MAX(<column>) ...``; ``volume`` → ``SELECT COUNT(*) ...``.
    Identifiers are validated (no bind slot for them), so a bad column/table raises
    :class:`MonitorConfigError` rather than building an injectable query. Dispatch
    is the #726 registry — a kind with no scalar-SQL form (the stateful kinds)
    refuses here rather than building a wrong query.
    """
    strategy = _strategy(kind)
    if strategy.build_sql is None:
        raise MonitorConfigError(f"monitor kind {kind!r} has no scalar-SQL form")
    target = qualified_table(table=table, schema=schema, catalog=catalog)
    return strategy.build_sql(target, config)


def _freshness_age_hours(max_timestamp: datetime, now: datetime) -> float:
    """Hours between ``MAX(column)`` and now (clamped at 0 — a clock-skew future
    timestamp isn't 'negatively stale')."""
    return max((now - max_timestamp).total_seconds() / 3600.0, 0.0)


def _as_aware_datetime(scalar: object, column: str) -> datetime:
    """Normalise a ``MAX(column)`` scalar to a tz-aware datetime for the age math.

    Accepts a ``datetime`` *or* a ``date`` (a DATE column's MAX is a ``date`` — e.g.
    Snowflake ``SIGNUP_DATE`` → ``datetime.date``; midnight is used). A naive value
    (Snowflake ``TIMESTAMP_NTZ`` returns no tzinfo) is assumed UTC, so subtracting a
    UTC ``now`` never raises offset-naive-vs-aware."""
    if isinstance(scalar, datetime):
        ts = scalar
    elif isinstance(scalar, date):  # a plain date (datetime is a date subclass — checked first)
        ts = datetime.combine(scalar, time.min)
    else:
        raise MonitorConfigError(
            f"freshness column {column!r} is not a date/timestamp (got {type(scalar).__name__})"
        )
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


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
# never a third parallel if-chain. `build_sql` receives the already-validated
# qualified target; identifiers can't be bound parameters, so validated
# interpolation is the correct construction (hence the S608 suppressions).


def _validate_freshness(config: dict[str, Any]) -> None:
    _ident(config.get("column"), what="freshness column")


def _freshness_sql(target: str, config: dict[str, Any]) -> str:
    column = _ident(config.get("column"), what="freshness column")
    return f"SELECT MAX({column}) FROM {target}"  # noqa: S608  # nosec B608


def _freshness_outcome(scalar: Any, config: dict[str, Any], now: datetime) -> CheckOutcome:
    expectation_type = monitor_expectation_type(FRESHNESS)
    column = _ident(config.get("column"), what="freshness column")
    if scalar is None:
        return CheckOutcome(
            expectation_type=expectation_type,
            success=False,
            errored=True,
            error_message=f"no rows: MAX({column}) is NULL, freshness can't be assessed",
            expected_value={"monitor": FRESHNESS, "column": column},
        )
    max_ts = _as_aware_datetime(scalar, column)
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
        expected_value={"monitor": FRESHNESS, "column": column},
    )


def _validate_volume(config: dict[str, Any]) -> None:
    _volume_bounds(config)


def _volume_sql(target: str, config: dict[str, Any]) -> str:
    return f"SELECT COUNT(*) FROM {target}"  # noqa: S608  # nosec B608


def _volume_outcome(scalar: Any, config: dict[str, Any], now: datetime) -> CheckOutcome:
    min_rows, max_rows = _volume_bounds(config)
    try:
        row_count = int(scalar)
    except (TypeError, ValueError) as exc:
        raise MonitorConfigError(f"volume COUNT(*) is not an integer: {scalar!r}") from exc
    deviation = _volume_deviation_pct(row_count, min_rows=min_rows, max_rows=max_rows)
    return CheckOutcome(
        expectation_type=monitor_expectation_type(VOLUME),
        success=deviation == 0.0,  # in range → pass; thresholds band the deviation
        metric_value=deviation,
        observed_value={"row_count": row_count, "deviation_pct": round(deviation, 3)},
        expected_value={"monitor": VOLUME, "min_rows": min_rows, "max_rows": max_rows},
    )


@dataclass(frozen=True)
class MonitorKindStrategy:
    """One monitor kind's behavior behind the #726 registry.

    ``validate_config`` is the DB-free structural gate; ``outcome`` bands the
    scalar; ``build_sql`` renders the scalar-aggregate over an already-validated
    qualified target — ``None`` for kinds with no scalar-SQL form (the stateful
    kinds, #592/#593, evaluate through their own path)."""

    kind: str
    validate_config: Callable[[dict[str, Any]], None]
    outcome: Callable[[Any, dict[str, Any], datetime], CheckOutcome]
    build_sql: Callable[[str, dict[str, Any]], str] | None


MONITOR_KIND_REGISTRY: dict[str, MonitorKindStrategy] = {
    FRESHNESS: MonitorKindStrategy(
        FRESHNESS, _validate_freshness, _freshness_outcome, _freshness_sql
    ),
    VOLUME: MonitorKindStrategy(VOLUME, _validate_volume, _volume_outcome, _volume_sql),
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
            outcomes.append(
                CheckOutcome(
                    expectation_type=monitor_expectation_type(spec.kind),
                    success=False,
                    errored=True,
                    error_message=str(exc),
                )
            )
    return outcomes


def evaluate_monitors(
    fetch_scalar: Callable[[str], Any],
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    monitors: list[MonitorSpec],
) -> list[CheckOutcome]:
    """Run a list of monitors over an already-open connection via `run_monitor_specs`,
    with the scalar sourced from a SQL aggregate. ``fetch_scalar`` runs a SQL string
    and returns its scalar — the runner closes over its connection, so this stays
    DB-free and unit-testable. Connection *establishment* failure is the runner's
    concern (it opens the connection before calling this)."""
    now = datetime.now(UTC)

    def scalar_for(spec: MonitorSpec) -> Any:
        sql = build_monitor_sql(
            spec.kind, table=table, schema=schema, catalog=catalog, config=spec.config
        )
        return fetch_scalar(sql)

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
    """
    from sqlalchemy import text  # lazy: keep sqlalchemy off this module's import cost

    with engine.connect() as conn:
        return evaluate_monitors(
            lambda sql: conn.execute(text(sql)).scalar(),
            table=table,
            schema=schema,
            catalog=catalog,
            monitors=monitors,
        )
