"""Datasource credential health, recorded at the ONE credential-use seam (#1697).

#828's blindness in a new place: a datasource connection whose credential has expired
or been revoked has no visible state anywhere until someone reads worker logs (#954 —
two dead Snowflake PATs cost real time exactly that way). #839 covers orchestration
connections only; #1100's staleness axis covers lineage-refreshing ones.

The signal piggybacks on outcomes DataQ already produces — runs, dry-runs, connection
tests, profiles — rather than a periodic live probe: a probe would spend warehouse
credits on every connection on a schedule and would need its own beat task, for a fact
the platform can read for free from work it is already doing.

`credential_use` is the seam. It is deliberately a context manager wrapped around
a whole datasource OPERATION rather than a hook inside each adapter: the
guard-at-one-door-and-not-its-sibling class is this repo's known failure mode,
and six adapters times several methods each is six times the surface for a door
to be missed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Connection
from backend.app.services.connection_lock import lock_connection
from backend.app.services.failure_classifier import AUTH_FAILURE_REASON, is_auth_failure

log = get_logger(__name__)

CredentialStatus = Literal["healthy", "failing", "unknown"]


def is_datasource(connection_type: str) -> bool:
    """Whether a connection type is a datasource, not an orchestration provider."""
    return connection_type not in ORCHESTRATION_PROVIDERS


def credential_status(conn: Connection) -> CredentialStatus:
    """The three-state signal. Never `healthy` on a credential never used (#828)."""
    if conn.consecutive_auth_failures:
        return "failing"
    if conn.last_auth_success_at is None:
        # No successful use on record. A row that has ONLY ever failed is already
        # `failing` above; reaching here means nothing has been observed at all.
        return "unknown"
    return "healthy"


def record_credential_success(session: Session, *, connection_id: uuid.UUID) -> int:
    """Mark a credential as working: stamp the success, clear the failure streak."""
    locked = lock_connection(session, connection_id)
    if locked is None:  # deleted or contended — bookkeeping never blocks the caller
        return 0
    previous = locked.consecutive_auth_failures or 0
    locked.last_auth_success_at = datetime.now(UTC)
    locked.last_auth_error = None
    locked.consecutive_auth_failures = 0
    session.commit()
    if previous:
        log.info(
            "credential_health_recovered",
            connection_id=str(connection_id),
            previous_failures=previous,
        )
    return previous


def record_credential_failure(
    session: Session, *, connection_id: uuid.UUID, exc: BaseException
) -> int:
    """Record a credential rejection and grow the streak. Returns the new streak."""
    locked = lock_connection(session, connection_id)
    if locked is None:
        return 0
    locked.last_auth_failure_at = datetime.now(UTC)
    # Classified, never raw — driver auth errors routinely embed the SAS/DSN/token.
    locked.last_auth_error = AUTH_FAILURE_REASON[:512]
    locked.consecutive_auth_failures = (locked.consecutive_auth_failures or 0) + 1
    session.commit()
    log.warning(
        "credential_health_auth_failed",
        connection_id=str(connection_id),
        consecutive_auth_failures=locked.consecutive_auth_failures,
        error_type=type(exc).__name__,
    )
    return locked.consecutive_auth_failures


class CredentialUse:
    """The handle `credential_use` yields, for callers that SWALLOW their exception.

    A raising caller needs nothing from it — the context manager sees the exception.
    A caller that converts a failure into a return value (the run path turns one into
    a `failed` run) must hand the exception over with `failed()`, or the clean exit
    would be recorded as a working credential.
    """

    def __init__(self, session: Session, connection_id: uuid.UUID | None) -> None:
        self._session = session
        self._connection_id = connection_id
        self._reported = False

    @property
    def reported(self) -> bool:
        return self._reported

    def failed(self, exc: BaseException) -> None:
        """Report an exception the caller is about to swallow."""
        self._reported = True
        if self._connection_id is not None and is_auth_failure(exc):
            _record_quietly(self._session, connection_id=self._connection_id, exc=exc)


@contextmanager
def credential_use(session: Session, connection: Connection | None) -> Iterator[CredentialUse]:
    """Wrap a datasource operation that uses ``connection``'s stored credential.

    A clean exit means the credential worked. An auth-class exception means it was
    rejected. Every OTHER exception leaves the signal untouched on purpose: a missing
    table, an unreachable host and a missing SELECT grant say nothing about whether
    the credential is still valid, and recording them would make the signal lie.

    Bookkeeping never changes the caller's outcome — the original exception always
    propagates, and a failure to record it is swallowed.
    """
    if connection is None or not is_datasource(connection.type) or connection.secret_ref is None:
        # Credential-less auth modes (managed identity / IAM role, ADR 0010/0011) have no
        # stored credential to have a health, and orchestration connections have #828's.
        yield CredentialUse(session, None)
        return

    use = CredentialUse(session, connection.id)
    connection_id = connection.id
    try:
        yield use
    except BaseException as exc:
        use.failed(exc)
        raise
    if not use.reported:
        _record_quietly(session, connection_id=connection_id, exc=None)


def _record_quietly(
    session: Session, *, connection_id: uuid.UUID, exc: BaseException | None
) -> None:
    """Record an outcome without ever letting the bookkeeping become the failure."""
    try:
        if exc is None:
            record_credential_success(session, connection_id=connection_id)
        else:
            record_credential_failure(session, connection_id=connection_id, exc=exc)
    except Exception:
        session.rollback()
        log.warning(
            "credential_health_record_failed", connection_id=str(connection_id), exc_info=True
        )
