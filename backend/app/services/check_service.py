"""Check CRUD — checks are GX expectations nested under a suite.

A check belongs to exactly one suite (FK + cascade). This layer validates the
suite exists, enforces the v1 monitor-kind limit, and validates the check's
`config` at author time: expectation-kind checks resolve + construct their GX
expectation class (#651 — the same translation the runner performs, pulled
forward so garbage 422s instead of persisting and only failing at run time);
validation against live data remains the dry-run path, not CRUD.

v1 monitor-kind limit (ADR 0012): although the schema CHECK reserves
`freshness / volume / schema_drift / anomaly / comparison`, v1 only *runs*
`expectation`. The API therefore refuses to author a non-`expectation` check —
a reserved kind is schema-valid for forward-compat but not yet runnable, so
letting a user create one would just produce a check that can never execute.

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
from backend.app.db.models import Check, CheckVersion, Connection, Result, Run, Suite
from backend.app.services.custom_sql import (
    SQL_QUERYABLE_TYPES,
    is_custom_sql,
    validate_custom_sql_check,
)
from backend.app.services.suite_service import get_suite

log = get_logger(__name__)

# v1 authors GX expectations + the freshness/volume monitor kinds (ADR 0012,
# pulled into v1 per the 2026-06-29 amendment). The remaining reserved kinds
# (schema_drift / anomaly / comparison) are schema-valid but have no runner yet,
# so CRUD still refuses them.
_V1_SUPPORTED_KINDS = {"expectation", *MONITOR_KINDS}


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
    1. **SQL datasource only** — monitors run a scalar SQL aggregate, so they need a
       SQL-queryable connection (Snowflake / Unity Catalog), exactly like custom-SQL.
       A monitor on a flat-file suite would only fail at run time (the runner has no
       `run_monitors`), so reject it up front.
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
    if connection_type not in SQL_QUERYABLE_TYPES:
        raise CheckConfigInvalidError(
            f"{kind} monitor checks require a SQL datasource, not {connection_type!r}",
            detail={"connection_type": connection_type, "supported": sorted(SQL_QUERYABLE_TYPES)},
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


# Longest string allowed anywhere in an expectation config. Generous for real
# kwargs (column names, value-set members, regexes) while blocking the
# 100KB-column-name class of junk GX itself accepts (#651). Custom-SQL queries
# are validated (and bounded) separately and never reach this walk.
_CONFIG_STRING_MAX_CHARS = 1_000


def _find_oversized_string(value: Any, path: str = "config") -> str | None:
    """Depth-first search for a string over the cap; returns its path, or None."""
    if isinstance(value, str):
        return path if len(value) > _CONFIG_STRING_MAX_CHARS else None
    if isinstance(value, dict):
        for key, item in value.items():
            found = _find_oversized_string(item, f"{path}.{key}")
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
    oversized = _find_oversized_string(config)
    if oversized is not None:
        raise CheckConfigInvalidError(
            f"config value at {oversized} exceeds {_CONFIG_STRING_MAX_CHARS} characters",
            detail={"path": oversized, "max_chars": _CONFIG_STRING_MAX_CHARS},
        )

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
        raise CheckConfigInvalidError(
            f"unknown expectation_type {expectation_type!r} — not a Great Expectations "
            "expectation",
            detail={"expectation_type": expectation_type},
        )
    try:
        expectation_cls(**config)
    except Exception as exc:
        # pydantic ValidationError (missing/wrong-typed/extra kwargs) or a GX
        # root-validator error; the message is user-actionable, so surface it.
        raise CheckConfigInvalidError(
            f"invalid config for {expectation_type}: {str(exc)[:500]}",
            detail={"expectation_type": expectation_type},
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
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Create a check in a suite, recording its first version (#280).

    Raises `SuiteNotFoundError` (404) if the suite does not exist, or
    `CheckConfigInvalidError` (422) for an unsupported kind.
    """
    suite = get_suite(session, suite_id)  # 404 if the suite is missing
    validate_kind(kind)
    if kind in MONITOR_KINDS:
        validate_monitor_check(
            kind,
            config,
            expectation_type=expectation_type,
            connection_type=_connection_type(session, suite),
            fail_threshold=fail_threshold,
            critical_threshold=critical_threshold,
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
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Partial update, snapshotting the post-update state as a new version (#280).

    Follows the codebase PATCH convention (connections / suites): a `None`
    argument means "not provided", so an omitted field is left unchanged. v1 has
    no clear-to-NULL path for thresholds; recreate the check to drop one.
    """
    check = get_check(session, suite_id, check_id)
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
    elif is_custom_sql(new_expectation_type):
        suite = get_suite(session, suite_id)
        validate_custom_sql_check(
            expectation_type=new_expectation_type,
            config=new_config,
            connection_type=_connection_type(session, suite),
        )
    else:
        validate_expectation_check(new_expectation_type, new_config)

    if name is not None:
        check.name = name
    if expectation_type is not None:
        check.expectation_type = expectation_type
    if config is not None:
        check.config = config
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
