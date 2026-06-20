"""Cron-expression helpers for suite run schedules (A7).

Thin, FastAPI-free wrapper over `croniter` + `zoneinfo`: validate a 5-field cron
expression and an IANA timezone, and compute the next fire time. The schedule
model stores `next_run_at` precomputed by `next_fire` so the beat dispatcher
never parses cron on its hot path — see `db.models.Schedule`.

**No-backfill semantics** live here: `next_fire` always returns the next fire
*strictly after* its base instant, so advancing a fired schedule jumps past any
slots missed during downtime — a recovery fires at most once, not once per
missed interval (correct for monitoring; confirmed design choice).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterError, croniter

from backend.app.core.errors import DataQError

# A fixed, timezone-agnostic base for the write-time validity probe below. An
# impossible *calendar* cron (e.g. Feb 30) never fires in any timezone, so the
# probe doesn't depend on which one — only on the calendar fields.
_PROBE_BASE = datetime(2000, 1, 1, tzinfo=UTC)


class InvalidCronError(DataQError):
    status_code = 422
    code = "invalid_cron"


class InvalidTimezoneError(DataQError):
    status_code = 422
    code = "invalid_timezone"


def validate_timezone(timezone: str) -> ZoneInfo:
    """Resolve an IANA timezone name or raise a 422. Returns the `ZoneInfo`."""
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezoneError(
            f"unknown timezone {timezone!r}", detail={"timezone": timezone}
        ) from exc


def validate_cron(expression: str) -> None:
    """Validate a 5-field cron expression or raise a 422.

    `croniter.is_valid` also accepts its non-standard extensions (e.g. `@hourly`,
    seconds fields); we keep to the documented 5-field standard so the stored
    expression is portable and the UI can render it predictably.
    """
    fields = expression.split()
    if len(fields) != 5:
        raise InvalidCronError(
            "cron expression must have exactly 5 fields (min hour dom mon dow)",
            detail={"cron": expression},
        )
    if not croniter.is_valid(expression):
        raise InvalidCronError(
            f"invalid cron expression {expression!r}", detail={"cron": expression}
        )
    # `is_valid` only checks field *syntax* — it returns True for impossible
    # calendar dates like "0 0 30 2 *" (Feb 30), which then raise
    # CroniterBadDateError at fire time. Probe one fire so an unsatisfiable cron
    # is a clean 422 here, not a 500 on create or a crashed dispatcher tick later.
    try:
        croniter(expression, _PROBE_BASE).get_next(datetime)
    except CroniterError as exc:
        raise InvalidCronError(
            f"cron expression {expression!r} never matches a real date",
            detail={"cron": expression},
        ) from exc


def next_fire(expression: str, timezone: str, *, after: datetime | None = None) -> datetime:
    """Next fire time strictly after ``after`` (default now), as a UTC-aware datetime.

    The cron is evaluated in ``timezone`` (DST-aware), so "0 6 * * *" in
    America/New_York fires at 06:00 local across DST boundaries; the returned
    instant is normalised to UTC for storage on ``Schedule.next_run_at``.
    Validates both inputs first (422 on either).
    """
    validate_cron(expression)
    tz = validate_timezone(timezone)
    base = (after or datetime.now(UTC)).astimezone(tz)
    nxt: datetime = croniter(expression, base).get_next(datetime)
    return nxt.astimezone(UTC)
