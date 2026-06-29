"""Monitor kinds — freshness & volume (ADR 0012), the non-GX computed monitors.

A monitor isn't a GX expectation: it runs a single **scalar SQL aggregate** against
the target table and turns the result into a badness ``metric_value`` that the
severity layer bands (higher = worse, ADR 0016), exactly like a GX check's
unexpected-%. This module is the pure, datasource-agnostic core:

* :func:`build_monitor_sql` — the aggregate query a SQL runner executes;
* :func:`monitor_outcome` — scalar result + check config → ``CheckOutcome``.

The per-datasource *execution* (open a connection, run the SQL, fetch the scalar)
lives in the SQL runners; this module never touches a connection, so it is fully
unit-tested. v1 monitors are SQL-datasource only (Snowflake / Unity Catalog).

Semantics (locked):
* **freshness** — config ``{"column": <timestamp col>}``; metric = **age in hours**
  of ``MAX(column)`` vs now (higher = staler = worse). Banded by the check's
  warn/fail/critical thresholds (e.g. warn 24h, fail 48h).
* **volume** — config ``{"min_rows": N, "max_rows": M}``; metric = **% deviation**
  of ``COUNT(*)`` *outside* ``[N, M]`` (either direction; 0 when in range). Banded
  by the thresholds, so a drop *or* a spike past tolerance escalates.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from backend.app.datasources.base import CheckOutcome

FRESHNESS = "freshness"
VOLUME = "volume"
MONITOR_KINDS = (FRESHNESS, VOLUME)

# A monitor's `expectation_type` slot records the kind (the column is GX-shaped but
# monitors aren't GX); `monitor:<kind>` keeps it self-describing on the result row.
_EXPECTATION_PREFIX = "monitor:"

# SQL identifier we're willing to interpolate into the aggregate. Monitor config is
# user-authored, so the column/table/schema must be validated before they touch a
# query string (no bound-param slot for an identifier). Snowflake/Databricks
# unquoted identifiers: a letter/underscore lead, then word chars or `$`.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class MonitorConfigError(ValueError):
    """A monitor check's config is missing/invalid (bad column, range, or kind)."""


def _ident(name: object, *, what: str) -> str:
    """Validate a SQL identifier (so it's safe to interpolate) and return it."""
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise MonitorConfigError(f"invalid {what} identifier: {name!r}")
    return name


def qualified_table(*, table: str, schema: str | None, catalog: str | None) -> str:
    """A dotted, identifier-validated ``[catalog.][schema.]table`` for a monitor query."""
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
    :class:`MonitorConfigError` rather than building an injectable query.
    """
    target = qualified_table(table=table, schema=schema, catalog=catalog)
    # `column` + every part of `target` are validated by `_ident` (strict identifier
    # regex) before interpolation. SQL identifiers can't be bound parameters, so
    # validated interpolation is the correct construction — not an injection vector
    # (hence the S608 suppressions).
    if kind == FRESHNESS:
        column = _ident(config.get("column"), what="freshness column")
        return f"SELECT MAX({column}) FROM {target}"  # noqa: S608
    if kind == VOLUME:
        return f"SELECT COUNT(*) FROM {target}"  # noqa: S608
    raise MonitorConfigError(f"unknown monitor kind: {kind!r}")


def _freshness_age_hours(max_timestamp: datetime, now: datetime) -> float:
    """Hours between ``MAX(column)`` and now (clamped at 0 — a clock-skew future
    timestamp isn't 'negatively stale')."""
    return max((now - max_timestamp).total_seconds() / 3600.0, 0.0)


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
    expectation_type = f"{_EXPECTATION_PREFIX}{kind}"
    if kind == FRESHNESS:
        column = _ident(config.get("column"), what="freshness column")
        if scalar is None:
            return CheckOutcome(
                expectation_type=expectation_type,
                success=False,
                errored=True,
                error_message=f"no rows: MAX({column}) is NULL, freshness can't be assessed",
                expected_value={"monitor": FRESHNESS, "column": column},
            )
        if not isinstance(scalar, datetime):
            raise MonitorConfigError(f"freshness column {column!r} is not a timestamp")
        age_hours = _freshness_age_hours(scalar, now)
        return CheckOutcome(
            expectation_type=expectation_type,
            success=True,  # binary fallback when no thresholds; thresholds band the age
            metric_value=age_hours,
            observed_value={"max_timestamp": scalar.isoformat(), "age_hours": round(age_hours, 3)},
            expected_value={"monitor": FRESHNESS, "column": column},
        )
    if kind == VOLUME:
        min_rows, max_rows = _volume_bounds(config)
        try:
            row_count = int(scalar)
        except (TypeError, ValueError) as exc:
            raise MonitorConfigError(f"volume COUNT(*) is not an integer: {scalar!r}") from exc
        deviation = _volume_deviation_pct(row_count, min_rows=min_rows, max_rows=max_rows)
        return CheckOutcome(
            expectation_type=expectation_type,
            success=deviation == 0.0,  # in range → pass; thresholds band the deviation
            metric_value=deviation,
            observed_value={"row_count": row_count, "deviation_pct": round(deviation, 3)},
            expected_value={"monitor": VOLUME, "min_rows": min_rows, "max_rows": max_rows},
        )
    raise MonitorConfigError(f"unknown monitor kind: {kind!r}")
