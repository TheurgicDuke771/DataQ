"""Detected seasonal periods (ADR 0048 decision 5): read from a check's stored history on
request, stored nowhere, and promoted to the declared calendar only by an admin."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import ANOMALY, MonitorConfigError, anomaly_params
from backend.app.db.models import Check, SeasonalPeriod
from backend.app.services import anomaly, check_service, seasonal, seasonal_period_service
from backend.app.services.monitor_baseline import get_baseline

log = get_logger(__name__)


class DetectedPeriodNotFoundError(DataQError):
    status_code = 404
    code = "detected_period_not_found"


def detected_periods(session: Session, check: Check) -> seasonal.Detection:
    """What the check's per-period history shows now, with the level its scoring uses."""
    if check.kind != ANOMALY:
        return seasonal.Detection(reason=seasonal.NOT_PER_PERIOD)
    try:
        params = anomaly_params(dict(check.config or {}))
    except MonitorConfigError:
        return seasonal.Detection(reason=seasonal.INVALID_CONFIG)
    if params.period is None:
        return seasonal.Detection(reason=seasonal.NOT_PER_PERIOD)
    observations = anomaly.load_observations(get_baseline(session, check.id), params)
    calendar = (
        seasonal_period_service.calendar_for(session, params.seasonal_periods)
        if params.seasonal_periods
        else None
    )
    return seasonal.detect(
        {o.ts.date(): o.value for o in observations},
        params,
        calendar,
        backfilled=frozenset(o.ts.date() for o in observations if not o.observed),
    )


def _entry_fields(found: seasonal.DetectedPeriod, fields: dict[str, Any]) -> dict[str, Any]:
    if found.kind == seasonal.RECURRING:
        if found.range is None:
            raise seasonal_period_service.SeasonalPeriodInvalidError(
                "this detected period spans a whole year and cannot be a recurring entry"
            )
        start_month, start_day, end_month, end_day = found.range
        return {
            **fields,
            "kind": seasonal.RECURRING,
            "start_month": start_month,
            "start_day": start_day,
            "end_month": end_month,
            "end_day": end_day,
        }
    return {**fields, "kind": seasonal.DATED, "start_date": found.start, "end_date": found.end}


def promote(
    session: Session,
    *,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    start: date,
    fields: dict[str, Any],
    attach: bool,
    actor_id: uuid.UUID,
) -> SeasonalPeriod:
    """A calendar entry from the detected period of ``check_id`` whose most recent occurrence
    starts on ``start``: recurring becomes a month/day range, seen once a dated entry.
    ``fields`` carries the name, note and (dated only) series. With ``attach`` the check
    names the new entry too, in the same transaction, so a refused attachment (the check has
    no ``yearly``) leaves no entry behind."""
    check = check_service.get_check(session, suite_id, check_id)
    found = next((p for p in detected_periods(session, check).periods if p.start == start), None)
    if found is None:
        raise DetectedPeriodNotFoundError(
            f"no detected period of this check starts on {start.isoformat()}; "
            "read the detected periods again",
            detail={"check_id": str(check_id), "start": start.isoformat()},
        )
    try:
        row = seasonal_period_service.create_period(
            session,
            _entry_fields(found, fields),
            actor_id=actor_id,
            annotations={
                "promoted_from": {
                    "check_id": str(check_id),
                    "kind": found.kind,
                    "start": found.start.isoformat(),
                    "end": found.end.isoformat(),
                    "years_seen": found.years_seen,
                }
            },
            commit=False,
        )
        if attach:
            config = dict(check.config or {})
            named = config.get(seasonal_period_service.CONFIG_KEY)
            config[seasonal_period_service.CONFIG_KEY] = [
                *(named if isinstance(named, list) else []),
                str(row.id),
            ]
            check_service.update_check(
                session, suite_id, check_id, config=config, actor_id=actor_id
            )
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(row)
    log.info(
        "seasonal_period_promoted",
        period_id=str(row.id),
        check_id=str(check_id),
        kind=row.kind,
        attached=attach,
    )
    return row
