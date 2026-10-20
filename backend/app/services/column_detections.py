"""Profiler-detected column classifications — ADR 0049.

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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, AssetColumnClassification
from backend.app.services import audit_service
from backend.app.services.column_classification import value_detector, value_signal_summary
from backend.app.services.column_tags import SENSITIVE, fold_key

log = get_logger(__name__)

#: The one source this module writes. The governance-catalog pull is the next value.
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
    persisting one would add nothing. One detection per folded column name: two columns a
    quoted identifier lets differ only by case share one classification cell (the first wins).
    """
    out: dict[str, Detection] = {}
    for col in columns:
        name = str(col.column).strip()
        key = fold_key(name)
        if not name or key in out:
            continue
        values = [tv.get("value") for tv in (getattr(col, "top_values", None) or [])]
        counts = value_signal_summary(values)
        if counts is None:
            continue
        detector = value_detector(counts)
        if detector is None:
            continue
        out[key] = Detection(column=name, detector=detector, evidence=dict(counts))
    return list(out.values())


def record_profile_detections(
    session: Session,
    asset: Asset,
    columns: Sequence[Any],
    *,
    full_profile: bool = False,
    now: datetime | None = None,
) -> int:
    """Persist the detections a profile of ``asset``'s own target produced; returns how many
    are NEW rows (the figure the `pii_drift` monitor counts).

    A column detected before keeps its ``first_detected_at`` and moves ``last_detected_at``
    (also when it was dismissed — the dismissal stands, the page can still say "still
    detected"). A column detected before and NOT seen this time is left alone: the SQL evidence
    is the top values, and their absence from a later profile proves little (ADR 0049 §4).
    ``detected_classifications_refreshed_at`` is stamped only by a ``full_profile`` — one that
    covered every column of the table — and then even when nothing was found, so "looked and
    found none" differs from "never looked"; a one-column probe records what it saw and claims
    nothing about the rest.

    Each detection is one ``INSERT … ON CONFLICT`` on the case-folded cell, so two profiles of
    the same asset racing each other both land. The writes run inside a SAVEPOINT and the
    session is then committed — the contract `refresh_asset_column_tags` already has on the
    same profile paths.
    """
    stamp = now or datetime.now(UTC)
    detections = detect(columns)
    created = 0
    with session.begin_nested():
        for d in detections:
            stmt = pg_insert(AssetColumnClassification).values(
                asset_id=asset.id,
                column_name=d.column,
                source=SOURCE_PROFILER,
                verdict=SENSITIVE,
                detector=d.detector,
                evidence=d.evidence,
                first_detected_at=stamp,
                last_detected_at=stamp,
            )
            upsert = stmt.on_conflict_do_update(
                index_elements=[
                    AssetColumnClassification.asset_id,
                    AssetColumnClassification.source,
                    func.lower(AssetColumnClassification.column_name),
                ],
                set_={
                    "detector": stmt.excluded.detector,
                    "evidence": stmt.excluded.evidence,
                    "last_detected_at": stmt.excluded.last_detected_at,
                },
            ).returning(AssetColumnClassification.first_detected_at)
            if session.execute(upsert).scalar_one() == stamp:
                created += 1
        if full_profile:
            asset.detected_classifications_refreshed_at = stamp
    session.commit()
    # The upserts bypassed the identity map; a row loaded earlier in this session must re-read.
    session.expire_all()
    if detections:
        log.info(
            "column_detections_recorded",
            asset_id=str(asset.id),
            detected=len(detections),
            new=created,
        )
    return created


def record_profile_detections_safely(
    session: Session, asset: Asset, columns: Sequence[Any], *, full_profile: bool = False
) -> None:
    """`record_profile_detections` for the profile paths: a failure to persist is logged and
    never fails the profile that produced the evidence. The writes run in their own SAVEPOINT,
    so a failure leaves whatever the caller had pending untouched."""
    try:
        record_profile_detections(session, asset, columns, full_profile=full_profile)
    except Exception as exc:
        log.warning(
            "column_detections_record_failed",
            asset_id=str(asset.id),
            error_type=type(exc).__name__,
        )


def active_detected_columns(session: Session, asset: Asset) -> list[str]:
    """Folded names of the columns with an active (not dismissed) detection."""
    rows = session.scalars(
        select(AssetColumnClassification.column_name).where(
            AssetColumnClassification.asset_id == asset.id,
            AssetColumnClassification.dismissed_at.is_(None),
        )
    )
    return sorted({fold_key(name) for name in rows})


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
            func.lower(AssetColumnClassification.column_name) == fold_key(column),
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
