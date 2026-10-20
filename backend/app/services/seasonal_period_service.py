"""The workspace calendar of declared seasonal periods (ADR 0048 decision 5).

Admins manage the entries; a check attaches them by id in its config (``seasonal_periods``),
and the run path reads the attached rows into a ``seasonal.Calendar``. An entry a check
still names cannot be deleted: the refusal names the checks.
"""

from __future__ import annotations

import calendar as _calendar
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.monitors import ANOMALY
from backend.app.db.models import SEASONAL_PERIOD_KINDS, Check, SeasonalPeriod, Suite
from backend.app.services import audit_service, seasonal

log = get_logger(__name__)

NAME_MAX: Final = 128
SERIES_MAX: Final = 128
NOTE_MAX: Final = 2000
#: The config key a check attaches entries under.
CONFIG_KEY: Final = "seasonal_periods"
_RECURRING_FIELDS: Final = ("start_month", "start_day", "end_month", "end_day")
_DATED_FIELDS: Final = ("start_date", "end_date")


class SeasonalPeriodNotFoundError(DataQError):
    status_code = 404
    code = "seasonal_period_not_found"


class SeasonalPeriodInvalidError(DataQError):
    status_code = 422
    code = "seasonal_period_invalid"


class SeasonalPeriodNameTakenError(DataQError):
    status_code = 409
    code = "seasonal_period_name_taken"


class SeasonalPeriodInUseError(DataQError):
    status_code = 409
    code = "seasonal_period_in_use"


# ───────────────────────── validation ─────────────────────────


def _text(value: Any, *, what: str, limit: int, required: bool) -> str | None:
    if value is None:
        if required:
            raise SeasonalPeriodInvalidError(f"{what} is required")
        return None
    if not isinstance(value, str):
        raise SeasonalPeriodInvalidError(f"{what} must be text")
    trimmed = value.strip()
    if not trimmed:
        if required:
            raise SeasonalPeriodInvalidError(f"{what} must not be blank")
        return None
    if len(trimmed) > limit:
        raise SeasonalPeriodInvalidError(f"{what} must be at most {limit} characters")
    return trimmed


