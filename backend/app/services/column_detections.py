"""Profiler-detected column classifications — ADR 0049 (#1674).

The profiler already sees every column's values (the most frequent values on SQL datasources,
a row sample on files and Iceberg). The two value detectors that fire on DATA rather than on a
column name — email addresses and encoded blobs — are persisted here as a durable classification
source, one rung below the warehouse tags and the suite policy and one above the live sample
classifier. Additive only: the verdict is always ``sensitive``, so a row can add masking and
never clear a column, and `effective_column_tags` reads it under the value ``detected`` so it
is neither the floor nor a clearance.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, AssetColumnClassification
from backend.app.services import audit_service
from backend.app.services.column_classification import value_detector, value_signal_summary
from backend.app.services.column_tags import SENSITIVE

log = get_logger(__name__)

#: The one source this module writes. The governance-catalog pull (#1690) is the next value.
SOURCE_PROFILER: Final[str] = "profiler"


class ClassificationNotFoundError(DataQError):
    status_code = 404
    code = "classification_not_found"


@dataclass(frozen=True)
class Detection:
    column: str
    detector: str
    #: `value_signal_summary` counts over the profiled values — never a value itself.
    evidence: dict[str, int]


def detect(columns: Iterable[Any]) -> list[Detection]:
    """Run the value detectors over profiled columns (anything with ``column`` and
    ``top_values``). Value-based only: a name signal is already applied live at every rung, so
    persisting one would add nothing.
    """
    out: list[Detection] = []
    for col in columns:
        values = [tv.get("value") for tv in (getattr(col, "top_values", None) or [])]
        counts = value_signal_summary(values)
        if counts is None:
            continue
        detector = value_detector(counts)
        if detector is None:
            continue
        out.append(
            Detection(
                column=str(col.column),
                detector=detector,
                evidence=dict(counts),
            )
        )
    return out


def _key(column: str) -> str:
    return column.strip().lower()


def record_profile_detections(
    session: Session, asset: Asset, columns: Sequence[Any], *, now: datetime | None = None
) -> int:
    """Persist the detections a profile of ``asset``'s own target produced; returns how many
    are NEW rows (the figure the `pii_drift` monitor, #1675, counts).

    A column detected before keeps its ``first_detected_at`` and moves ``last_detected_at``
    (also when it was dismissed — the dismissal stands, the page can still say "still
    detected"). A column detected before and NOT seen this time is left alone: the SQL evidence
    is the top values, and their absence from a later profile proves little (ADR 0049 §4).
    ``detected_classifications_refreshed_at`` is stamped even when nothing was found, so
    "looked and found none" differs from "never looked". Commits.
    """
    stamp = now or datetime.now(UTC)
    detections = detect(columns)
    existing = {
        _key(row.column_name): row
        for row in session.scalars(
            select(AssetColumnClassification).where(
                AssetColumnClassification.asset_id == asset.id,
                AssetColumnClassification.source == SOURCE_PROFILER,
            )
        )
    }
    created = 0
    for d in detections:
        row = existing.get(_key(d.column))
        if row is None:
            session.add(
                AssetColumnClassification(
                    asset_id=asset.id,
                    column_name=d.column,
                    source=SOURCE_PROFILER,
                    verdict=SENSITIVE,
                    detector=d.detector,
                    evidence=d.evidence,
                    first_detected_at=stamp,
                    last_detected_at=stamp,
                )
            )
            created += 1
        else:
            row.detector = d.detector
            row.evidence = d.evidence
            row.last_detected_at = stamp
    asset.detected_classifications_refreshed_at = stamp
    session.commit()
    if detections:
        log.info(
            "column_detections_recorded",
            asset_id=str(asset.id),
            detected=len(detections),
            new=created,
        )
    return created


def record_profile_detections_safely(
    session: Session, asset: Asset, columns: Sequence[Any]
) -> None:
    """`record_profile_detections` for the profile paths: a failure to persist is logged and
    never fails the profile that produced the evidence."""
    try:
        record_profile_detections(session, asset, columns)
    except Exception as exc:
        try:
            session.rollback()
        except Exception:  # pragma: no cover - defensive
            log.warning("column_detections_rollback_failed", exc_info=True)
        log.warning(
            "column_detections_record_failed",
            asset_id=str(asset.id),
            error_type=type(exc).__name__,
        )


def active_detected_columns(session: Session, asset: Asset) -> list[str]:
    """Lower-cased names of the columns with an active (not dismissed) detection."""
    rows = session.scalars(
        select(AssetColumnClassification.column_name).where(
            AssetColumnClassification.asset_id == asset.id,
            AssetColumnClassification.dismissed_at.is_(None),
        )
    )
    return sorted({_key(name) for name in rows})


def list_classifications(session: Session, asset_id: uuid.UUID) -> list[AssetColumnClassification]:
    """Every persisted classification of the asset, dismissed ones included, by column."""
    return list(
        session.scalars(
            select(AssetColumnClassification)
            .where(AssetColumnClassification.asset_id == asset_id)
            .order_by(func.lower(AssetColumnClassification.column_name))
        )
    )


def set_dismissed(
    session: Session,
    asset: Asset,
    column: str,
    *,
    dismissed: bool,
    actor_id: uuid.UUID | None,
) -> AssetColumnClassification:
    """Dismiss (un-mask) or restore one detection — the only way a detection is cleared, so it
    is a workspace-Admin act (gated at the API) and an audited one. Commits.
    """
    row = session.scalar(
        select(AssetColumnClassification).where(
            AssetColumnClassification.asset_id == asset.id,
            AssetColumnClassification.source == SOURCE_PROFILER,
            func.lower(AssetColumnClassification.column_name) == _key(column),
        )
    )
    if row is None:
        raise ClassificationNotFoundError(
            "no detected classification for that column",
            detail={"asset_id": str(asset.id), "column": column},
        )
    before = {"column": row.column_name, "dismissed": row.dismissed_at is not None}
    if dismissed and row.dismissed_at is None:
        row.dismissed_at = datetime.now(UTC)
        row.dismissed_by = actor_id
    elif not dismissed and row.dismissed_at is not None:
        row.dismissed_at = None
        row.dismissed_by = None
    after = {"column": row.column_name, "dismissed": row.dismissed_at is not None}
    if before != after:
        audit_service.record(
            session,
            action="asset.classification_update",
            entity_type="asset",
            entity_id=asset.id,
            actor=actor_id,
            before=before,
            after=after,
        )
    session.commit()
    session.refresh(row)
    log.info(
        "column_detection_dismissal_set",
        asset_id=str(asset.id),
        dismissed=after["dismissed"],
    )
    return row
