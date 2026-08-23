"""Cron-expression helpers for suite run schedules (A7)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterError, croniter

from backend.app.core.errors import DataQError

# A fixed, timezone-agnostic base for the write-time validity probe below.
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
    """Validate a 5-field cron expression or raise a 422."""
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
    # `is_valid` only checks field *syntax* — it returns True for impossible calendar dates like "0
    # 0 30 2 *" (Feb 30), which then raise CroniterBadDateError at fire time.
    try:
        croniter(expression, _PROBE_BASE).get_next(datetime)
    except CroniterError as exc:
        raise InvalidCronError(
            f"cron expression {expression!r} never matches a real date",
            detail={"cron": expression},
        ) from exc


def next_fire(expression: str, timezone: str, *, after: datetime | None = None) -> datetime:
    """Next fire time strictly after ``after`` (default now), as a UTC-aware datetime."""
    validate_cron(expression)
    tz = validate_timezone(timezone)
    base = (after or datetime.now(UTC)).astimezone(tz)
    nxt: datetime = croniter(expression, base).get_next(datetime)
    return nxt.astimezone(UTC)
