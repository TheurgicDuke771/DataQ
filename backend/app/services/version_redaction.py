"""Redacting one snapshot row of a version table (#685): the values go, the row stays."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from backend.app.db.models import CheckVersion, ConnectionVersion


def redact(version: ConnectionVersion | CheckVersion, *, actor_id: uuid.UUID | None) -> None:
    """Empty the snapshot's ``config`` and stamp who redacted it and when. The flag is the
    ``redacted_at`` column, never a marker inside the JSONB (a config could imitate one).
    The caller commits.
    """
    version.config = {}
    version.redacted_at = datetime.now(UTC)
    version.redacted_by = actor_id
