"""Timestamp normalisation shared across the app — one naive→aware coercion."""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """A naive datetime read as UTC; an aware one unchanged."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def as_utc_or_none(value: datetime | None) -> datetime | None:
    """`as_utc`, passing ``None`` through — for the optional-column callers."""
    return None if value is None else as_utc(value)
