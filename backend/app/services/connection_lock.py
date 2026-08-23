"""Row-locking a ``connections`` row for a best-effort bookkeeping write."""

from __future__ import annotations

import time
import uuid

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Connection

log = get_logger(__name__)

# Postgres SQLSTATE for "could not obtain lock within the timeout".
_LOCK_NOT_AVAILABLE = "55P03"

# How many times to try for the lock before giving up.
_LOCK_ATTEMPTS = 2
_LOCK_RETRY_SECONDS = 0.25


def _is_lock_timeout(exc: OperationalError) -> bool:
    """Whether this is lock contention, as opposed to a real database fault."""
    return getattr(getattr(exc, "orig", None), "pgcode", None) == _LOCK_NOT_AVAILABLE


def lock_connection(session: Session, connection_id: uuid.UUID) -> Connection | None:
    """Row-lock a connection for a health write; ``None`` if it is gone or contended."""
    for attempt in range(_LOCK_ATTEMPTS):
        try:
            return session.get(Connection, connection_id, with_for_update=True)
        except OperationalError as exc:
            session.rollback()
            if not _is_lock_timeout(exc):
                raise  # a real DB fault must never masquerade as lock contention
            if attempt + 1 < _LOCK_ATTEMPTS:
                time.sleep(_LOCK_RETRY_SECONDS)
                continue
            log.warning(
                "connection_health_lock_contended",
                connection_id=str(connection_id),
                attempts=_LOCK_ATTEMPTS,
            )
    return None
