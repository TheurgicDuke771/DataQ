"""Row-locking a ``connections`` row for a best-effort bookkeeping write.

Extracted from `orchestration_service` (#837/#854/#855) so the inventory sync
(#1104) uses the SAME implementation rather than a second, subtly-different one.
Every caller has the same shape: a periodic sweep that has just finished talking
to a remote system and now wants to read-modify-write a health/outcome field on
the connection it just touched.

Two properties this module exists to hold, both bought with production outages:

* **The read-modify-write takes a row lock.** Overlapping sweeps of the same
  connection would otherwise both read the pre-write value and both write their
  own — a lost update. For `consecutive_poll_failures` that means a duplicated
  alert and an under-counted outage (#837); for `inventory_sync_failing_since`
  it means the *start* of a failure streak silently jumping forward, so the UI
  under-reports how long a connection has been broken (#1104).

* **The wait is bounded and never fatal.** An unbounded lock wait on this exact
  table wedged a shared beat worker and silently stopped EVERY periodic task in
  prod (#854). `lock_timeout` now lives on the engine (see `db.session`), so no
  statement can block forever; this module only decides what to do when the lock
  is contended — retry once, then give up and let the caller move on. The
  bookkeeping is worth a retry, never a stalled sweep.
"""

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

# How many times to try for the lock before giving up. Contention here is transient by
# nature (the lock is held across two statements), so one retry converts almost every
# collision into a normal write — which matters, because SKIPPING the write leaves
# `last_polled_at` stale and the UI then reports a HEALTHY poll as failing: the confident
# -and-wrong health display #828 exists to prevent (#855 review).
_LOCK_ATTEMPTS = 2
_LOCK_RETRY_SECONDS = 0.25


def _is_lock_timeout(exc: OperationalError) -> bool:
    """Whether this is lock contention, as opposed to a real database fault.

    `OperationalError` also covers a dropped connection, a server restart, an
    admin-terminated backend. Treating those as "the row was busy" would report a genuine
    DB outage as routine contention and send the next debugger down the wrong path — and
    the entire lesson of #854 is what an invisible failure costs. Anything that is not
    `lock_not_available` is re-raised.
    """
    return getattr(getattr(exc, "orig", None), "pgcode", None) == _LOCK_NOT_AVAILABLE


def lock_connection(session: Session, connection_id: uuid.UUID) -> Connection | None:
    """Row-lock a connection for a health write; ``None`` if it is gone or contended.

    The wait is bounded by the engine-level `lock_timeout`, so this can never hang. A
    contended row is retried once (contention is brief) and only then given up on — the
    caller treats the bookkeeping as best-effort, because blocking a SHARED beat task is
    never worth a health field.

    ``None`` also covers the row having been DELETED mid-sweep (a connection removed
    while the sweep was out talking to the warehouse): `Session.get` re-reads it inside
    the current transaction and returns ``None`` rather than handing back a stale ORM
    instance, which is exactly why every caller must pass an ID and not the object it
    was iterating.

    NOTE: on contention this **rolls the session back** — a lock timeout aborts the
    transaction, so it must be. Callers must reach here with nothing uncommitted pending
    (`ingest_polled_runs` commits first; the failure paths have already rolled back),
    which is why that is safe. Do not add an uncommitted write before calling this
    (#855 review).
    """
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
