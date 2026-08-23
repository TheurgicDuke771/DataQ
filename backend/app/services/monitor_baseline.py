"""The `monitor_baselines` store — shared by every *stateful* monitor kind."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.db.models import Check, MonitorBaseline

# The table's UNIQUE(check_id) constraint — the ON CONFLICT target below.
BASELINE_UNIQUE_CONSTRAINT = "uq_monitor_baselines_check"


def get_baseline(
    session: Session, check_id: uuid.UUID, *, for_update: bool = False
) -> MonitorBaseline | None:
    """The check's current baseline row, or ``None``."""
    stmt = select(MonitorBaseline).where(MonitorBaseline.check_id == check_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def rebaseline(session: Session, check: Check) -> bool:
    """Drop the check's stored baseline so the NEXT run recaptures it live."""
    row = get_baseline(session, check.id)
    if row is None:
        return False
    session.delete(row)
    return True


def insert_baseline_if_absent(
    session: Session, *, check_id: uuid.UUID, kind: str, baseline: dict[str, Any]
) -> None:
    """Capture a first baseline, tolerating a concurrent capture of the same check."""
    session.execute(
        pg_insert(MonitorBaseline)
        .values(check_id=check_id, kind=kind, baseline=baseline)
        .on_conflict_do_nothing(constraint=BASELINE_UNIQUE_CONSTRAINT)
    )
