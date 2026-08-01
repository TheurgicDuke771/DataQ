"""Check CRUD — checks are GX expectations nested under a suite.

A check belongs to exactly one suite (FK + cascade). This layer validates the
suite exists, enforces the v1 monitor-kind limit, and validates the check's
`config` at author time: expectation-kind checks resolve + construct their GX
expectation class (#651 — the same translation the runner performs, pulled
forward so garbage 422s instead of persisting and only failing at run time);
validation against live data remains the dry-run path, not CRUD.

Kind gating (ADR 0012): every kind in the schema CHECK is now authorable —
`expectation`, the freshness/volume monitors, the stateful `schema_drift` (#592)
and `anomaly` (#593) monitors, and `comparison` (ADR 0015). The allowlist is
DERIVED from the monitor registry (`_V1_SUPPORTED_KINDS`), so registering a kind
widens it; what stays hand-written per kind is its datasource capability set and
any config/threshold guardrail that needs the DB.

FastAPI-free like the sibling services: takes a `Session`, returns ORM models,
raises `DataQError` subclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import (
    ANOMALY,
    FRESHNESS,
    MONITOR_KINDS,
    SCHEMA_DRIFT,
    MonitorConfigError,
    monitor_expectation_type,
    validate_monitor_config,
)
from backend.app.db.models import (
    COMPARISON_KIND,
    DQ_DIMENSIONS,
    ORCHESTRATION_PROVIDERS,
    Check,
    CheckVersion,
    Connection,
    Result,
    Run,
    Suite,
)
from backend.app.services.check_dimension import is_valid_dimension, resolve_dimension
from backend.app.services.custom_sql import (
    SQL_QUERYABLE_TYPES,
    CustomSqlInvalidError,
    is_custom_sql,
    validate_custom_sql_check,
    validate_query,
)
from backend.app.services.run_target import SuiteTargetInvalidError, resolve_target
from backend.app.services.suite_service import get_suite

log = get_logger(__name__)

# Authorable kinds: GX expectations, every registered monitor kind (ADR 0012 —
# freshness/volume, schema_drift #592, anomaly #593), and `comparison` (ADR 0015).
# Derived from the registry, never hand-maintained: registering a kind there is
# the one step that widens this.
_V1_SUPPORTED_KINDS = {"expectation", *MONITOR_KINDS, COMPARISON_KIND}

# Canonical expectation_types for a comparison check (mirrors `monitor:<kind>`).
# `comparison:records` = row grain; `comparison:columns` = FDC's per-column
# value grain (#799).
COMPARISON_EXPECTATION_TYPE = "comparison:records"
COMPARISON_EXPECTATION_TYPES = ("comparison:records", "comparison:columns")

# The flat-file datasources. Their runner reads the resolved batch into pandas, so
# volume is a row count and freshness is either an in-frame MAX or — uniquely —
# the object's arrival time (#520).
FILE_TYPES = frozenset({"adls_gen2", "s3"})

# Datasources whose runner implements `run_monitors` (a `MonitorRunner`) — the
# author-time gate for freshness/volume checks. The SQL datasources compute the
# aggregate in-warehouse; Iceberg computes it natively (`scan().count()` / a column
# MAX, ADR 0030); flat files compute it over the resolved batch (#520). This is
# broader than `SQL_QUERYABLE_TYPES` (which gates *custom SQL* — neither Iceberg nor
# a flat file is SQL-queryable), so the two stay distinct. Kept in sync with the
# runners' `supported_monitor_kinds` capability (#429).
MONITOR_CAPABLE_TYPES = frozenset({*SQL_QUERYABLE_TYPES, "iceberg", *FILE_TYPES})

# schema_drift (#592) introspects the target's column shape through the
# baseline-diff executor (`services/schema_drift.py`) — not the runner — so its
# coverage is derived separately even though it now matches: flat files reached it
# (Parquet footer / CSV header sample) before they had a `run_monitors` at all.
SCHEMA_DRIFT_CAPABLE_TYPES = frozenset({*MONITOR_CAPABLE_TYPES, *FILE_TYPES})

# anomaly (#593) is stateful like schema_drift, but it does not reach the runner
# EITHER — its executor takes its own measurement by running the freshness/volume
# Core statement over a live SQLAlchemy connection. That is a SQL capability, not
# a `run_monitors` one, so the set is narrower than `MONITOR_CAPABLE_TYPES`
# despite freshness/volume working on Iceberg and flat files: those two compute
# their scalars natively INSIDE their runners, which stateful kinds never touch.
# Widening this is a real change (a per-datasource measurement seam on
# `anomaly.measure_metric`), not a set edit — refusing at author time keeps that
# honest instead of saving a check that errors every night.
ANOMALY_CAPABLE_TYPES = frozenset(SQL_QUERYABLE_TYPES)

# Each stateful kind's capability set; scalar kinds all use MONITOR_CAPABLE_TYPES.
_CAPABLE_TYPES_BY_KIND: dict[str, frozenset[str]] = {
    SCHEMA_DRIFT: SCHEMA_DRIFT_CAPABLE_TYPES,
    ANOMALY: ANOMALY_CAPABLE_TYPES,
}


class CheckNotFoundError(DataQError):
    status_code = 404
    code = "check_not_found"


class CheckConfigInvalidError(DataQError):
    status_code = 422
    code = "check_config_invalid"


class CheckVersionNotFoundError(DataQError):
    status_code = 404
    code = "check_version_not_found"


# The unique-constraint name on `check_versions(check_id, version_no)` — the
# concurrency backstop a racing double-edit trips. Matched against the DB error
# so only that collision becomes a 409 (see `update_check`).
_VERSION_UNIQUE_CONSTRAINT = "uq_check_versions_check_version"


class CheckEditConflictError(DataQError):
    # A concurrent edit of the same check raced on the `(check_id, version_no)`
    # snapshot backstop (#309-adjacent C3): a benign write-write collision, so 409
    # (reload + retry) — not an unhandled 500. read-modify-write is only as safe as
    # its unique constraint (no row-locking on the check-then-write today).
    status_code = 409
    code = "check_edit_conflict"


def _connection_type(session: Session, suite: Suite) -> str:
    """The datasource type of the suite's connection — for custom-SQL gating.

    The suite's `connection_id` FK is NOT NULL, so the connection always exists.
    """
    connection = session.get(Connection, suite.connection_id)
    assert connection is not None
    return connection.type


def validate_kind(kind: str) -> None:
    """Reject an unsupported check kind (422). Shared by CRUD and suite import.

    Supported: `expectation`, every registered monitor kind, and `comparison`
    (ADR 0012 / 0015). A kind outside the set has no run path, so authoring one
    would persist a check that can never execute."""
    if kind not in _V1_SUPPORTED_KINDS:
        raise CheckConfigInvalidError(
            f"check kind {kind!r} is not supported in v1",
            detail={"kind": kind, "supported": sorted(_V1_SUPPORTED_KINDS)},
        )


def validate_dimension(dimension: str | None) -> str | None:
    """Reject a DQ dimension outside the seven canonical ones (422), ADR 0038.

    `None` passes through — it means "not specified, derive it", not "invalid".
    The vocabulary is closed precisely so the #889 coverage view can say "you have
    no Timeliness checks"; a typo'd free-text value would make that a lie.
    """
    if dimension is not None and not is_valid_dimension(dimension):
        raise CheckConfigInvalidError(
            f"unknown DQ dimension {str(dimension)[:_ERROR_ECHO_MAX_CHARS]!r}",
            detail={
                "dimension": str(dimension)[:_ERROR_ECHO_MAX_CHARS],
                "supported": sorted(DQ_DIMENSIONS),
            },
        )
    return dimension


# Mirrors the `checks.name` / `checks.expectation_type` column widths (db/models.py)
# and the REST `CheckCreate`/`CheckUpdate` Field bounds (api/v1/checks.py). Enforced
# here too so every caller with no Pydantic layer of its own — the MCP `create_check`
# tool today, plus suite import's direct `Check(...)` construction — gets a clean 422
# instead of a raw `StringDataRightTruncation` from Postgres on an over-length
# INSERT/UPDATE.
_NAME_MAX_LEN = 256
_EXPECTATION_TYPE_MAX_LEN = 128


def validate_lengths(*, name: str | None, expectation_type: str | None) -> None:
    if name is not None and not (1 <= len(name) <= _NAME_MAX_LEN):
        raise CheckConfigInvalidError(
            f"name must be 1-{_NAME_MAX_LEN} characters",
            detail={"field": "name", "length": len(name), "max": _NAME_MAX_LEN},
        )
    if expectation_type is not None and not (
        1 <= len(expectation_type) <= _EXPECTATION_TYPE_MAX_LEN
    ):
        raise CheckConfigInvalidError(
            f"expectation_type must be 1-{_EXPECTATION_TYPE_MAX_LEN} characters",
            detail={
                "field": "expectation_type",
                "length": len(expectation_type),
                "max": _EXPECTATION_TYPE_MAX_LEN,
            },
        )


def validate_threshold_ordering(
    *,
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> None:
    """Reject negative or out-of-order severity thresholds at author time (422).

    #568: `severity.derive_status` assumes thresholds are ordered ``warn <= fail
    <= critical`` (higher `metric_value` is worse) and skips any unset threshold
    as if it were +infinity — it has no ordering guard of its own, so nothing
    upstream of it ever rejected an inverted (e.g. 90/50/10) or negative set.
    Runtime tolerance if a bad row still slips through (a pre-existing row is
    NOT migrated by this fix): `derive_status` checks `critical` first, so an
    inverted set degrades to a surprising-but-defined band rather than a crash —
    see the comment there.

    Shared by every kind (`create_check`, `update_check`, suite import) because
    the tier derivation is kind-agnostic — a monitor's freshness/positive-value
    gate (`validate_monitor_check`) is a narrower, kind-specific rule and does
    not overlap with this one. Compares only the pairs that are both set: an
    unset threshold is "no bound", not "0", so it never participates in the
    ordering check (only in the non-negative one, and only if actually set).
    """
    for field, value in (
        ("warn_threshold", warn_threshold),
        ("fail_threshold", fail_threshold),
        ("critical_threshold", critical_threshold),
    ):
        if value is not None and value < 0:
            raise CheckConfigInvalidError(
                f"{field} must be non-negative, not {value}",
                detail={"field": field, "value": str(value)},
            )
    pairs = (
        ("warn_threshold", warn_threshold, "fail_threshold", fail_threshold),
        ("fail_threshold", fail_threshold, "critical_threshold", critical_threshold),
        ("warn_threshold", warn_threshold, "critical_threshold", critical_threshold),
    )
    for lower_field, lower, upper_field, upper in pairs:
        if lower is not None and upper is not None and lower > upper:
            raise CheckConfigInvalidError(
                f"{lower_field} ({lower}) must be <= {upper_field} ({upper}) — "
                "severity thresholds band an increasingly bad metric, so they "
                "must be non-decreasing",
                detail={lower_field: str(lower), upper_field: str(upper)},
            )


def validate_monitor_check(
    kind: str,
    config: dict[str, Any],
    *,
    expectation_type: str,
    connection_type: str,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> None:
    """Validate a monitor check of any kind at author time (create/update).

    Four gates, each a 422:
    1. **Capable datasource only** — a scalar monitor (freshness/volume) needs a
       runner with `run_monitors` (`MONITOR_CAPABLE_TYPES`); a stateful kind runs
       through its own executor instead, so each carries its own set —
       schema_drift (#592) also introspects flat files
       (`SCHEMA_DRIFT_CAPABLE_TYPES`), anomaly (#593) measures over a live SQL
       connection and so is SQL-only (`ANOMALY_CAPABLE_TYPES`). Rejecting an
       unsupported pairing up front keeps the failure a 422, not a failed run.
    2. **expectation_type matches the kind** — a monitor's type is the canonical
       ``monitor:<kind>``. The run path keys off `kind`, so a mismatched/junk type
       would still execute but mislabel every result row (and could smuggle a
       custom-SQL type past its guardrails) — keep the stored row self-consistent.
    3. **Config shape** — a valid `column` (freshness) or `min_rows`/`max_rows` range
       (volume), via the shared `monitors.validate_monitor_config`.
    4. **Freshness and anomaly need a positive threshold** — neither has an
       in-config bound (unlike volume's min/max rows), so without a fail/critical
       threshold they would always resolve `pass` no matter how stale or how far
       from the baseline (the silent-green footgun flagged in the #426 review); a
       *zero* threshold is the inverse footgun (always fail). Require a positive
       fail-or-critical threshold so the metric bands meaningfully. For anomaly
       the threshold is a z-score — "how many standard deviations from normal" —
       so it is the sensitivity knob, not an incidental setting.
    """
    capable = _CAPABLE_TYPES_BY_KIND.get(kind, MONITOR_CAPABLE_TYPES)
    if connection_type not in capable:
        raise CheckConfigInvalidError(
            f"{kind} monitor checks are not supported on a {connection_type!r} datasource",
            detail={
                "connection_type": connection_type,
                "supported": sorted(capable),
            },
        )
    expected_type = monitor_expectation_type(kind)
    if expectation_type != expected_type:
        raise CheckConfigInvalidError(
            f"a {kind} monitor's expectation_type must be {expected_type!r}, not "
            f"{expectation_type!r}",
            detail={"kind": kind, "expectation_type": expectation_type},
        )
    try:
        validate_monitor_config(kind, config)
    except MonitorConfigError as exc:
        raise CheckConfigInvalidError(str(exc), detail={"kind": kind, "config": config}) from exc
    # A column-less freshness monitor measures ARRIVAL time, which only a
    # datasource with a native per-object timestamp can answer (#520). Gate it
    # here: `monitors.freshness_column` accepts the omission structurally so the
    # flat-file runner can use it, and without this the SQL builder would raise
    # only at RUN time — a check that saves clean and then errors every night.
    if kind == FRESHNESS and config.get("column") is None and connection_type not in FILE_TYPES:
        raise CheckConfigInvalidError(
            f"a freshness monitor on {connection_type!r} needs a timestamp column — "
            "only flat-file datasources can measure freshness from file arrival time",
            detail={"kind": kind, "connection_type": connection_type},
        )
    if kind == FRESHNESS and not _has_positive_threshold(fail_threshold, critical_threshold):
        raise CheckConfigInvalidError(
            "a freshness monitor needs a positive fail or critical age threshold (hours) — "
            "without one it can never fail (no threshold) or always fails (zero)",
            detail={"kind": kind},
        )
    if kind == ANOMALY and not _has_positive_threshold(fail_threshold, critical_threshold):
        raise CheckConfigInvalidError(
            "an anomaly monitor needs a positive fail or critical z-score threshold "
            "(standard deviations from the learned baseline) — without one it can never "
            "fail (no threshold) or always fails (zero)",
            detail={"kind": kind},
        )


def _has_positive_threshold(fail: Decimal | None, critical: Decimal | None) -> bool:
    """Whether a fail or critical threshold is set to a positive value."""
    return (fail is not None and fail > 0) or (critical is not None and critical > 0)


def _validate_comparison_keys(keys: Any) -> None:
    """`config.keys` — the join keys the diff matches rows on (ADR 0015 §1).

    A non-empty list; each entry is either a column name (same on both sides) or
    a `{"source": ..., "target": ...}` mapping when the names differ.
    """
    if not isinstance(keys, list) or not keys:
        raise CheckConfigInvalidError(
            "a comparison check needs config.keys — a non-empty list of join key columns",
            detail={"field": "config.keys"},
        )
    for i, key in enumerate(keys):
        if isinstance(key, str) and key.strip():
            continue
        if (
            isinstance(key, dict)
            and isinstance(key.get("source"), str)
            and key["source"].strip()
            and isinstance(key.get("target"), str)
            and key["target"].strip()
        ):
            continue
        raise CheckConfigInvalidError(
            "each comparison join key must be a column name or a "
            '{"source": ..., "target": ...} mapping of non-empty names',
            detail={"field": f"config.keys[{i}]"},
        )


def _validate_side_query(query: Any, *, connection_type: str, field: str) -> None:
    """A per-side SQL projection must be read-only (ADR 0019 rules) and its side's
    connection must be SQL-queryable (Iceberg/flat-file reads are native, not SQL)."""
    if connection_type not in SQL_QUERYABLE_TYPES:
        raise CheckConfigInvalidError(
            f"{field}: a comparison SQL query requires a SQL datasource, "
            f"not {connection_type!r}",
            detail={"field": field, "supported": sorted(SQL_QUERYABLE_TYPES)},
        )
    try:
        validate_query(query)
    except CustomSqlInvalidError as exc:
        raise CheckConfigInvalidError(
            f"invalid comparison query in {field}: {exc.message}",
            detail={"field": field, **(exc.detail or {})},
        ) from exc


def _reject_oversized_config(config: dict[str, Any]) -> None:
    """422 when any config string (keys included) exceeds the #651 cap.

    Shared by the expectation and comparison validators so no kind can persist
    a multi-megabyte config that every GET/version snapshot/export re-emits.
    """
    oversized = _find_oversized_string(config)
    if oversized is not None:
        # Bound the WHOLE path, not just each segment: deep nesting grows the
        # accumulated path ~200 chars per level, which would round-trip an
        # arbitrarily large echo through the 422 envelope and the error log.
        if len(oversized) > _ERROR_ECHO_MAX_CHARS:
            oversized = oversized[:_ERROR_ECHO_MAX_CHARS] + "…"
        raise CheckConfigInvalidError(
            f"config value at {oversized} exceeds {_CONFIG_STRING_MAX_CHARS} characters",
            detail={"path": oversized, "max_chars": _CONFIG_STRING_MAX_CHARS},
        )


def validate_comparison_check(
    session: Session,
    *,
    config: dict[str, Any],
    expectation_type: str,
    source_connection_id: uuid.UUID | None,
    suite_connection_type: str,
) -> None:
    """Author-time validation for `kind='comparison'` checks (ADR 0015). All 422s.

    The suite supplies the target under test; the check supplies the source
    (baseline): a connection ref + a suite-target-shaped dataset spec in
    `config.source`. Either side may instead/additionally carry a read-only SQL
    projection (`config.source.query` / `config.target_query`), gated exactly
    like custom-SQL checks (ADR 0019). Cross-env source↔target is allowed by
    design (DEV-vs-QA parity is a headline use case), so `env` is not compared.
    """
    # Same #651 string-size cap as expectation checks — no kind may persist a
    # config every GET / version snapshot / export re-emits unbounded.
    _reject_oversized_config(config)
    if expectation_type not in COMPARISON_EXPECTATION_TYPES:
        raise CheckConfigInvalidError(
            "a comparison check's expectation_type must be one of "
            f"{', '.join(COMPARISON_EXPECTATION_TYPES)}, not "
            f"{expectation_type[:_ERROR_ECHO_MAX_CHARS]!r}",
            detail={"expectation_type": expectation_type[:_ERROR_ECHO_MAX_CHARS]},
        )
    if source_connection_id is None:
        raise CheckConfigInvalidError(
            "a comparison check needs source_connection_id — the baseline connection "
            "the suite's dataset is compared against",
            detail={"field": "source_connection_id"},
        )
    source_conn = session.get(Connection, source_connection_id)
    if source_conn is None:
        raise CheckConfigInvalidError(
            "source connection not found",
            detail={"source_connection_id": str(source_connection_id)},
        )
    if source_conn.type in ORCHESTRATION_PROVIDERS:
        # Orchestration providers are never queryable datasources (CLAUDE.md §4).
        raise CheckConfigInvalidError(
            "orchestration providers cannot be a comparison source; pick a datasource "
            "connection",
            detail={"source_connection_id": str(source_connection_id), "type": source_conn.type},
        )

    source_spec = config.get("source")
    if not isinstance(source_spec, dict):
        raise CheckConfigInvalidError(
            "a comparison check needs config.source — the source dataset spec "
            "(same shape as a suite target)",
            detail={"field": "config.source"},
        )
    if "query" in source_spec:
        _validate_side_query(
            source_spec["query"], connection_type=source_conn.type, field="config.source.query"
        )
    else:
        try:
            resolve_target(source_conn.type, source_spec)
        except SuiteTargetInvalidError as exc:
            raise CheckConfigInvalidError(
                f"invalid config.source for a {source_conn.type} source: {exc.message}",
                detail={"field": "config.source", **(exc.detail or {})},
            ) from exc

    if "target_query" in config:
        _validate_side_query(
            config["target_query"],
            connection_type=suite_connection_type,
            field="config.target_query",
        )

    _validate_comparison_keys(config.get("keys"))

    if "tolerance" in config:
        # Same shape check the engine applies at run time (defence in depth) —
        # surfaced as the authoring 422 code.
        from backend.app.datasources.comparison import ComparisonInputError, parse_tolerance

        try:
            parse_tolerance(config["tolerance"])
        except ComparisonInputError as exc:
            raise CheckConfigInvalidError(exc.message, detail=exc.detail) from exc

    max_rows = config.get("max_rows")
    # bool is an int subclass: {"max_rows": true} would otherwise pass as 1 and
    # silently cap the diff to a single row when the #794 runner lands.
    if max_rows is not None and (
        isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows <= 0
    ):
        raise CheckConfigInvalidError(
            "config.max_rows must be a positive integer when set",
            detail={"field": "config.max_rows"},
        )


# Longest string allowed anywhere in an expectation config (keys AND values).
# Generous for real kwargs — a long regex or value-set member runs fine on the
# worker, so the cap must not reject anything the runner would execute — while
# still blocking the 100KB-column-name class of junk GX itself accepts (#651).
# Custom-SQL queries are validated (and bounded) separately, never by this walk.
_CONFIG_STRING_MAX_CHARS = 10_000

# The reported path/type in a 422 is bounded too — the error envelope is echoed
# to the client and logged, so it must not round-trip the oversized input.
_ERROR_ECHO_MAX_CHARS = 200


def _find_oversized_string(value: Any, path: str = "config") -> str | None:
    """Depth-first search for a string over the cap (dict keys included);
    returns its (bounded) path, or None."""
    if isinstance(value, str):
        return path if len(value) > _CONFIG_STRING_MAX_CHARS else None
    if isinstance(value, dict):
        for key, item in value.items():
            # str() first: JSON transports only produce string keys, but a
            # direct Python caller may not — slicing an int key would TypeError.
            key_repr = str(key)[:_ERROR_ECHO_MAX_CHARS]
            if isinstance(key, str) and len(key) > _CONFIG_STRING_MAX_CHARS:
                return f"{path}.{key_repr}… (key)"
            found = _find_oversized_string(item, f"{path}.{key_repr}")
            if found:
                return found
    if isinstance(value, list):
        for i, item in enumerate(value):
            found = _find_oversized_string(item, f"{path}[{i}]")
            if found:
                return found
    return None


def validate_expectation_check(expectation_type: str, config: dict[str, Any]) -> None:
    """Author-time validation for `kind='expectation'` checks (#651).

    Resolves and constructs the GX expectation exactly like the runner
    (`gx_runner._to_gx_expectation`), so an unknown `expectation_type`, a
    missing/wrong-typed/extra config key — anything that would fail on the
    worker — 422s at create/update/import instead of persisting. GX expectation
    classes are pydantic models, so construction IS the schema validation.
    Custom-SQL checks (ADR 0019) have their own validator and must not be passed
    here (their type is not a GX class).
    """
    _reject_oversized_config(config)

    # Lazy: importing great_expectations is heavy (seconds), and the API process
    # only needs it on the authoring paths — same pattern as the vault client.
    import great_expectations.expectations as gxe
    from great_expectations.expectations.expectation import Expectation

    from backend.app.datasources.gx_runner import _expectation_class_name

    class_name = _expectation_class_name(expectation_type)
    expectation_cls = getattr(gxe, class_name, None)
    # The issubclass guard keeps a crafted type from resolving to a non-expectation
    # module attribute via the title-casing getattr.
    if expectation_cls is None or not (
        isinstance(expectation_cls, type) and issubclass(expectation_cls, Expectation)
    ):
        # Bounded echo: REST caps expectation_type at 128 chars, but the MCP
        # tools don't — never round-trip an unbounded string through the 422
        # envelope and the error log.
        raise CheckConfigInvalidError(
            f"unknown expectation_type {expectation_type[:_ERROR_ECHO_MAX_CHARS]!r} — "
            "not a Great Expectations expectation",
            detail={"expectation_type": expectation_type[:_ERROR_ECHO_MAX_CHARS]},
        )
    try:
        expectation_cls(**config)
    except Exception as exc:
        # pydantic ValidationError (missing/wrong-typed/extra kwargs) or a GX
        # root-validator error; the message is user-actionable, so surface it.
        raise CheckConfigInvalidError(
            f"invalid config for {expectation_type[:_ERROR_ECHO_MAX_CHARS]}: {str(exc)[:500]}",
            detail={"expectation_type": expectation_type[:_ERROR_ECHO_MAX_CHARS]},
        ) from exc


def record_check_version(
    session: Session, check: Check, *, actor_id: uuid.UUID | None
) -> CheckVersion:
    """Append an immutable snapshot of `check`'s current state as its next
    version (a per-check sequence starting at 1). The caller commits — this only
    adds the row, so the snapshot and the create/update it records commit
    atomically. The `(check_id, version_no)` unique constraint is the backstop
    against a concurrent double-write computing the same number (rare under v1's
    single-tenant, low-concurrency editing).

    `check.id` must be populated (flush or commit the check first).
    """
    # MAX over no rows is NULL → None; `or 0` makes the first version 1.
    current_max = session.scalar(
        select(func.max(CheckVersion.version_no)).where(CheckVersion.check_id == check.id)
    )
    next_no = (current_max or 0) + 1
    version = CheckVersion(
        check_id=check.id,
        version_no=next_no,
        name=check.name,
        kind=check.kind,
        expectation_type=check.expectation_type,
        dimension=check.dimension,
        source_connection_id=check.source_connection_id,
        config=check.config,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
        changed_by=actor_id,
    )
    session.add(version)
    return version


def create_check(
    session: Session,
    *,
    suite_id: uuid.UUID,
    name: str,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
    source_connection_id: uuid.UUID | None = None,
    dimension: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Create a check in a suite, recording its first version (#280).

    `dimension` (ADR 0038) is the author's optional override; omitted, it is
    DERIVED from the expectation type/kind. Note that omitting it does not mean
    "unclassified" — only a check whose type has no derivation lands NULL.

    Raises `SuiteNotFoundError` (404) if the suite does not exist, or
    `CheckConfigInvalidError` (422) for an unsupported kind.
    """
    suite = get_suite(session, suite_id)  # 404 if the suite is missing
    validate_kind(kind)
    validate_lengths(name=name, expectation_type=expectation_type)
    validate_threshold_ordering(
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    if kind != COMPARISON_KIND and source_connection_id is not None:
        raise CheckConfigInvalidError(
            "only comparison checks carry a source connection (ADR 0015)",
            detail={"kind": kind, "field": "source_connection_id"},
        )
    if kind in MONITOR_KINDS:
        validate_monitor_check(
            kind,
            config,
            expectation_type=expectation_type,
            connection_type=_connection_type(session, suite),
            fail_threshold=fail_threshold,
            critical_threshold=critical_threshold,
        )
    elif kind == COMPARISON_KIND:
        validate_comparison_check(
            session,
            config=config,
            expectation_type=expectation_type,
            source_connection_id=source_connection_id,
            suite_connection_type=_connection_type(session, suite),
        )
    elif is_custom_sql(expectation_type):
        validate_custom_sql_check(
            expectation_type=expectation_type,
            config=config,
            connection_type=_connection_type(session, suite),
        )
    else:
        validate_expectation_check(expectation_type, config)

    check = Check(
        suite_id=suite_id,
        name=name,
        kind=kind,
        expectation_type=expectation_type,
        dimension=resolve_dimension(
            expectation_type=expectation_type, kind=kind, explicit=validate_dimension(dimension)
        ),
        source_connection_id=source_connection_id,
        config=config,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    session.add(check)
    session.flush()  # assign check.id so the v1 snapshot can reference it
    record_check_version(session, check, actor_id=actor_id)
    session.commit()
    session.refresh(check)
    log.info("check_created", check_id=str(check.id), suite_id=str(suite_id))
    return check


def list_checks(session: Session, suite_id: uuid.UUID) -> list[Check]:
    """List a suite's checks (404 if the suite does not exist)."""
    get_suite(session, suite_id)
    stmt = select(Check).where(Check.suite_id == suite_id).order_by(Check.created_at)
    return list(session.scalars(stmt))


def get_check(session: Session, suite_id: uuid.UUID, check_id: uuid.UUID) -> Check:
    """Fetch a check, enforcing that it belongs to `suite_id` (else 404)."""
    check = session.get(Check, check_id)
    if check is None or check.suite_id != suite_id:
        raise CheckNotFoundError(
            "check not found",
            detail={"suite_id": str(suite_id), "check_id": str(check_id)},
        )
    return check


def _validate_kind_specific_config(
    session: Session,
    suite_id: uuid.UUID,
    check: Check,
    *,
    expectation_type: str,
    config: dict[str, Any],
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
    source_connection_id: uuid.UUID | None,
    validate_expectation_config: bool,
) -> None:
    """The kind-specific validation branch shared by `update_check` and
    `restore_check_version` (#283) — factored out to ONE place so a check kind
    added later, or a validator tightened, updates both callers instead of
    restore silently falling behind whichever hand-picked subset it called.
    `kind` is immutable, so the branch is keyed on the LIVE check's `kind`.

    `validate_expectation_config` preserves `update_check`'s pre-#651 escape
    valve: GX-validate a plain expectation only when the caller is actually
    changing `expectation_type`/`config` (a rename/threshold-only PATCH must
    stay possible on a pre-#651 check whose stored config today's pinned GX
    would reject — there is no config backfill). `restore_check_version`
    always re-applies both fields, so it always passes `True`.
    """
    if check.kind in MONITOR_KINDS:
        suite = get_suite(session, suite_id)
        validate_monitor_check(
            check.kind,
            config,
            expectation_type=expectation_type,
            connection_type=_connection_type(session, suite),
            fail_threshold=fail_threshold,
            critical_threshold=critical_threshold,
        )
    elif check.kind == COMPARISON_KIND:
        suite = get_suite(session, suite_id)
        validate_comparison_check(
            session,
            config=config,
            expectation_type=expectation_type,
            source_connection_id=source_connection_id,
            suite_connection_type=_connection_type(session, suite),
        )
    elif is_custom_sql(expectation_type):
        suite = get_suite(session, suite_id)
        validate_custom_sql_check(
            expectation_type=expectation_type,
            config=config,
            connection_type=_connection_type(session, suite),
        )
    elif validate_expectation_config:
        validate_expectation_check(expectation_type, config)


def _record_version_and_commit(
    session: Session, check: Check, check_id: uuid.UUID, actor_id: uuid.UUID | None
) -> Check:
    """Snapshot-if-modified + commit + the concurrent-edit-race → 409 mapping
    shared by `update_check` and `restore_check_version` (#283): both are a
    read-modify-write against the same `(check_id, version_no)` backstop, so
    both need the identical race handling, not two copies that can drift.
    """
    # Only snapshot a real change: a no-op write (identical fields, or restoring
    # the already-current version) must not mint a duplicate version — that
    # would fill the history drawer with noise and defeat "see previous config".
    # SQLAlchemy reports net changes, so setting a field to its existing value
    # isn't dirty.
    if session.is_modified(check):
        record_check_version(session, check, actor_id=actor_id)
    try:
        session.commit()
    except IntegrityError as exc:
        # Roll back the poisoned tx, then map ONLY the version-snapshot collision to
        # a 409 (reload + retry): two concurrent edits computed the same next
        # `version_no` and raced on the `uq_check_versions_check_version` backstop.
        # Any other IntegrityError (a different constraint) is not a concurrency
        # conflict — re-raise it rather than mislabel it "edited concurrently".
        session.rollback()
        if _VERSION_UNIQUE_CONSTRAINT not in str(exc.orig):
            raise
        raise CheckEditConflictError(
            "this check was edited concurrently — reload and retry",
            detail={"check_id": str(check_id)},
        ) from exc
    session.refresh(check)
    return check


def update_check(
    session: Session,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    *,
    name: str | None = None,
    expectation_type: str | None = None,
    config: dict[str, Any] | None = None,
    warn_threshold: Decimal | None = None,
    fail_threshold: Decimal | None = None,
    critical_threshold: Decimal | None = None,
    source_connection_id: uuid.UUID | None = None,
    dimension: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Partial update, snapshotting the post-update state as a new version (#280).

    Follows the codebase PATCH convention (connections / suites): a `None`
    argument means "not provided", so an omitted field is left unchanged. v1 has
    no clear-to-NULL path for thresholds; recreate the check to drop one. The
    same applies to `source_connection_id` (a comparison check can be repointed,
    never cleared — the kind requires it, ADR 0015). `dimension` (ADR 0038) is
    re-settable at any time — derivation is a guess about intent, not a fact —
    but the same convention applies: `None` means "not provided", so it cannot be
    cleared back to unclassified.
    """
    check = get_check(session, suite_id, check_id)
    validate_lengths(name=name, expectation_type=expectation_type)
    validate_dimension(dimension)
    if source_connection_id is not None and check.kind != COMPARISON_KIND:
        raise CheckConfigInvalidError(
            "only comparison checks carry a source connection (ADR 0015)",
            detail={"kind": check.kind, "field": "source_connection_id"},
        )
    # Compute the effective post-patch values and validate them BEFORE touching
    # the ORM object: a rejected update must leave nothing dirty in the session
    # (mutate-then-raise would let a later commit on the same session persist
    # the invalid state). `kind` is immutable on update, so it's read off the
    # existing check.
    new_expectation_type = (
        expectation_type if expectation_type is not None else check.expectation_type
    )
    new_config = config if config is not None else check.config
    new_warn = warn_threshold if warn_threshold is not None else check.warn_threshold
    new_fail = fail_threshold if fail_threshold is not None else check.fail_threshold
    new_critical = (
        critical_threshold if critical_threshold is not None else check.critical_threshold
    )
    # #568: validate the EFFECTIVE post-patch thresholds, not just the ones this
    # PATCH touches — same merge-then-validate shape as the monitor guard below.
    # A pre-existing out-of-order row (not migrated by this fix) therefore needs
    # its thresholds fixed before any further edit persists, same as an
    # already-invalid config under the GX gate a few lines down.
    validate_threshold_ordering(
        warn_threshold=new_warn, fail_threshold=new_fail, critical_threshold=new_critical
    )
    _validate_kind_specific_config(
        session,
        suite_id,
        check,
        expectation_type=new_expectation_type,
        config=new_config,
        fail_threshold=new_fail,
        critical_threshold=new_critical,
        source_connection_id=(
            source_connection_id if source_connection_id is not None else check.source_connection_id
        ),
        # GX-validate a plain expectation only when the PATCH touches it: a
        # rename or threshold tweak must stay possible on a pre-#651 check whose
        # stored config today's pinned GX rejects (there is no config backfill —
        # such a row would otherwise be un-editable until delete-and-recreate).
        validate_expectation_config=(expectation_type is not None or config is not None),
    )

    if name is not None:
        check.name = name
    if expectation_type is not None:
        check.expectation_type = expectation_type
    if config is not None:
        check.config = config
    if source_connection_id is not None:
        check.source_connection_id = source_connection_id
    if warn_threshold is not None:
        check.warn_threshold = warn_threshold
    if fail_threshold is not None:
        check.fail_threshold = fail_threshold
    if critical_threshold is not None:
        check.critical_threshold = critical_threshold
    if dimension is not None:
        check.dimension = dimension
    check = _record_version_and_commit(session, check, check_id, actor_id)
    log.info("check_updated", check_id=str(check.id))
    return check


def delete_check(session: Session, suite_id: uuid.UUID, check_id: uuid.UUID) -> None:
    check = get_check(session, suite_id, check_id)
    session.delete(check)
    session.commit()
    log.info("check_deleted", check_id=str(check_id))


def snooze_check(
    session: Session,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    *,
    hours: float,
    now: datetime | None = None,
) -> Check:
    """Mute a check's alerts until ``hours`` from now (alert suppression).

    Operational state only — sets ``alert_snoozed_until`` directly and does **not**
    record a ``check_versions`` snapshot (a snooze isn't a config change; config
    history shouldn't churn on it). 404 / cross-suite guard via ``get_check``.
    """
    check = get_check(session, suite_id, check_id)
    check.alert_snoozed_until = (now or datetime.now(UTC)) + timedelta(hours=hours)
    session.commit()
    session.refresh(check)
    log.info("check_snoozed", check_id=str(check.id), hours=hours)
    return check


def clear_check_snooze(session: Session, suite_id: uuid.UUID, check_id: uuid.UUID) -> Check:
    """Clear a check's alert snooze (re-enable alerts immediately). Idempotent."""
    check = get_check(session, suite_id, check_id)
    check.alert_snoozed_until = None
    session.commit()
    session.refresh(check)
    log.info("check_snooze_cleared", check_id=str(check.id))
    return check


def list_check_versions(
    session: Session, suite_id: uuid.UUID, check_id: uuid.UUID
) -> list[CheckVersion]:
    """A check's version history, newest first (#280). 404 if the check is
    missing or doesn't belong to `suite_id`. Eager-loads each version's author
    (only query that needs it) so the API can name the editor without an N+1.
    """
    get_check(session, suite_id, check_id)  # 404 / cross-suite guard
    return list(
        session.scalars(
            select(CheckVersion)
            .where(CheckVersion.check_id == check_id)
            .options(selectinload(CheckVersion.author))
            .order_by(CheckVersion.version_no.desc())
        )
    )


def restore_check_version(
    session: Session,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    version_no: int,
    *,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Restore a check to a previous version (#283) by re-validating its frozen
    snapshot through the SAME validators `update_check` uses (`validate_lengths`,
    `validate_dimension`, `validate_threshold_ordering`,
    `_validate_kind_specific_config`) and then applying it. That matters
    because the snapshot may predate a validator that ships later (e.g. #568's
    threshold-ordering gate, or a tightened ADR-0019 custom-SQL rule):
    re-validating means a snapshot no longer valid under CURRENT rules is
    rejected (422) with the live check left untouched, instead of silently
    reinstating something today's authoring path would refuse to create.

    Deliberately does NOT delegate to `update_check` itself: `update_check`'s
    `None`-means-"not provided" PATCH convention has no way to clear a
    threshold/dimension back to NULL, but restoring a version that WAS null
    there (while the check has since had it set to a real value) must reproduce
    the snapshot exactly — restore always applies every field unconditionally,
    including a `None`.

    404s if the check is missing/cross-suite (`get_check`) or `version_no`
    doesn't exist for it. `kind` is immutable — asserted equal to the live
    check's rather than applied, since `CheckVersion.kind` is captured for a
    self-contained history record, never to drive a restore.

    Snapshots the restored state as a brand-new version on success (history is
    additive — nothing is renumbered or deleted) unless the live check is
    already identical to the target snapshot, in which case
    `_record_version_and_commit`'s no-op gating (`session.is_modified`) skips
    the snapshot — restoring the already-current version is a no-op.
    """
    check = get_check(session, suite_id, check_id)  # 404 / cross-suite guard
    version = session.scalar(
        select(CheckVersion).where(
            CheckVersion.check_id == check_id, CheckVersion.version_no == version_no
        )
    )
    if version is None:
        raise CheckVersionNotFoundError(
            "check version not found",
            detail={"check_id": str(check_id), "version_no": version_no},
        )
    assert version.kind == check.kind, "a check's kind is immutable; a version can't disagree"
    assert (
        version.source_connection_id is None or check.kind == COMPARISON_KIND
    ), "only a comparison check's snapshot carries a source connection (ADR 0015)"

    validate_lengths(name=version.name, expectation_type=version.expectation_type)
    validate_dimension(version.dimension)
    validate_threshold_ordering(
        warn_threshold=version.warn_threshold,
        fail_threshold=version.fail_threshold,
        critical_threshold=version.critical_threshold,
    )
    _validate_kind_specific_config(
        session,
        suite_id,
        check,
        expectation_type=version.expectation_type,
        config=version.config,
        fail_threshold=version.fail_threshold,
        critical_threshold=version.critical_threshold,
        source_connection_id=version.source_connection_id,
        # Restore always re-applies both fields (never a partial touch), so
        # always GX-validate — no PATCH-style "only if touched" escape valve.
        validate_expectation_config=True,
    )

    # Apply the FULL snapshot unconditionally (unlike update_check's merge):
    # restore's entire point is to reproduce the version exactly, including
    # clearing a threshold/dimension back to NULL if that's what it held.
    check.name = version.name
    check.expectation_type = version.expectation_type
    check.config = version.config
    check.source_connection_id = version.source_connection_id
    check.warn_threshold = version.warn_threshold
    check.fail_threshold = version.fail_threshold
    check.critical_threshold = version.critical_threshold
    check.dimension = version.dimension

    check = _record_version_and_commit(session, check, check_id, actor_id)
    log.info("check_restored", check_id=str(check.id), version_no=version_no)
    return check


@dataclass(frozen=True)
class CheckResultPoint:
    """One past result for a check — the trend datum behind the per-check chart."""

    run_id: uuid.UUID
    status: str
    metric_value: float | None
    created_at: datetime


def list_check_result_history(
    session: Session, suite_id: uuid.UUID, check_id: uuid.UUID, *, limit: int = 30
) -> list[CheckResultPoint]:
    """A check's recent results in chronological order (oldest→newest) for the
    per-check trend (ADR 0022). 404 if the check is missing or cross-suite.

    Takes the latest `limit` results (newest-first in SQL, then reversed) so the
    chart shows the most recent window left-to-right. `metric_value` is the
    SQL-aggregatable scalar a run measured (ADR 0012); `None` for checks that
    record no metric. Suite scoping is the caller's (router `require_permission`);
    the Run join only guards against a result leaking across suites.
    """
    get_check(session, suite_id, check_id)  # 404 / cross-suite guard
    stmt = (
        select(Result.run_id, Result.status, Result.metric_value, Run.created_at)
        .join(Run, Result.run_id == Run.id)
        .where(Result.check_id == check_id, Run.suite_id == suite_id)
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    rows = [
        CheckResultPoint(
            run_id=run_id,
            status=status,
            metric_value=float(metric_value) if metric_value is not None else None,
            created_at=created_at,
        )
        for run_id, status, metric_value, created_at in session.execute(stmt)
    ]
    rows.reverse()  # chronological for the chart x-axis
    return rows