def _month_day(month: Any, day: Any, *, what: str) -> tuple[int, int]:
    for name, value in (("month", month), ("day", day)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise SeasonalPeriodInvalidError(f"{what} {name} must be a whole number")
    if not 1 <= month <= 12:
        raise SeasonalPeriodInvalidError(f"{what} month must be between 1 and 12")
    # A leap year: 29 February is a valid bound and is taken as 28 February in other years.
    if not 1 <= day <= _calendar.monthrange(2024, month)[1]:
        raise SeasonalPeriodInvalidError(f"{what} day {day} does not exist in month {month}")
    return month, day


def normalise(fields: dict[str, Any]) -> dict[str, Any]:
    """The column values for one entry, or a 422 naming the first thing wrong. The kind's
    own fields are required and the other kind's refused."""
    kind = fields.get("kind")
    if kind not in SEASONAL_PERIOD_KINDS:
        raise SeasonalPeriodInvalidError(f"kind must be one of {', '.join(SEASONAL_PERIOD_KINDS)}")
    out: dict[str, Any] = {
        "name": _text(fields.get("name"), what="name", limit=NAME_MAX, required=True),
        "kind": kind,
        "note": _text(fields.get("note"), what="note", limit=NOTE_MAX, required=False),
        "series": None,
        **dict.fromkeys(_RECURRING_FIELDS),
        **dict.fromkeys(_DATED_FIELDS),
    }
    if kind == seasonal.RECURRING:
        stray = [k for k in (*_DATED_FIELDS, "series") if fields.get(k) is not None]
        if stray:
            raise SeasonalPeriodInvalidError(f"{stray[0]} applies only to a dated entry")
        out["start_month"], out["start_day"] = _month_day(
            fields.get("start_month"), fields.get("start_day"), what="start"
        )
        out["end_month"], out["end_day"] = _month_day(
            fields.get("end_month"), fields.get("end_day"), what="end"
        )
        return out
    stray = [k for k in _RECURRING_FIELDS if fields.get(k) is not None]
    if stray:
        raise SeasonalPeriodInvalidError(f"{stray[0]} applies only to a recurring entry")
    start, end = fields.get("start_date"), fields.get("end_date")
    if not isinstance(start, date) or not isinstance(end, date):
        missing = "start_date" if not isinstance(start, date) else "end_date"
        raise SeasonalPeriodInvalidError(f"{missing} is required for a dated entry")
    if end < start:
        raise SeasonalPeriodInvalidError("end_date must not be before start_date")
    out["start_date"], out["end_date"] = start, end
    out["series"] = _text(fields.get("series"), what="series", limit=SERIES_MAX, required=False)
    return out


# ───────────────────────── reads ─────────────────────────


def get_period(session: Session, period_id: uuid.UUID) -> SeasonalPeriod:
    row = session.get(SeasonalPeriod, period_id)
    if row is None:
        raise SeasonalPeriodNotFoundError("seasonal period not found")
    return row


def list_periods(session: Session) -> list[SeasonalPeriod]:
    return list(session.scalars(select(SeasonalPeriod).order_by(func.lower(SeasonalPeriod.name))))


def attached_ids(config: Any) -> set[uuid.UUID]:
    """The entry ids a stored config names, whatever their spelling (the authoring path
    canonicalises them, but a match must not depend on it)."""
    raw = config.get(CONFIG_KEY) if isinstance(config, dict) else None
    found: set[uuid.UUID] = set()
    for item in raw if isinstance(raw, list) else []:
        try:
            found.add(uuid.UUID(str(item)))
        except ValueError:
            continue
    return found


def _attached_rows(session: Session) -> Sequence[Any]:
    return session.execute(
        select(Check.id, Check.name, Check.config, Suite.id, Suite.name)
        .join(Suite, Suite.id == Check.suite_id)
        .where(Check.kind == ANOMALY, Check.config.has_key(CONFIG_KEY))
        .order_by(Suite.name, Check.name)
    ).all()


def attached_checks(session: Session, period_id: uuid.UUID) -> list[dict[str, Any]]:
    """The checks whose config names ``period_id``, with their suites."""
    return [
        {"id": str(cid), "name": cname, "suite_id": str(sid), "suite_name": sname}
        for cid, cname, config, sid, sname in _attached_rows(session)
        if period_id in attached_ids(config)
    ]


def attached_counts(session: Session) -> dict[uuid.UUID, int]:
    """How many checks name each entry, for the listing."""
    counts: dict[uuid.UUID, int] = {}
    for _, _, config, _, _ in _attached_rows(session):
        for found in attached_ids(config):
            counts[found] = counts.get(found, 0) + 1
    return counts


def declared(row: SeasonalPeriod) -> seasonal.DeclaredPeriod:
    return seasonal.DeclaredPeriod(
        id=row.id,
        name=row.name,
        kind=row.kind,
        start_month=row.start_month,
        start_day=row.start_day,
        end_month=row.end_month,
        end_day=row.end_day,
        start_date=row.start_date,
        end_date=row.end_date,
        series=row.series,
    )


def load_entries(
    session: Session, ids: tuple[uuid.UUID, ...], *, lock: bool = False
) -> tuple[list[SeasonalPeriod], list[uuid.UUID]]:
    """``(rows, missing ids)`` for the ids a check names. ``lock`` takes a share lock on each
    row for the caller's transaction, so a delete cannot slip between this read and the
    check's save."""
    if not ids:
        return [], []
    stmt = select(SeasonalPeriod).where(SeasonalPeriod.id.in_(ids))
    if lock:
        stmt = stmt.with_for_update(read=True)
    rows = {row.id: row for row in session.scalars(stmt)}
    return [rows[i] for i in ids if i in rows], [i for i in ids if i not in rows]


def calendar_for(session: Session, ids: tuple[uuid.UUID, ...]) -> seasonal.Calendar:
    """The attached entries for the run path. An id with no row (which the delete refusal
    should make impossible) is reported, not fatal: the check keeps running."""
    rows, missing = load_entries(session, ids)
    if missing:
        log.warning("seasonal_periods_missing", missing=[str(i) for i in missing])
    return seasonal.Calendar(entries=tuple(declared(r) for r in rows), missing=tuple(missing))


def unknown_ids(
    session: Session, ids: tuple[uuid.UUID, ...], *, lock: bool = True
) -> list[uuid.UUID]:
    """The ids among ``ids`` that name no entry. A save reads them under a share lock so a
    delete cannot land before it commits; a preview, which saves nothing, reads without."""
    return load_entries(session, ids, lock=lock)[1]


# ───────────────────────── writes ─────────────────────────


def _name_taken(session: Session, name: str, *, except_id: uuid.UUID | None) -> bool:
    stmt = select(SeasonalPeriod.id).where(func.lower(SeasonalPeriod.name) == name.lower())
    if except_id is not None:
        stmt = stmt.where(SeasonalPeriod.id != except_id)
    return session.scalar(stmt) is not None


def _series_spelling(
    session: Session, series: str | None, *, except_id: uuid.UUID | None = None
) -> str | None:
    """A series is one period across years whatever its case: a new entry takes the spelling
    the calendar already uses. The row being edited is not "the calendar": a case-only
    correction of the only entry of a series must land."""
    if series is None:
        return None
    stmt = (
        select(SeasonalPeriod.series)
        .where(func.lower(SeasonalPeriod.series) == series.lower())
        .order_by(SeasonalPeriod.created_at)
        .limit(1)
    )
    if except_id is not None:
        stmt = stmt.where(SeasonalPeriod.id != except_id)
    return session.scalar(stmt) or series


def _taken(name: str) -> SeasonalPeriodNameTakenError:
    return SeasonalPeriodNameTakenError(
        f"a seasonal period named {name!r} already exists (names are case-insensitive)",
        detail={"name": name},
    )


def create_period(
    session: Session,
    fields: dict[str, Any],
    *,
    actor_id: uuid.UUID,
    annotations: dict[str, Any] | None = None,
    commit: bool = True,
) -> SeasonalPeriod:
    """``commit=False`` leaves the row flushed for a caller that commits it with more."""
    values = normalise(fields)
    if _name_taken(session, values["name"], except_id=None):
        raise _taken(values["name"])
    values["series"] = _series_spelling(session, values["series"])
    row = SeasonalPeriod(**values, created_by=actor_id, updated_by=actor_id)
    session.add(row)
    try:
        # Two creates of one name at once: the index decides, and the loser gets the 409.
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        raise _taken(values["name"]) from None
    audit_service.record_entity_change(
        session,
        action="seasonal_period.create",
        entity_type="seasonal_period",
        entity=row,
        actor=actor_id,
        annotations=annotations,
    )
    if commit:
        session.commit()
        session.refresh(row)
        log.info("seasonal_period_created", period_id=str(row.id), kind=row.kind)
    return row


def update_period(
    session: Session, period_id: uuid.UUID, fields: dict[str, Any], *, actor_id: uuid.UUID
) -> SeasonalPeriod:
    """Replace an entry's definition. Checks keep their attachment: a changed range applies
    from their next run. A field left out keeps its value, except that a change of kind
    drops the old kind's fields, so the new kind's are the ones the edit must send."""
    row = get_period(session, period_id)
    before = audit_service.snapshot("seasonal_period", row)
    current = fields_of(row)
    if fields.get("kind") not in (None, row.kind):
        current = {k: v for k, v in current.items() if k in ("name", "kind", "note")}
    values = normalise({**current, **fields})
    if _name_taken(session, values["name"], except_id=row.id):
        raise _taken(values["name"])
    values["series"] = _series_spelling(session, values["series"], except_id=row.id)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = actor_id
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        raise _taken(values["name"]) from None
    audit_service.record_entity_change(
        session,
        action="seasonal_period.update",
        entity_type="seasonal_period",
        entity=row,
        actor=actor_id,
        before=before,
    )
    session.commit()
    session.refresh(row)
    log.info("seasonal_period_updated", period_id=str(row.id))
    return row


def fields_of(row: SeasonalPeriod) -> dict[str, Any]:
    return {
        "name": row.name,
        "kind": row.kind,
        "note": row.note,
        "series": row.series,
        **{k: getattr(row, k) for k in (*_RECURRING_FIELDS, *_DATED_FIELDS)},
    }


def delete_period(session: Session, period_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
    """Refused while any check names the entry; the refusal lists them."""
    # The row lock orders this against a check attaching the entry (which takes a share
    # lock on it): whichever commits first, the other sees the result.
    row = session.scalar(
        select(SeasonalPeriod).where(SeasonalPeriod.id == period_id).with_for_update()
    )
    if row is None:
        raise SeasonalPeriodNotFoundError("seasonal period not found")
    attached = attached_checks(session, period_id)
    if attached:
        raise SeasonalPeriodInUseError(
            f"{len(attached)} check(s) still name this seasonal period; detach them first",
            detail={"checks": attached},
        )
    before = audit_service.snapshot("seasonal_period", row)
    session.delete(row)
    session.flush()
    audit_service.record_entity_change(
        session,
        action="seasonal_period.delete",
        entity_type="seasonal_period",
        entity=None,
        actor=actor_id,
        before=before,
    )
    session.commit()
    log.info("seasonal_period_deleted", period_id=str(period_id))
