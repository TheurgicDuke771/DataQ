"""The `monitor_baselines` store — shared by every *stateful* monitor kind.

`schema_drift` (#592) landed the table; `anomaly` (#593) is its second consumer,
exactly as the model's docstring anticipated ("one persistence shape, two
consumers"). The row/uniqueness semantics are the same for both — one CURRENT
baseline per check, replaced not appended — so they live here rather than inside
either kind's module, which is also what keeps `anomaly` from having to import
`schema_drift` to read a row.

The payload is kind-shaped JSONB and this module deliberately does not interpret
it: `schema_drift` stores a column snapshot, `anomaly` a rolling observation
window. Neither is row data, so nothing here is in the PII/retention path.
"""

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
    """The check's current baseline row, or ``None``.

    ``for_update`` emits ``SELECT ... FOR UPDATE``, serialising concurrent runs of
    the SAME check against each other for the rest of the caller's transaction.
    Read-modify-write callers **must** pass it: `anomaly` appends this run's
    measurement to the observation list it just read, so two overlapping runs (an
    overdue schedule firing while a Run-Now is in flight) would otherwise both
    read the same list and the second commit would silently drop the first's
    measurement — a lost update that leaves no trace anywhere, because the
    baseline is a rolling window with no per-observation identity to notice a gap
    in. `schema_drift` never updates a row (it captures or diffs), so it reads
    unlocked and pays nothing.

    Deliberately opt-in rather than always-on: this is the same row the
    read-only paths (`rebaseline`'s existence check, a diff) touch, and taking a
    write lock there would serialise runs that never conflict.
    """
    stmt = select(MonitorBaseline).where(MonitorBaseline.check_id == check_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def rebaseline(session: Session, check: Check) -> bool:
    """Drop the check's stored baseline so the NEXT run recaptures it live.

    Deliberately a delete, not an immediate recapture: recapturing here would run
    datasource introspection (schema_drift) or a target query (anomaly) on the API
    request thread with the caller's patience as the timeout. Kind-agnostic — it
    is the row that is dropped, and every stateful kind treats "no row" as its
    own first-run case. Returns whether a baseline existed.
    """
    row = get_baseline(session, check.id)
    if row is None:
        return False
    session.delete(row)
    return True


def insert_baseline_if_absent(
    session: Session, *, check_id: uuid.UUID, kind: str, baseline: dict[str, Any]
) -> None:
    """Capture a first baseline, tolerating a concurrent capture of the same check.

    ON CONFLICT DO NOTHING: two concurrent first runs of one suite both see no
    baseline (READ COMMITTED) and both insert — the loser must NOT blow up the
    whole run's commit with an IntegrityError, which would discard every sibling
    result row (#122). Whichever run wins captured the same live state moments
    apart, so the loser's report stays truthful. Rides the caller's transaction,
    so a rolled-back run strands nothing.

    This is the FIRST-capture race; the update race is a different problem with a
    different answer, because a row that doesn't exist yet cannot be locked. See
    `get_baseline(for_update=True)`. Losing one observation on the very first run
    is harmless (the window is empty either way); losing one on an established
    baseline is a silent measurement gap.
    """
    session.execute(
        pg_insert(MonitorBaseline)
        .values(check_id=check_id, kind=kind, baseline=baseline)
        .on_conflict_do_nothing(constraint=BASELINE_UNIQUE_CONSTRAINT)
    )
