"""The tombstone a redacted version snapshot carries in place of its ``config`` (#685).

A snapshot table (ADR 0020) is append-only history, but a value pasted into a config field by
mistake must be removable without deleting the row — the row keeps the history's shape
(``version_no``, author, timestamp) and only the values go. The marker lives in the JSONB
``config`` so no column is added.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

REDACTED_KEY = "redacted"


def tombstone(*, actor_id: uuid.UUID | None) -> dict[str, Any]:
    """The ``config`` a redacted snapshot keeps: who redacted it and when, nothing else."""
    return {
        REDACTED_KEY: True,
        "redacted_at": datetime.now(UTC).isoformat(),
        "redacted_by": str(actor_id) if actor_id is not None else None,
    }


def is_redacted(config: Any) -> bool:
    return isinstance(config, dict) and config.get(REDACTED_KEY) is True and "redacted_at" in config


def redacted_at(config: Any) -> datetime | None:
    if not is_redacted(config):
        return None
    try:
        return datetime.fromisoformat(str(config["redacted_at"]))
    except ValueError:
        return None
