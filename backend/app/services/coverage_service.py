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


def _freshness_column(session: Session, check_names: dict[str, Check]) -> str | None:
    """A load or event timestamp column, from the schema-drift check's captured baseline."""
    schema_check = check_names.get(SCHEMA_CHECK)
    if schema_check is None:
        return None
    baseline = session.scalar(
        select(MonitorBaseline.baseline).where(MonitorBaseline.check_id == schema_check.id)
    )
    columns = (baseline or {}).get("columns") or []
    timestamps = [
        str(c.get("name"))
        for c in columns
        if any(t in str(c.get("type", "")).upper() for t in ("TIMESTAMP", "DATETIME"))
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
) -> None:
    t = thresholds or {}
    check_service.create_check(
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
    state = dict(suite.auto_state or {})
    declined = set(state.get("declined", []))
    existing = {c.name: c for c in session.scalars(select(Check).where(Check.suite_id == suite.id))}
    created = 0

    def wanted(name: str) -> bool:
        return name not in existing and name not in declined

    if wanted(SCHEMA_CHECK):
        _add_check(
            session, suite, name=SCHEMA_CHECK, kind="schema_drift", config={}, thresholds=None
        )
        created += 1
    if wanted(VOLUME_CHECK):
        _add_check(
            session,
            suite,
            name=VOLUME_CHECK,
            kind="anomaly",
            config={"target_metric": "row_count", **_ANOMALY_WINDOW},
            thresholds=_ANOMALY_THRESHOLDS,
        )
        created += 1
    if wanted(FRESHNESS_CHECK):
        column = _freshness_column(session, existing)
        if column is not None:
            _add_check(
                session,
                suite,
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
            created += 1
        elif SCHEMA_CHECK in existing:
            # The schema is known only after the first run; before it, there is nothing to say.
            baseline_known = session.scalar(
                select(MonitorBaseline.id).where(
                    MonitorBaseline.check_id == existing[SCHEMA_CHECK].id
                )
            )
            if baseline_known is not None:
                state["freshness_gap"] = "no timestamp column to measure freshness by"
    if state != (suite.auto_state or {}):
        suite.auto_state = state
        session.commit()
    return created


def _set_schedule(session: Session, suite: Suite, *, enabled: bool, now: datetime) -> str | None:
    """Ensure the suite's daily schedule exists and is on or off; returns the transition."""
    schedule = session.scalar(select(Schedule).where(Schedule.suite_id == suite.id))
    if schedule is None:
        if not enabled:
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
    if schedule.enabled == enabled:
        return None
    schedule.enabled = enabled
    if enabled:
        schedule.next_run_at = cron.next_fire(schedule.cron, schedule.timezone, after=now)
    session.commit()
    return "resumed" if enabled else "paused"


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
        suite = _auto_suite(session, asset)
        if suite is None:
            target = target_for_asset(connection, asset)
            if target is None:
                report.skipped_assets.append(asset.name)
                continue
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
        if _set_schedule(session, suite, enabled=True, now=moment) == "resumed":
            report.suites_resumed += 1
    # Pause the connection's automatic suites that are no longer covered.
    for suite in session.scalars(
        select(Suite).where(Suite.connection_id == connection.id, Suite.origin == "auto")
    ):
        if suite.asset_id not in covered_ids:
            if _set_schedule(session, suite, enabled=False, now=moment) == "paused":
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
