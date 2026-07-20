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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.sql import Select, TableClause

from backend.app.datasources.base import CheckOutcome, MonitorSpec
from backend.app.datasources.sql import core_table, folding_identifier, is_sql_identifier

FRESHNESS = "freshness"
VOLUME = "volume"
SCHEMA_DRIFT = "schema_drift"

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


def qualified_table(*, table: str, schema: str | None, catalog: str | None) -> TableClause:
    """An identifier-validated Core table clause for a monitor's target.

    A ``catalog`` with no ``schema`` is rejected: skipping the None ``schema`` would
    emit a 2-part ``catalog.table``, which Databricks/Unity Catalog resolves as
    ``schema.table`` (wrong object), not the intended 3-part name. So a catalog
    requires a schema — a misqualified-name footgun raised as a clear config error
    rather than a confusing "table not found" at query time.

    Validation happens here (for the `MonitorConfigError` message); construction is
    the shared `datasources.sql.core_table`, so the dialect does the quoting."""
    if catalog is not None and schema is None:
        raise MonitorConfigError(
            f"monitor target {table!r} has a catalog but no schema — "
            "a catalog needs a schema (else catalog.table misresolves as schema.table)"
        )
    for part, label in ((catalog, "catalog"), (schema, "schema"), (table, "table")):
        if part is not None:
            _ident(part, what=label)
    return core_table(table=table, schema=schema, catalog=catalog)


def build_monitor_statement(
    kind: str, *, table: str, schema: str | None, catalog: str | None, config: dict[str, Any]
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

    A bad column/table raises :class:`MonitorConfigError` rather than building a
    wrong query. Dispatch is the #726 registry — a kind with no scalar form (the
    stateful kinds) refuses here.
    """
    strategy = _strategy(kind)
    if strategy.build_statement is None:
        raise MonitorConfigError(f"monitor kind {kind!r} has no scalar-SQL form")
    target = qualified_table(table=table, schema=schema, catalog=catalog)
    return strategy.build_statement(target, config)


def _freshness_age_hours(max_timestamp: datetime, now: datetime) -> float:
    """Hours between ``MAX(column)`` and now (clamped at 0 — a clock-skew future
    timestamp isn't 'negatively stale')."""
    return max((now - max_timestamp).total_seconds() / 3600.0, 0.0)


def _as_aware_datetime(scalar: object, source: str) -> datetime:
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
            raise MonitorConfigError(
                f"freshness value from {source} is not a parseable timestamp: {scalar[:40]!r}"
            ) from None
    else:
        raise MonitorConfigError(
            f"freshness value from {source} is not a date/timestamp "
            f"(got {type(scalar).__name__})"
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
    max_ts = _as_aware_datetime(scalar, source)
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
    fetch_scalar: Callable[[Select[Any]], Any],
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    monitors: list[MonitorSpec],
) -> list[CheckOutcome]:
    """Run a list of monitors over an already-open connection via `run_monitor_specs`,
    with the scalar sourced from a SQL aggregate. ``fetch_scalar`` executes a Core
    statement and returns its scalar — the runner closes over its connection, so this
    stays DB-free and unit-testable. Connection *establishment* failure is the runner's
    concern (it opens the connection before calling this).

    The statement stays uncompiled all the way to the connection so the **connection's
    own dialect** renders it (#476) — that is what makes identifier quoting correct
    per warehouse instead of guessed here."""
    now = datetime.now(UTC)

    def scalar_for(spec: MonitorSpec) -> Any:
        statement = build_monitor_statement(
            spec.kind, table=table, schema=schema, catalog=catalog, config=spec.config
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
    """
    with engine.connect() as conn:
        return evaluate_monitors(
            lambda statement: conn.execute(statement).scalar(),
            table=table,
            schema=schema,
            catalog=catalog,
            monitors=monitors,
        )
