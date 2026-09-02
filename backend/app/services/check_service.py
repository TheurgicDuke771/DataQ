"""Check CRUD — checks are GX expectations nested under a suite."""

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
from backend.app.datasources.engines import engines_for
from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
    UNBANDED_EXPECTATION_TYPES,
    is_allowed,
)
from backend.app.datasources.monitors import (
    ANOMALY,
    FRESHNESS,
    MONITOR_KINDS,
    SCHEMA_DRIFT,
    MonitorConfigError,
    monitor_expectation_type,
    validate_monitor_config,
)
from backend.app.datasources.sampling import (
    SAMPLING_ROW_COUNT_CONFLICT,
    is_row_count_expectation,
)
from backend.app.datasources.snowflake_dmf import (
    DMF_ENGINE,
    DMF_EXPECTATION_TYPES,
    DMF_KINDS,
    DMF_UNBANDABLE_TYPES,
)
from backend.app.datasources.sql import is_sql_identifier
from backend.app.db.models import (
    CHECK_ENGINES,
    CHECK_ORDER,
    COMPARISON_KIND,
    DQ_DIMENSIONS,
    GX_ENGINE,
    ORCHESTRATION_PROVIDERS,
    Check,
    CheckVersion,
    Connection,
    Result,
    Run,
    Suite,
)
from backend.app.services import audit_service
from backend.app.services.check_dimension import is_valid_dimension, resolve_dimension
from backend.app.services.custom_sql import (
    SQL_QUERYABLE_TYPES,
    CustomSqlInvalidError,
    is_custom_sql,
    validate_custom_sql_check,
    validate_query,
)
from backend.app.services.monitor_baseline import get_baseline as _get_monitor_baseline
from backend.app.services.run_target import SuiteTargetInvalidError, resolve_target
from backend.app.services.suite_service import get_suite

log = get_logger(__name__)

# Authorable kinds: GX expectations, every registered monitor kind (ADR 0012 — freshness/volume,
# schema_drift #592, anomaly #593), and `comparison` (ADR 0015).
_V1_SUPPORTED_KINDS = {"expectation", *MONITOR_KINDS, COMPARISON_KIND}

# Canonical expectation_types for a comparison check (mirrors `monitor:<kind>`).
COMPARISON_EXPECTATION_TYPE = "comparison:records"
COMPARISON_EXPECTATION_TYPES = ("comparison:records", "comparison:columns")

# The flat-file datasources.
FILE_TYPES = frozenset({"adls_gen2", "s3"})

# Datasources whose runner implements `run_monitors` (a `MonitorRunner`) — the author-time gate for
# freshness/volume checks.
MONITOR_CAPABLE_TYPES = frozenset({*SQL_QUERYABLE_TYPES, "iceberg", *FILE_TYPES})

# schema_drift (#592) introspects the target's column shape through the baseline-diff executor
# (`services/schema_drift.py`) — not the runner.
SCHEMA_DRIFT_CAPABLE_TYPES = frozenset({*MONITOR_CAPABLE_TYPES, *FILE_TYPES})

# anomaly (#593) is stateful like schema_drift, but it does not reach the runner EITHER.
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


# The unique-constraint name on `check_versions(check_id, version_no)` — the concurrency backstop a
# racing double-edit trips.
_VERSION_UNIQUE_CONSTRAINT = "uq_check_versions_check_version"


class CheckEditConflictError(DataQError):
    # A concurrent edit of the same check raced on the `(check_id, version_no)` snapshot backstop
    # (#309-adjacent C3): a benign write-write collision, so 409 (reload + retry).
    status_code = 409
    code = "check_edit_conflict"


def _connection_type(session: Session, suite: Suite) -> str:
    """The datasource type of the suite's connection — for custom-SQL gating."""
    connection = session.get(Connection, suite.connection_id)
    assert connection is not None
    return connection.type


