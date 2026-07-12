"""Check CRUD — checks are GX expectations nested under a suite.

A check belongs to exactly one suite (FK + cascade). This layer validates the
suite exists, enforces the v1 monitor-kind limit, and validates the check's
`config` at author time: expectation-kind checks resolve + construct their GX
expectation class (#651 — the same translation the runner performs, pulled
forward so garbage 422s instead of persisting and only failing at run time);
validation against live data remains the dry-run path, not CRUD.

Kind gating (ADR 0012): the schema CHECK reserves `freshness / volume /
schema_drift / anomaly / comparison`; authorable today are `expectation`, the
freshness/volume monitor kinds, and `comparison` (ADR 0015 — source ref +
config validated here; its runner lands with #794, until then it yields an
`error` result). `schema_drift` / `anomaly` remain schema-valid but refused —
authoring one would produce a check that can never execute.

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
    FRESHNESS,
    MONITOR_KINDS,
    MonitorConfigError,
    monitor_expectation_type,
    validate_monitor_config,
)
from backend.app.db.models import (
    COMPARISON_KIND,
    ORCHESTRATION_PROVIDERS,
    Check,
    CheckVersion,
    Connection,
    Result,
    Run,
    Suite,
)
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

# Authorable kinds: GX expectations, the freshness/volume monitor kinds (ADR
# 0012, pulled into v1 per the 2026-06-29 amendment), and `comparison` (ADR
# 0015 — authorable now, runnable when the #794 runner lands; until then a
# comparison check yields an `error` result, see run_service). The remaining
# reserved kinds (schema_drift / anomaly) have no model yet, so CRUD refuses.
_V1_SUPPORTED_KINDS = {"expectation", *MONITOR_KINDS, COMPARISON_KIND}

# Canonical expectation_types for a comparison check (mirrors `monitor:<kind>`).
# `comparison:records` = row grain; `comparison:columns` = FDC's per-column
# value grain (#799).
COMPARISON_EXPECTATION_TYPE = "comparison:records"
COMPARISON_EXPECTATION_TYPES = ("comparison:records", "comparison:columns")

# Datasources whose runner implements `run_monitors` (a `MonitorRunner`) — the
# author-time gate for freshness/volume checks. The SQL datasources compute the
# aggregate in-warehouse; Iceberg computes it natively (`scan().count()` / a column
# MAX, ADR 0030). This is broader than `SQL_QUERYABLE_TYPES` (which gates *custom
# SQL* — Iceberg is a native DataFrame read, not SQL-queryable), so the two stay
# distinct. Kept in sync with the run path's `isinstance(runner, MonitorRunner)`.
MONITOR_CAPABLE_TYPES = frozenset({*SQL_QUERYABLE_TYPES, "iceberg"})


class CheckNotFoundError(DataQError):
    status_code = 404
    code = "check_not_found"


class CheckConfigInvalidError(DataQError):
    status_code = 422
    code = "check_config_invalid"


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

    v1 supports `expectation` + the freshness/volume monitor kinds; the remaining
    reserved kinds (ADR 0012) have no runner yet, so authoring one is refused."""
    if kind not in _V1_SUPPORTED_KINDS:
        raise CheckConfigInvalidError(
            f"check kind {kind!r} is not supported in v1",
            detail={"kind": kind, "supported": sorted(_V1_SUPPORTED_KINDS)},
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
    """Validate a freshness/volume monitor check at author time (create/update).

    Four gates, each a 422:
    1. **Monitor-capable datasource only** — a monitor needs a datasource whose runner
       implements `run_monitors` (`MONITOR_CAPABLE_TYPES`: the SQL datasources compute
       the aggregate in-warehouse; Iceberg computes it natively). A monitor on a
       flat-file suite would only fail at run time (its runner has no `run_monitors`),
       so reject it up front. Broader than custom-SQL's `SQL_QUERYABLE_TYPES` — Iceberg
       supports monitors but is not SQL-queryable.
    2. **expectation_type matches the kind** — a monitor's type is the canonical
       ``monitor:<kind>``. The run path keys off `kind`, so a mismatched/junk type
       would still execute but mislabel every result row (and could smuggle a
       custom-SQL type past its guardrails) — keep the stored row self-consistent.
    3. **Config shape** — a valid `column` (freshness) or `min_rows`/`max_rows` range
       (volume), via the shared `monitors.validate_monitor_config`.
    4. **Freshness needs a positive threshold** — freshness has no in-config bound, so
       without a fail/critical age threshold it would always resolve `pass` no matter
       how stale (the silent-green footgun flagged in the #426 review); a *zero*
       threshold is the inverse footgun (always fail). Require a positive fail-or-
       critical threshold so a freshness check bands meaningfully.
    """
    if connection_type not in MONITOR_CAPABLE_TYPES:
        raise CheckConfigInvalidError(
            f"{kind} monitor checks require a monitor-capable datasource, not {connection_type!r}",
            detail={
                "connection_type": connection_type,
                "supported": sorted(MONITOR_CAPABLE_TYPES),
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
    if kind == FRESHNESS and not _has_positive_threshold(fail_threshold, critical_threshold):
        raise CheckConfigInvalidError(
            "a freshness monitor needs a positive fail or critical age threshold (hours) — "
            "without one it can never fail (no threshold) or always fails (zero)",
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
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Create a check in a suite, recording its first version (#280).

    Raises `SuiteNotFoundError` (404) if the suite does not exist, or
    `CheckConfigInvalidError` (422) for an unsupported kind.
    """
    suite = get_suite(session, suite_id)  # 404 if the suite is missing
    validate_kind(kind)
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
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Partial update, snapshotting the post-update state as a new version (#280).

    Follows the codebase PATCH convention (connections / suites): a `None`
    argument means "not provided", so an omitted field is left unchanged. v1 has
    no clear-to-NULL path for thresholds; recreate the check to drop one. The
    same applies to `source_connection_id` (a comparison check can be repointed,
    never cleared — the kind requires it, ADR 0015).
    """
    check = get_check(session, suite_id, check_id)
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
    new_fail = fail_threshold if fail_threshold is not None else check.fail_threshold
    new_critical = (
        critical_threshold if critical_threshold is not None else check.critical_threshold
    )
    if check.kind in MONITOR_KINDS:
        suite = get_suite(session, suite_id)
        validate_monitor_check(
            check.kind,
            new_config,
            expectation_type=new_expectation_type,
            connection_type=_connection_type(session, suite),
            fail_threshold=new_fail,
            critical_threshold=new_critical,
        )
    elif check.kind == COMPARISON_KIND:
        suite = get_suite(session, suite_id)
        validate_comparison_check(
            session,
            config=new_config,
            expectation_type=new_expectation_type,
            source_connection_id=(
                source_connection_id
                if source_connection_id is not None
                else check.source_connection_id
            ),
            suite_connection_type=_connection_type(session, suite),
        )
    elif is_custom_sql(new_expectation_type):
        suite = get_suite(session, suite_id)
        validate_custom_sql_check(
            expectation_type=new_expectation_type,
            config=new_config,
            connection_type=_connection_type(session, suite),
        )
    elif expectation_type is not None or config is not None:
        # GX-validate only when the PATCH touches the expectation itself: a
        # rename or threshold tweak must stay possible on a pre-#651 check whose
        # stored config today's pinned GX rejects (there is no config backfill —
        # such a row would otherwise be un-editable until delete-and-recreate).
        validate_expectation_check(new_expectation_type, new_config)

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
    # Only snapshot a real change: a no-op PATCH (empty body, or fields set to
    # their current values) must not mint a duplicate version — that would fill
    # the history drawer with noise and defeat "see previous config". SQLAlchemy
    # reports net changes, so setting a field to its existing value isn't dirty.
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
