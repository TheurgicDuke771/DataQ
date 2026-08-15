"""Timestamp normalisation shared across the app — one naive→aware coercion.

`as_utc` had grown **six** private copies (`secret_sweep_service._as_utc`,
`core.secrets._ensure_aware` and its `_parse_vault_timestamp` tail,
`datasources.monitors`, `services.anomaly`, and #318's `_elapsed_ms`). Each was
written for the same reason and each was one edit away from disagreeing with the
others, which for this particular coercion is not a style problem: subtracting a
naive datetime from an aware one raises `TypeError`, and every one of those call
sites sits inside a broad `except` that would swallow it into a silently wrong
answer — "0 orphans", "no staleness", "the janitor stopped".

The values need it because they cross a **driver boundary** (a cloud SDK's
`created_on`, a warehouse's `MAX(ts)`, a JSONB round-trip, a hand-restored row).
A fixture that hand-builds an aware datetime asserts our model rather than the
driver's (#953), so the coercion has to be defensive at the boundary rather than
assumed away.
"""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """A naive datetime read as UTC; an aware one unchanged.

    Deliberately *not* a conversion — an aware value in another zone is returned
    as it is, because it already denotes an unambiguous instant and rewriting its
    zone would change how it renders without changing what it means.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def as_utc_or_none(value: datetime | None) -> datetime | None:
    """`as_utc`, passing ``None`` through — for the optional-column callers."""
    return None if value is None else as_utc(value)