def validate_kind(kind: str) -> None:
    """Reject an unsupported check kind (422). Shared by CRUD and suite import."""
    if kind not in _V1_SUPPORTED_KINDS:
        raise CheckConfigInvalidError(
            f"check kind {kind!r} is not supported in v1",
            detail={"kind": kind, "supported": sorted(_V1_SUPPORTED_KINDS)},
        )


def validate_engine(engine: str, *, connection_type: str) -> None:
    """Reject an engine the suite's connection doesn't offer (422, ADR 0036 §5)."""
    if engine not in CHECK_ENGINES:
        raise CheckConfigInvalidError(
            f"check engine {engine!r} is not recognised",
            detail={"engine": engine, "known": sorted(CHECK_ENGINES)},
        )
    offered = engines_for(connection_type)
    if engine not in offered:
        raise CheckConfigInvalidError(
            f"engine {engine!r} is not offered by this suite's connection "
            f"(type {connection_type!r}) — ADR 0036 anchors native engines to "
            "the connection that can run them",
            detail={
                "engine": engine,
                "connection_type": connection_type,
                "offered": sorted(offered),
            },
        )


def validate_engine_compatibility(
    engine: str,
    *,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> None:
    """The engine's supported matrix (ADR 0036 §4) — 422 for a kind/type/config
    the engine cannot evaluate. Shared by create, update, restore and suite
    import (the one-gate-per-rule discipline).
    """
    if engine != DMF_ENGINE:
        return
    if kind not in DMF_KINDS:
        raise CheckConfigInvalidError(
            f"the dmf engine cannot evaluate kind {kind!r} — it evaluates "
            "freshness and the dmf:* column metrics",
            detail={"engine": engine, "kind": kind, "supported_kinds": sorted(DMF_KINDS)},
        )
    if kind != "expectation":
        return  # freshness/volume config is the monitor validators' job
    if expectation_type not in DMF_EXPECTATION_TYPES:
        raise CheckConfigInvalidError(
            f"expectation_type {expectation_type!r} is not a dmf metric",
            detail={"engine": engine, "supported_types": sorted(DMF_EXPECTATION_TYPES)},
        )
    unknown_keys = sorted(set(config) - {"column"})
    if unknown_keys:
        raise CheckConfigInvalidError(
            f"a dmf column metric's config is exactly {{'column': …}}; unknown keys: "
            f"{', '.join(unknown_keys)}",
            detail={"expectation_type": expectation_type, "unknown_keys": unknown_keys},
        )
    if not is_sql_identifier(config.get("column")):
        raise CheckConfigInvalidError(
            "a dmf column metric needs a valid 'column' identifier in config",
            detail={"expectation_type": expectation_type},
        )
    thresholds_set = any(
        t is not None for t in (warn_threshold, fail_threshold, critical_threshold)
    )
    if expectation_type in DMF_UNBANDABLE_TYPES:
        if thresholds_set:
            raise CheckConfigInvalidError(
                f"{expectation_type} does not accept thresholds: severity bands treat a "
                "higher metric as worse, but a unique count degrades DOWNWARD — a "
                "threshold would invert its meaning. It records the metric for trends.",
                detail={"expectation_type": expectation_type},
            )
    elif not _has_positive_threshold(fail_threshold, critical_threshold):
        raise CheckConfigInvalidError(
            f"a {expectation_type} check needs a positive fail or critical threshold — "
            "without one it can never fail (no threshold) or always fails (zero)",
            detail={"expectation_type": expectation_type},
        )


def validate_dimension(dimension: str | None) -> str | None:
    """Reject a DQ dimension outside the seven canonical ones (422), ADR 0038."""
    if dimension is not None and not is_valid_dimension(dimension):
        raise CheckConfigInvalidError(
            f"unknown DQ dimension {str(dimension)[:_ERROR_ECHO_MAX_CHARS]!r}",
            detail={
                "dimension": str(dimension)[:_ERROR_ECHO_MAX_CHARS],
                "supported": sorted(DQ_DIMENSIONS),
            },
        )
    return dimension


# Mirrors the `checks.name` / `checks.expectation_type` column widths (db/models.py) and the REST
# `CheckCreate`/`CheckUpdate` Field bounds (api/v1/checks.py).
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
    """Reject negative or out-of-order severity thresholds at author time (422)."""
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
    """Validate a monitor check of any kind at author time (create/update)."""
    # Same #651 string-size cap as expectation/comparison checks (#1787).
    _reject_oversized_config(config)
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
        # The envelope is echoed to the client and logged: never round-trip the caller's config
        # through it — the message already names the offending field (#1787).
        raise CheckConfigInvalidError(str(exc)[:500], detail={"kind": kind}) from exc
    # A column-less freshness monitor measures ARRIVAL time, which only a datasource with a native
    # per-object timestamp can answer (#520).
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
    """`config.keys` — the join keys the diff matches rows on (ADR 0015 §1)."""
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
    connection must be SQL-queryable (Iceberg/flat-file reads are native, not SQL).
    """
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
    """422 when any config string (keys included) exceeds the #651 cap."""
    oversized = _find_oversized_string(config)
    if oversized is not None:
        # Bound the WHOLE path, not just each segment: deep nesting grows the accumulated path ~200
        # chars per level.
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
    """Author-time validation for `kind='comparison'` checks (ADR 0015). All 422s."""
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
        if "sampling" in source_spec:
            # `resolve_target` below would happily ACCEPT a sampling block on a capable source type.
            raise CheckConfigInvalidError(
                "a comparison source does not support 'sampling' yet — the reader "
                "materialises both sides in full for the diff, so the block would be "
                "silently ignored. Narrow the source, or set config.max_rows "
                "deliberately.",
                detail={"field": "config.source.sampling"},
            )
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
_CONFIG_STRING_MAX_CHARS = 10_000

# The reported path/type in a 422 is bounded too — the error envelope is echoed
# to the client and logged, so it must not round-trip the oversized input.
_ERROR_ECHO_MAX_CHARS = 200


def _find_oversized_string(value: Any, path: str = "config") -> str | None:
    """Depth-first search for a string over the cap (dict keys included);
    returns its (bounded) path, or None.
    """
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


def _reject_row_count_on_sampled_suite(
    session: Session, suite: Suite, expectation_type: str
) -> None:
    """422 for a row-count expectation on a suite whose target samples (#595 C6)."""
    if not is_row_count_expectation(expectation_type) or not suite.target:
        return
    connection_type = _connection_type(session, suite)
    try:
        sampling = resolve_target(connection_type, suite.target).sampling
    except SuiteTargetInvalidError:
        # A target that no longer resolves is its own (separate) problem, and it is not this gate's
        # job to report it.
        return
    if sampling is not None:
        raise CheckConfigInvalidError(
            SAMPLING_ROW_COUNT_CONFLICT,
            detail={"expectation_type": expectation_type, "field": "expectation_type"},
        )


def reject_thresholds_on_unbanded(
    expectation_type: str,
    *,
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> None:
    """422 for severity thresholds on a type whose result has no `unexpected_percent` —
    they could never fire, so refuse rather than store an inert knob (#1607).
    """
    if expectation_type not in UNBANDED_EXPECTATION_TYPES:
        return
    if warn_threshold is None and fail_threshold is None and critical_threshold is None:
        return
    raise CheckConfigInvalidError(
        f"{expectation_type} compares the column's distinct-value SET — its result has no "
        "unexpected-% for severity bands to read, so thresholds can never fire and are "
        "refused rather than stored inert. Remove them; the result is binary pass/fail.",
        detail={"expectation_type": expectation_type, "field": "thresholds"},
    )


def reject_dataframe_only_expectation(expectation_type: str, *, connection_type: str) -> None:
    """422 for an expectation GX cannot evaluate on this connection's SQL batch (#1509).

    Hiding it in the editor is not enough — the API, MCP and suite import all reach the same
    rows, and the alternative is a check that saves cleanly and errors on every run. Takes the
    connection type rather than a Suite so import (which has no Suite yet) shares this gate
    instead of hand-rolling a second copy.
    """
    from backend.app.datasources.gx_runner import SQL_BATCH_CONNECTION_TYPES

    if expectation_type not in DATAFRAME_ONLY_EXPECTATION_TYPES:
        return
    if connection_type not in SQL_BATCH_CONNECTION_TYPES:
        return
    raise CheckConfigInvalidError(
        f"{expectation_type} has no SQL implementation in Great Expectations, so it can never "
        f"run against a {connection_type} connection. Use a custom-SQL check instead.",
        detail={"expectation_type": expectation_type, "field": "expectation_type"},
    )


def validate_expectation_check(
    expectation_type: str,
    config: dict[str, Any],
    *,
    permitted_stored_type: str | None = None,
) -> None:
    """Author-time validation for `kind='expectation'` checks (#651, allowlisted in #1510).

    `permitted_stored_type`: a type already stored on the row being edited/restored —
    retaining it bypasses the allowlist (a legacy row must stay editable and restorable);
    GX resolvability and config validation still apply.
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
    resolves_to_gx = isinstance(expectation_cls, type) and issubclass(expectation_cls, Expectation)
    if not is_allowed(expectation_type) and expectation_type != permitted_stored_type:
        # Bounded echo: REST caps expectation_type at 128 chars, but the MCP tools don't — never
        # round-trip an unbounded string through the 422 envelope and the error log.
        echoed = expectation_type[:_ERROR_ECHO_MAX_CHARS]
        # The two refusals are NOT the same problem, and saying so is the difference between
        # "fix your typo" and "this one is real, DataQ just doesn't run it".
        message = (
            f"expectation_type {echoed!r} is a Great Expectations expectation but is not in "
            "DataQ's vetted set, so it cannot be authored here — see `supported`, or express "
            "the rule as a custom-SQL check"
            if resolves_to_gx
            else f"unknown expectation_type {echoed!r} — not a Great Expectations expectation"
        )
        raise CheckConfigInvalidError(
            message,
            detail={
                "expectation_type": echoed,
                "recognised_by_great_expectations": resolves_to_gx,
                "supported": sorted(ALLOWED_EXPECTATION_TYPES),
            },
        )
    if not (isinstance(expectation_cls, type) and issubclass(expectation_cls, Expectation)):
        # Allowlisted and yet unresolvable: a GX upgrade renamed or dropped it. The parity test
        # catches that at build time; this keeps the run-time answer a 422 rather than a
        # "NoneType is not callable" 500. (Repeats resolves_to_gx's condition inline so the
        # type-checker narrows expectation_cls for the construction below.)
        raise CheckConfigInvalidError(
            f"expectation_type {expectation_type[:_ERROR_ECHO_MAX_CHARS]!r} is enabled in DataQ "
            "but the installed Great Expectations no longer provides it",
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
    """Append an immutable snapshot of `check`'s current state as its next version (a per-check
    sequence starting at 1). The caller commits — this only adds the row, so the snapshot and
    the create/update it records commit atomically.
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
        engine=check.engine,
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
    engine: str = GX_ENGINE,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Create a check in a suite, recording its first version (#280)."""
    suite = get_suite(session, suite_id)  # 404 if the suite is missing
    validate_kind(kind)
    validate_engine(engine, connection_type=_connection_type(session, suite))
    validate_engine_compatibility(
        engine,
        kind=kind,
        expectation_type=expectation_type,
        config=config,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
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
    elif engine == DMF_ENGINE:
        # A dmf:* column metric — its whole config was validated by validate_engine_compatibility
        # above; it is not a GX expectation.
        pass
    else:
        validate_expectation_check(expectation_type, config)
        _reject_row_count_on_sampled_suite(session, suite, expectation_type)
        reject_dataframe_only_expectation(
            expectation_type, connection_type=_connection_type(session, suite)
        )
    reject_thresholds_on_unbanded(
        expectation_type,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )

    check = Check(
        suite_id=suite_id,
        name=name,
        kind=kind,
        engine=engine,
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
    audit_service.record_entity_change(
        session,
        action="check.create",
        entity_type="check",
        entity=check,
        actor=actor_id,
    )
    session.commit()
    session.refresh(check)
    log.info("check_created", check_id=str(check.id), suite_id=str(suite_id))
    return check


def list_checks(session: Session, suite_id: uuid.UUID) -> list[Check]:
    """List a suite's checks (404 if the suite does not exist)."""
    get_suite(session, suite_id)
    stmt = select(Check).where(Check.suite_id == suite_id).order_by(*CHECK_ORDER)
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
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
    source_connection_id: uuid.UUID | None,
    validate_expectation_config: bool,
    engine: str = GX_ENGINE,
    permitted_stored_type: str | None = None,
) -> None:
    """The kind-specific validation branch shared by `update_check` and `restore_check_version`
    (#283) — factored out to ONE place so a check kind added later, or a validator tightened,
    updates both callers instead of restore silently falling behind whichever hand-picked subset
    it called.
    """
    # Outside the validate_expectation_config flag: covers a threshold-only PATCH.
    reject_thresholds_on_unbanded(
        expectation_type,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
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
    elif engine == DMF_ENGINE:
        # dmf:* column metric — validated by validate_engine_compatibility at
        # the caller; never a GX expectation, so the GX gate does not apply.
        pass
    elif validate_expectation_config:
        validate_expectation_check(
            expectation_type, config, permitted_stored_type=permitted_stored_type
        )
        suite = get_suite(session, suite_id)
        _reject_row_count_on_sampled_suite(session, suite, expectation_type)
        reject_dataframe_only_expectation(
            expectation_type, connection_type=_connection_type(session, suite)
        )


def _record_version_and_commit(
    session: Session,
    check: Check,
    check_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    *,
    audit_action: str = "check.update",
    audit_before: dict[str, Any] | None = None,
) -> Check:
    """Snapshot-if-modified + commit + the concurrent-edit-race → 409 mapping
    shared by `update_check` and `restore_check_version` (#283): both are a
    read-modify-write against the same `(check_id, version_no)` backstop, so
    both need the identical race handling, not two copies that can drift.
    """
    # Only snapshot a real change: a no-op write (identical fields, or restoring the already-current
    # version) must not mint a duplicate version.
    modified = session.is_modified(check)
    if modified:
        record_check_version(session, check, actor_id=actor_id)
    try:
        # Audited on the same condition as the version snapshot, and for the same reason: a no-op
        # write (identical fields, or restoring the already-current version) changed nothing.
        if modified:
            audit_service.record_entity_change(
                session,
                action=audit_action,
                entity_type="check",
                entity=check,
                actor=actor_id,
                before=audit_before,
            )
        session.commit()
    except IntegrityError as exc:
        # Roll back the poisoned tx, then map ONLY the version-snapshot collision to a 409 (reload
        # + retry): two concurrent edits computed the same next `version_no` and raced on the
        # `uq_check_versions_check_version` backstop.
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
    engine: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Partial update, snapshotting the post-update state as a new version (#280)."""
    check = get_check(session, suite_id, check_id)
    # Before any field below is mutated.
    audit_before = audit_service.snapshot("check", check)
    validate_lengths(name=name, expectation_type=expectation_type)
    validate_dimension(dimension)
    if engine is not None:
        validate_engine(
            engine, connection_type=_connection_type(session, get_suite(session, suite_id))
        )
    if source_connection_id is not None and check.kind != COMPARISON_KIND:
        raise CheckConfigInvalidError(
            "only comparison checks carry a source connection (ADR 0015)",
            detail={"kind": check.kind, "field": "source_connection_id"},
        )
    # Compute the effective post-patch values and validate them BEFORE touching the ORM object: a
    # rejected update must leave nothing dirty in the session (mutate-then-raise would let a later
    # commit on the same session persist the invalid state).
    new_expectation_type = (
        expectation_type if expectation_type is not None else check.expectation_type
    )
    new_config = config if config is not None else check.config
    new_warn = warn_threshold if warn_threshold is not None else check.warn_threshold
    new_fail = fail_threshold if fail_threshold is not None else check.fail_threshold
    new_critical = (
        critical_threshold if critical_threshold is not None else check.critical_threshold
    )
    # #568: validate the EFFECTIVE post-patch thresholds, not just the ones this PATCH touches —
    # same merge-then-validate shape as the monitor guard below.
    validate_threshold_ordering(
        warn_threshold=new_warn, fail_threshold=new_fail, critical_threshold=new_critical
    )
    # The engine matrix is validated on the EFFECTIVE post-patch state (ADR 0036): re-pointing a GX
    # check to dmf without also supplying a dmf config must 422 here, not at run time.
    new_engine = engine if engine is not None else (check.engine or GX_ENGINE)
    validate_engine_compatibility(
        new_engine,
        kind=check.kind,
        expectation_type=new_expectation_type,
        config=new_config,
        warn_threshold=new_warn,
        fail_threshold=new_fail,
        critical_threshold=new_critical,
    )
    _validate_kind_specific_config(
        session,
        suite_id,
        check,
        expectation_type=new_expectation_type,
        config=new_config,
        warn_threshold=new_warn,
        fail_threshold=new_fail,
        critical_threshold=new_critical,
        engine=new_engine,
        source_connection_id=(
            source_connection_id if source_connection_id is not None else check.source_connection_id
        ),
        # GX-validate a plain expectation only when the PATCH touches it: a rename or threshold
        # tweak must stay possible on a pre-#651 check whose stored config today's pinned GX
        # rejects (there is no config backfill — such a row would otherwise be un-editable until
        # delete-and-recreate).
        validate_expectation_config=(
            expectation_type is not None or config is not None or engine is not None
        ),
        # A pre-#1510 row's stored type stays editable; only CHANGING to an
        # unvetted type is refused.
        permitted_stored_type=check.expectation_type,
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
    if engine is not None:
        check.engine = engine
    check = _record_version_and_commit(
        session, check, check_id, actor_id, audit_action="check.update", audit_before=audit_before
    )
    log.info("check_updated", check_id=str(check.id))
    return check


def delete_check(
    session: Session,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
) -> None:
    """Delete a check."""
    check = get_check(session, suite_id, check_id)
    audit_before = audit_service.snapshot("check", check)
    session.delete(check)
    audit_service.record_entity_change(
        session,
        action="check.delete",
        entity_type="check",
        entity=None,
        actor=actor_id,
        before=audit_before,
    )
    session.commit()
    log.info("check_deleted", check_id=str(check_id))


def snooze_check(
    session: Session,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    *,
    hours: float,
    now: datetime | None = None,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Mute a check's alerts until ``hours`` from now (alert suppression)."""
    check = get_check(session, suite_id, check_id)
    audit_before = audit_service.snapshot("check", check)
    check.alert_snoozed_until = (now or datetime.now(UTC)) + timedelta(hours=hours)
    # Audited even though it records no `check_versions` snapshot.
    audit_service.record_entity_change(
        session,
        action="check.snooze",
        entity_type="check",
        entity=check,
        actor=actor_id,
        before=audit_before,
    )
    session.commit()
    session.refresh(check)
    log.info("check_snoozed", check_id=str(check.id), hours=hours)
    return check


def clear_check_snooze(
    session: Session,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Clear a check's alert snooze (re-enable alerts immediately). Idempotent."""
    check = get_check(session, suite_id, check_id)
    audit_before = audit_service.snapshot("check", check)
    check.alert_snoozed_until = None
    audit_service.record_entity_change(
        session,
        action="check.unsnooze",
        entity_type="check",
        entity=check,
        actor=actor_id,
        before=audit_before,
    )
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
    """Restore a check to a previous version (#283) by re-validating its frozen snapshot through
    the SAME validators `update_check` uses (`validate_lengths`, `validate_dimension`,
    `validate_threshold_ordering`, `_validate_kind_specific_config`) and then applying it.
    """
    check = get_check(session, suite_id, check_id)  # 404 / cross-suite guard
    audit_before = audit_service.snapshot("check", check)
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
    # Re-validated like every other snapshot field (ADR 0036): a snapshot cut when the connection
    # offered an engine it no longer does must be refused.
    validate_engine(
        version.engine,
        connection_type=_connection_type(session, get_suite(session, suite_id)),
    )
    validate_engine_compatibility(
        version.engine,
        kind=check.kind,
        expectation_type=version.expectation_type,
        config=version.config,
        warn_threshold=version.warn_threshold,
        fail_threshold=version.fail_threshold,
        critical_threshold=version.critical_threshold,
    )
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
        warn_threshold=version.warn_threshold,
        fail_threshold=version.fail_threshold,
        critical_threshold=version.critical_threshold,
        engine=version.engine,
        source_connection_id=version.source_connection_id,
        # Restore always re-applies both fields (never a partial touch), so
        # always GX-validate — no PATCH-style "only if touched" escape valve.
        validate_expectation_config=True,
        # Restore re-applies this check's own history, not new authoring — the
        # allowlist gated the version when it was written (#1510).
        permitted_stored_type=version.expectation_type,
    )

    # Apply the FULL snapshot unconditionally (unlike update_check's merge): restore's entire point
    # is to reproduce the version exactly.
    check.name = version.name
    check.engine = version.engine
    check.expectation_type = version.expectation_type
    check.config = version.config
    check.source_connection_id = version.source_connection_id
    check.warn_threshold = version.warn_threshold
    check.fail_threshold = version.fail_threshold
    check.critical_threshold = version.critical_threshold
    check.dimension = version.dimension

    check = _record_version_and_commit(
        session, check, check_id, actor_id, audit_action="check.restore", audit_before=audit_before
    )
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


def count_check_results(session: Session, suite_id: uuid.UUID, check_id: uuid.UUID) -> int:
    """Total results this check has ever recorded, ignoring any `limit`."""
    get_check(session, suite_id, check_id)  # 404 / cross-suite guard, as the list does
    stmt = (
        select(func.count())
        .select_from(Result)
        .join(Run, Result.run_id == Run.id)
        .where(Result.check_id == check_id, Run.suite_id == suite_id)
    )
    return session.scalar(stmt) or 0


@dataclass(frozen=True)
class CheckBaselinePoint:
    """A stateful monitor's stored baseline row (#594) — the raw payload the
    trend chart overlays. `kind` is denormalized on the row (see
    `MonitorBaseline`) rather than re-read off the check, so this stays a
    single-row lookup.
    """

    kind: str
    baseline: dict[str, Any]
    captured_at: datetime


def get_check_baseline(
    session: Session, suite_id: uuid.UUID, check_id: uuid.UUID
) -> CheckBaselinePoint | None:
    """A check's current stored baseline (#594), or `None` when absent — a check that has never
    run, isn't a stateful kind (`schema_drift`/`anomaly`), or was just re-baselined (#592,
    delete-then-recapture) all read as "no baseline yet" rather than an error; the caller (API)
    renders that as an empty overlay, not a 404.
    """
    get_check(session, suite_id, check_id)  # 404 / cross-suite guard
    row = _get_monitor_baseline(session, check_id)
    if row is None:
        return None
    return CheckBaselinePoint(kind=row.kind, baseline=row.baseline, captured_at=row.captured_at)
