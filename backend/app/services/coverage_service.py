"""Automatic coverage (ADR 0047): a system-owned suite of universal baselines per inventory table.

For every connection with ``auto_coverage`` switched on, each table its inventory lists (not
excluded, still being seen) gets one suite with ``origin='auto'`` and no human owner, holding
ordinary checks that report change against the table's own history: row count and freshness
anomaly, and schema drift. The daily reconciler only ever ADDS what is missing: it never edits
an existing check, never recreates one a person deleted, and pauses (never deletes) the suites
of tables that stop being covered.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.datasources.sql import is_sql_identifier
from backend.app.db.models import Asset, Check, Connection, MonitorBaseline, Schedule, Suite
from backend.app.services import check_service, cron, suite_service
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.inventory_service import INVENTORY_TYPES

log = get_logger(__name__)

#: An asset the inventory has not seen for this long has aged out of the warehouse listing.
INVENTORY_FRESH_DAYS = 3
SUITE_NAME_PREFIX = "Auto: "

VOLUME_CHECK = "Row count is normal"
SCHEMA_CHECK = "Schema is unchanged"
FRESHNESS_CHECK = "Data is fresh"
PROFILE_CHECK = "Column profile is normal"

#: Anomaly defaults (ADR 0047 §5): warn at 3 standard deviations, fail at 4, critical at 6.
#: Same-weekday history, 4 points before scoring: a daily suite starts scoring in its fifth week.
_ANOMALY_THRESHOLDS = {"warn": 3, "fail": 4, "critical": 6}
_ANOMALY_WINDOW = {"window": 8, "min_points": 4, "seasonality": True}

#: Timestamp columns preferred as the freshness column, most specific first.
_FRESHNESS_NAMES = (
    "loaded_at",
    "load_ts",
    "ingested_at",
    "updated_at",
    "modified_at",
    "last_modified",
    "event_time",
    "event_ts",
    "created_at",
)


@dataclass
class ReconcileReport:
    suites_created: int = 0
    checks_created: int = 0
    suites_paused: int = 0
    suites_resumed: int = 0
    skipped_assets: list[str] = field(default_factory=list)
    truncated: bool = False


def coverage_on(connection: Connection) -> bool:
    return (
        connection.type in INVENTORY_TYPES
        and (connection.config or {}).get("auto_coverage") is True
    )


def target_for_asset(connection: Connection, asset: Asset) -> dict[str, str] | None:
    """The suite target that resolves back to exactly this asset, or ``None`` when the name does
    not round-trip (e.g. a quoted mixed-case identifier) — skipped, never guessed."""
    parts = asset.name.split(".")
    if len(parts) < 2:
        return None
    if connection.type == "unity_catalog":
        if len(parts) != 3:
            return None
        target = {"catalog": parts[0], "schema": parts[1], "table": parts[2]}
    else:
        target = {"schema": parts[-2], "table": parts[-1]}
    try:
        identity = resolve_asset_identity(connection.type, dict(connection.config or {}), target)
    except ValueError:
        return None
    if (identity.namespace, identity.name) != (asset.namespace, asset.name):
        return None
    return target


def covered_assets(
    session: Session, connection: Connection, *, now: datetime
) -> tuple[list[Asset], bool]:
    """The inventory tables coverage applies to, capped: ``(assets, truncated)``."""
    cap = get_settings().auto_coverage_max_assets
    query = (
        select(Asset)
        .where(
            Asset.connection_id == connection.id,
            Asset.auto_coverage_excluded.is_(False),
            Asset.last_seen >= now - timedelta(days=INVENTORY_FRESH_DAYS),
        )
        .order_by(Asset.name)
    )
    if cap > 0:
        query = query.limit(cap + 1)
    assets = list(session.scalars(query))
    truncated = cap > 0 and len(assets) > cap
    if truncated:
        log.warning("auto_coverage_truncated", connection_id=str(connection.id), cap=cap)
        assets = assets[:cap]
    return assets, truncated


def _cron_for(asset_id: uuid.UUID) -> str:
    """A daily slot derived from the asset id, so a large inventory does not fire at once."""
    slot = asset_id.int % (24 * 60)
    return f"{slot % 60} {slot // 60} * * *"


def _auto_suite(session: Session, asset: Asset) -> Suite | None:
    return session.scalar(select(Suite).where(Suite.asset_id == asset.id, Suite.origin == "auto"))


def _freshness_column(session: Session, schema_check_id: uuid.UUID | None) -> str | None:
    """A load or event timestamp column, from the schema-drift check's captured baseline.

    Only plain SQL identifiers qualify: the anomaly config refuses anything else, and one
    table's rejected column must not stall the rest.
    """
    if schema_check_id is None:
        return None
    baseline = session.scalar(
        select(MonitorBaseline.baseline).where(MonitorBaseline.check_id == schema_check_id)
    )
    columns = (baseline or {}).get("columns") or []
    timestamps = [
        str(c.get("name"))
        for c in columns
        if any(t in str(c.get("type", "")).upper() for t in ("TIMESTAMP", "DATETIME"))
        and is_sql_identifier(c.get("name"))
    ]
    if not timestamps:
        return None
    by_lower = {name.lower(): name for name in timestamps}
    for preferred in _FRESHNESS_NAMES:
        if preferred in by_lower:
            return by_lower[preferred]
    return timestamps[0]


def _decimal(value: int | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _add_check(
    session: Session,
    suite: Suite,
    *,
    name: str,
    kind: str,
    config: dict[str, Any],
    thresholds: dict[str, int] | None,
) -> Check:
    t = thresholds or {}
    return check_service.create_check(
        session,
        suite_id=suite.id,
        name=name,
        kind=kind,
        expectation_type=f"monitor:{kind}",
        config=config,
        warn_threshold=_decimal(t.get("warn")),
        fail_threshold=_decimal(t.get("fail")),
        critical_threshold=_decimal(t.get("critical")),
        origin="auto",
        machine_write=True,
    )


def _ensure_checks(session: Session, suite: Suite) -> int:
    """Add each universal check once, tracked by role -> check id in ``auto_state['checks']``.

    Tracking by id, not name, means a person can rename a check without it being re-added, and a
    recorded id that no longer exists is a check a person deleted: never recreated.
    """
    state = dict(suite.auto_state or {})
    roles: dict[str, str] = dict(state.get("checks", {}))
    live = {str(cid) for cid in session.scalars(select(Check.id).where(Check.suite_id == suite.id))}
    created = 0

    def add(role: str, **spec: Any) -> None:
        nonlocal created
        check = _add_check(session, suite, **spec)
        roles[role] = str(check.id)
        created += 1

    if "schema" not in roles:
        add("schema", name=SCHEMA_CHECK, kind="schema_drift", config={}, thresholds=None)
    if "volume" not in roles:
        add(
            "volume",
            name=VOLUME_CHECK,
            kind="anomaly",
            config={"target_metric": "row_count", **_ANOMALY_WINDOW},
            thresholds=_ANOMALY_THRESHOLDS,
        )
    if "profile" not in roles:
        add(
            "profile",
            name=PROFILE_CHECK,
            kind="anomaly",
            config={"target_metric": "column_profile", **_ANOMALY_WINDOW},
            thresholds=_ANOMALY_THRESHOLDS,
        )
    if "freshness" not in roles:
        schema_id = roles.get("schema")
        schema_uuid = uuid.UUID(schema_id) if schema_id and schema_id in live else None
        column = _freshness_column(session, schema_uuid)
        if column is not None:
            add(
                "freshness",
                name=FRESHNESS_CHECK,
                kind="anomaly",
                config={
                    "target_metric": "freshness_age_hours",
                    "column": column,
                    **_ANOMALY_WINDOW,
                },
                thresholds=_ANOMALY_THRESHOLDS,
            )
            state.pop("freshness_gap", None)
        elif schema_uuid is not None and session.scalar(
            # The schema is known only after the first run; before it, there is nothing to say.
            select(MonitorBaseline.id).where(MonitorBaseline.check_id == schema_uuid)
        ):
            state["freshness_gap"] = "no timestamp column to measure freshness by"
    state["checks"] = roles
    if state != (suite.auto_state or {}):
        suite.auto_state = state
        session.commit()
    return created


def _set_schedule(session: Session, suite: Suite, *, covered: bool, now: datetime) -> str | None:
    """Keep a covered suite's daily schedule on, and pause an uncovered one.

    Resumes only a schedule this loop paused (``auto_state['paused_by_coverage']``): a schedule a
    person switched off stays off.
    """
    state = dict(suite.auto_state or {})
    schedule = session.scalar(select(Schedule).where(Schedule.suite_id == suite.id))
    if schedule is None:
        if not covered:
            return None
        expr = _cron_for(suite.asset_id or suite.id)
        session.add(
            Schedule(
                suite_id=suite.id,
                cron=expr,
                timezone="UTC",
                enabled=True,
                next_run_at=cron.next_fire(expr, "UTC", after=now),
            )
        )
        session.commit()
        return None
    transition: str | None = None
    if covered and not schedule.enabled and state.get("paused_by_coverage"):
        schedule.enabled = True
        schedule.next_run_at = cron.next_fire(schedule.cron, schedule.timezone, after=now)
        state.pop("paused_by_coverage", None)
        transition = "resumed"
    elif not covered and schedule.enabled:
        schedule.enabled = False
        state["paused_by_coverage"] = True
        transition = "paused"
    if transition is None:
        return None
    suite.auto_state = state
    session.commit()
    return transition


def _reconcile_asset(
    session: Session, connection: Connection, asset: Asset, report: ReconcileReport, now: datetime
) -> None:
    suite = _auto_suite(session, asset)
    if suite is None:
        target = target_for_asset(connection, asset)
        if target is None:
            report.skipped_assets.append(asset.name)
            return
        suite = suite_service.create_suite(
            session,
            name=(SUITE_NAME_PREFIX + asset.name)[:128],
            description="Created by automatic coverage: change against this table's history.",
            connection_id=connection.id,
            created_by=None,
            target=target,
            origin="auto",
            machine_write=True,
        )
        report.suites_created += 1
    report.checks_created += _ensure_checks(session, suite)
    if _set_schedule(session, suite, covered=True, now=now) == "resumed":
        report.suites_resumed += 1


def reconcile_connection(
    session: Session, connection: Connection, *, now: datetime | None = None
) -> ReconcileReport:
    """Bring one connection's automatic suites in line with what its coverage should be."""
    moment = now or datetime.now(UTC)
    report = ReconcileReport()
    assets, report.truncated = (
        covered_assets(session, connection, now=moment) if coverage_on(connection) else ([], False)
    )
    covered_ids = {a.id for a in assets}
    for asset in assets:
        name = asset.name
        try:
            _reconcile_asset(session, connection, asset, report, moment)
        except Exception:
            # One table's failure must not stall the rest of the connection, every day.
            session.rollback()
            report.skipped_assets.append(name)
            log.warning(
                "auto_coverage_asset_failed",
                connection_id=str(connection.id),
                asset=name,
                exc_info=True,
            )
    # Pause the connection's automatic suites that are no longer covered.
    for suite in session.scalars(
        select(Suite).where(Suite.connection_id == connection.id, Suite.origin == "auto")
    ):
        if suite.asset_id not in covered_ids:
            if _set_schedule(session, suite, covered=False, now=moment) == "paused":
                report.suites_paused += 1
    if report.skipped_assets:
        log.info(
            "auto_coverage_skipped_assets",
            connection_id=str(connection.id),
            count=len(report.skipped_assets),
        )
    log.info(
        "auto_coverage_reconciled",
        connection_id=str(connection.id),
        suites_created=report.suites_created,
        checks_created=report.checks_created,
        paused=report.suites_paused,
        resumed=report.suites_resumed,
        truncated=report.truncated,
    )
    return report


def reconcile_all(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Every connection that has, or had, automatic suites; one failing connection never stops
    the rest."""
    connection_ids = {
        *session.scalars(select(Connection.id).where(Connection.type.in_(INVENTORY_TYPES))),
    }
    totals = {"connections": 0, "suites_created": 0, "checks_created": 0, "paused": 0, "failed": 0}
    for connection_id in sorted(connection_ids, key=str):
        connection = session.get(Connection, connection_id)
        if connection is None:
            continue
        has_auto = session.scalar(
            select(Suite.id).where(Suite.connection_id == connection_id, Suite.origin == "auto")
        )
        if not coverage_on(connection) and has_auto is None:
            continue
        try:
            report = reconcile_connection(session, connection, now=now)
        except Exception:
            session.rollback()
            log.warning(
                "auto_coverage_reconcile_failed", connection_id=str(connection_id), exc_info=True
            )
            totals["failed"] += 1
            continue
        totals["connections"] += 1
        totals["suites_created"] += report.suites_created
        totals["checks_created"] += report.checks_created
        totals["paused"] += report.suites_paused
    return totals
