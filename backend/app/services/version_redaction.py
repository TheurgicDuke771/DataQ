"""Redacting one snapshot row of a version table (#685): the values go, the row stays."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import CheckVersion, ConnectionVersion

#: Kept on a redacted check version: the tested column is the result's identity — the
#: as-of resolver, the masking ladder and data-subject searches read it from the snapshot,
#: so emptying it would weaken the very privacy controls redaction serves.
KEPT_CHECK_KEYS: Final[tuple[str, ...]] = ("column",)


def lock_check_version(
    session: Session, check_id: uuid.UUID, version_no: int
) -> CheckVersion | None:
    """The version row under ``FOR UPDATE``, so a redaction and a restore (or two redactions) of
    the same version serialize instead of one reading a config the other is emptying."""
    return session.scalar(
        select(CheckVersion)
        .where(CheckVersion.check_id == check_id, CheckVersion.version_no == version_no)
        .with_for_update()
    )


def lock_connection_version(
    session: Session, connection_id: uuid.UUID, version_no: int
) -> ConnectionVersion | None:
    """As `lock_check_version`, for a connection's snapshot."""
    return session.scalar(
        select(ConnectionVersion)
        .where(
            ConnectionVersion.connection_id == connection_id,
            ConnectionVersion.version_no == version_no,
        )
        .with_for_update()
    )


def latest_check_version_no(session: Session, check_id: uuid.UUID) -> int | None:
    return session.scalar(
        select(func.max(CheckVersion.version_no)).where(CheckVersion.check_id == check_id)
    )


def latest_connection_version_no(session: Session, connection_id: uuid.UUID) -> int | None:
    return session.scalar(
        select(func.max(ConnectionVersion.version_no)).where(
            ConnectionVersion.connection_id == connection_id
        )
    )


def redact(version: ConnectionVersion | CheckVersion, *, actor_id: uuid.UUID | None) -> None:
    """Empty the snapshot's ``config`` (a check version keeps ``KEPT_CHECK_KEYS``) and stamp who
    redacted it and when. The flag is the ``redacted_at`` column, never a marker inside the
    JSONB (a config could imitate one). The caller commits.
    """
    keep = KEPT_CHECK_KEYS if isinstance(version, CheckVersion) else ()
    config = version.config or {}
    version.config = {key: config[key] for key in keep if key in config}
    version.redacted_at = datetime.now(UTC)
    version.redacted_by = actor_id
