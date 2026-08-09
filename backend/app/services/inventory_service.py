"""Warehouse inventory sync (#919, ADR 0040) — make every table a known asset.

Assets materialize from three signals (suite target, run, lineage edge); a table
with none of them is INVISIBLE, not merely unmonitored — prod's UC ``reference``
schema never appeared at all. This service enumerates an opted-in connection's
tables through the ADR 0040 table-enumeration seam
(`WarehouseLineageProvider.enumerate_tables`) and upserts them into ``assets``.

Lifecycle needs no new machinery: ``upsert_assets(preserve_provenance=True)``
advances ``last_seen`` on every tick, so a table that still exists never becomes
an orphan-sweep (#770) candidate, and a dropped table freezes and ages out
through that sweep after ``ASSET_ORPHAN_RETENTION_DAYS`` — ADR 0034's
accrete-not-delete posture doing its job, not an exemption. Provenance
(``connection_id``/``env``) is stamped with COALESCE semantics, never stolen
from a datasource-resolved asset. A discovered asset renders as ADR 0037's
neutral unmonitored row (zero suites, empty scorecard) — "known but
unmonitored", never fake health.

Opt-in is per connection (``inventory_sync: true`` in the connection's config;
the Snowflake/UC form checkbox), and the sync is fail-soft per connection like
the lineage refresh — one unreachable warehouse logs and never aborts the rest.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Connection
from backend.app.lineage.warehouse import get_warehouse_lineage_provider
from backend.app.services.asset_service import upsert_assets
from backend.app.services.connection_lock import lock_connection
from backend.app.services.failure_classifier import classify_inventory_sync_error

log = get_logger(__name__)

#: Connection types the inventory sync can enumerate (the warehouse half of the
#: ADR 0040 seam; flat-file/iceberg are recorded non-goals — path-grain floods).
INVENTORY_TYPES = ("snowflake", "unity_catalog")


class InventorySyncEnumerationError(Exception):
    """The ENUMERATION QUERY itself failed — phase, not category (#1104 review).

    Raised only once the credential resolved and the warehouse connection is
    open, so everything it wraps is genuinely a warehouse answer to DataQ's own
    `SELECT`. The distinction is load-bearing for the user-facing reason: the
    classifier's PERMISSION markers are broad substrings that a secret-store
    403, a sealed vault, or an IdP handshake rejection match just as well as a
    missing grant does — and only in this phase can DataQ honestly say "grant
    SELECT on the system schema". The original exception rides on ``__cause__``
    (`raise … from`), which is what gets classified; this wrapper carries no
    text of its own.
    """


def inventory_opted_in(connection: Connection) -> bool:
    """The per-connection opt-in gate: ``inventory_sync`` truthy in config."""
    return bool((connection.config or {}).get("inventory_sync"))


def sync_connection_inventory(
    session: Session, *, connection: Connection, secret_store: SecretStore
) -> int:
    """Enumerate one connection's tables into ``assets``; return the row count.

    Raises on failure — the caller (`sync_asset_inventory`) isolates
    per-connection errors so one broken warehouse never starves the rest.
    """
    provider = get_warehouse_lineage_provider(connection.type)
    if provider is None:  # registry gap, not an operator error — loud, not silent
        raise ValueError(f"no table enumerator for connection type {connection.type!r}")

    cap = get_settings().asset_inventory_max_tables
    # The connection helper lives in profile_service (it owns engine args per
    # type); imported lazily exactly like the lineage refresh does (#858).
    from backend.app.services.profile_service import _open_connection

    with _open_connection(connection, secret_store) as conn:
        try:
            # cap+1 so overflow is detectable; the enumerator itself stays cap-blind
            # (ADR 0040 — the caller owns the honesty of any truncation).
            identities = provider.enumerate_tables(
                conn,
                connection_config=dict(connection.config),
                limit=(cap + 1) if cap > 0 else None,
            )
        except Exception as exc:
            # Phase marker, not a category — see `InventorySyncEnumerationError`.
            # Wrapping ONLY this call is the whole point: everything above it
            # (secret read, engine build, driver handshake) can raise a
            # permission-shaped error that has nothing to do with a warehouse
            # grant, and must not be reported as one.
            raise InventorySyncEnumerationError() from exc

    truncated = cap > 0 and len(identities) > cap
    if truncated:
        # No silent caps: the first `cap` in catalog order sync; the overflow is
        # said out loud, because a silently-partial inventory reads as complete.
        log.warning(
            "inventory_sync_truncated",
            connection_id=str(connection.id),
            connection_type=connection.type,
            cap=cap,
            enumerated=len(identities),
        )
        identities = identities[:cap]

    rows = [
        {
            "namespace": ident.namespace,
            "name": ident.name,
            "env": connection.env,
            "connection_id": connection.id,
        }
        for ident in identities
    ]
    if rows:
        upsert_assets(session, rows, preserve_provenance=True)
        session.commit()
    log.info(
        "inventory_sync_refreshed",
        connection_id=str(connection.id),
        connection_type=connection.type,
        tables=len(rows),
        truncated=truncated,
    )
    return len(rows)


def _safe_rollback(session: Session) -> None:
    """Roll back, swallowing a rollback that itself fails.

    A session left in "needs rollback" state fails every subsequent statement, so
    the next connection in the sweep would die on its first query — the whole
    reason this module isolates per-connection failures. If even the rollback
    raises, the sweep is already doomed, but it must fail LOUDLY in the logs
    rather than by silently taking the beat task down.
    """
    try:
        session.rollback()
    except Exception:  # pragma: no cover - a rollback failing is a dead session
        log.warning("inventory_sync_rollback_failed", exc_info=True)


def _record_sync_outcome(
    session: Session,
    *,
    connection_id: uuid.UUID,
    connection_type: str,
    attempted_at: datetime,
    reason: str | None,
    table_count: int | None,
) -> None:
    """Best-effort bookkeeping: stamp one connection's sync outcome. Never raises.

    Two hazards this exists to contain, both of which would otherwise abort the
    ENTIRE nightly sweep for every remaining connection (#1104 review):

    * **Its own commit can fail.** A transient Postgres error, a deadlock, or a
      pool timeout on this bookkeeping write is not a reason to starve the other
      connections — the function's documented fail-soft contract is per
      connection, and an unguarded `session.commit()` in the caller's `except`
      block silently made it per SWEEP.

    * **The row can be gone.** The caller has just rolled back (which expires
      every instrumented attribute on the session), and a connection deleted
      mid-sweep would raise `ObjectDeletedError` on the next attribute touch. So
      this takes an ID and re-reads inside a fresh transaction rather than
      trusting a stale ORM instance — the same reason
      `orchestration_service.record_poll_failure` does.

    Row-locked via the shared `lock_connection`, for the reason its module
    documents: this is a read-modify-write, and `inventory_sync_failing_since`
    must not be lost-updated by an overlapping sweep (two sweeps would both read
    NULL and both write their own "now", walking the start of a failure streak
    forward and under-reporting how long the connection has been broken).

    ``table_count`` is the enumerated row count on a SUCCESSFUL attempt, or
    ``None`` on a failed one (#1242) — a failure has no count to report, so
    `inventory_sync_last_table_count`/`inventory_sync_zero_since` are left
    exactly as they were rather than overwritten with a non-answer. On success,
    the transition rule is: dropping from a previously-recorded N>0 to 0 stamps
    `inventory_sync_zero_since` (the privilege-loss/dropped-database signal);
    staying at 0 leaves it untouched (so it still reads "since the drop", not
    "since the latest zero tick"); going back above 0 clears it. A connection
    that has never recorded anything but 0 never sets it — that is the neutral,
    "empty by design" state, not a failure.
    """
    try:
        connection = lock_connection(session, connection_id)
        if connection is None:
            # Deleted mid-sweep, or the row is contended — either way the
            # bookkeeping is not worth blocking or crashing a shared beat task.
            log.info(
                "inventory_sync_outcome_skipped",
                connection_id=str(connection_id),
                connection_type=connection_type,
            )
            return
        connection.inventory_sync_last_attempted_at = attempted_at
        # Truncated to the column width like `last_poll_error` — every reason is a
        # fixed constant today, but a silent DataError here would fail the write
        # for a connection that is ALREADY failing, i.e. exactly when the state
        # matters most.
        connection.inventory_sync_last_error = reason[:512] if reason is not None else None
        if reason is None:
            connection.inventory_sync_failing_since = None
        elif connection.inventory_sync_failing_since is None:
            # First failure after a healthy tick — the streak starts here and is
            # left untouched by every later failure, so the UI can say "failing
            # since <ts>" rather than merely "failing".
            connection.inventory_sync_failing_since = attempted_at

        if table_count is not None:  # only a SUCCESSFUL attempt has a count to record
            previous_count = connection.inventory_sync_last_table_count
            if previous_count is not None and previous_count > 0 and table_count == 0:
                connection.inventory_sync_zero_since = attempted_at
            elif table_count > 0:
                connection.inventory_sync_zero_since = None
            connection.inventory_sync_last_table_count = table_count

        session.commit()
    except Exception:
        log.warning(
            "inventory_sync_outcome_write_failed",
            connection_id=str(connection_id),
            connection_type=connection_type,
            exc_info=True,
        )
        _safe_rollback(session)


def _has_sync_state(connection: Connection) -> bool:
    return (
        connection.inventory_sync_last_attempted_at is not None
        or connection.inventory_sync_last_error is not None
        or connection.inventory_sync_failing_since is not None
        or connection.inventory_sync_last_table_count is not None
        or connection.inventory_sync_zero_since is not None
    )


def _clear_opted_out_state(session: Session, stale: Sequence[uuid.UUID]) -> None:
    """Reset the outcome columns on connections that have opted OUT. Never raises.

    Without this, a connection that was failing when its `inventory_sync` toggle
    was turned off keeps `inventory_sync_last_error`/`_failing_since` forever —
    and re-enabling it months later would render a stale "failing since <old
    date>" badge describing a sync that has not run since. The state describes
    the last attempt, so when there are no longer any attempts it must be blank,
    not frozen.
    """
    if not stale:
        return
    try:
        session.execute(
            update(Connection)
            .where(Connection.id.in_(list(stale)))
            .values(
                inventory_sync_last_attempted_at=None,
                inventory_sync_last_error=None,
                inventory_sync_failing_since=None,
                inventory_sync_last_table_count=None,
                inventory_sync_zero_since=None,
            )
        )
        session.commit()
        log.info("inventory_sync_state_cleared_on_opt_out", connections=len(stale))
    except Exception:  # pragma: no cover - defensive; the sweep must outlive it
        log.warning("inventory_sync_state_clear_failed", exc_info=True)
        _safe_rollback(session)


def sync_asset_inventory(session: Session, *, secret_store: SecretStore) -> int:
    """One sweep over every opted-in connection; returns total tables synced.

    Fail-soft per connection (the lineage-refresh discipline): an unreachable
    warehouse logs a classified line and the sweep continues — and the failure
    is per-connection VISIBLE in logs rather than aborting the beat task. That
    contract covers the BOOKKEEPING too: see `_record_sync_outcome`, which never
    raises, because an unguarded commit there used to make one transient DB
    error skip every connection after it.

    Also records the outcome onto the connection itself (#1104) — mirroring the
    `lineage_last_*` pattern (#858): `inventory_sync_last_attempted_at` is
    stamped on EVERY attempt, `inventory_sync_last_error` holds a classified
    reason (NULL on success), and `inventory_sync_failing_since` marks the start
    of the current failure streak (NULL while healthy). Before these three
    columns, a connection whose principal couldn't read the enumeration query
    failed every tick invisibly to the user — toggle on, connection test green,
    zero assets, no surface said why (#828 shape).
    """
    connections = (
        session.scalars(select(Connection).where(Connection.type.in_(INVENTORY_TYPES)))
        .unique()
        .all()
    )
    # Read everything the sweep needs off these instances in ONE pass, before any
    # commit or rollback below expires them. Each per-connection failure rolls the
    # session back, and a later `connection.type` (or the opt-in read itself) would
    # then re-SELECT — raising `ObjectDeletedError` if that OTHER connection had
    # meanwhile been deleted, aborting the whole sweep for a row it wasn't even
    # working on. Ids up front + a re-fetch per iteration keeps that local.
    targets = [(c.id, c.type) for c in connections if inventory_opted_in(c)]
    _clear_opted_out_state(
        session, [c.id for c in connections if not inventory_opted_in(c) and _has_sync_state(c)]
    )
    total = 0
    for connection_id, connection_type in targets:
        connection = session.get(Connection, connection_id)
        if connection is None:  # deleted since the snapshot — nothing to sync or stamp
            log.info("inventory_sync_connection_vanished", connection_id=str(connection_id))
            continue
        now = datetime.now(UTC)
        try:
            synced = sync_connection_inventory(
                session, connection=connection, secret_store=secret_store
            )
        except Exception as exc:
            _safe_rollback(session)
            # Classify the ROOT cause; the enumeration wrapper is a phase marker
            # that carries no message of its own.
            during_enumeration = isinstance(exc, InventorySyncEnumerationError)
            root = exc.__cause__ if during_enumeration and exc.__cause__ is not None else exc
            reason = classify_inventory_sync_error(
                root, connection_type, during_enumeration=during_enumeration
            )
            log.warning(
                "inventory_sync_connection_failed",
                connection_id=str(connection_id),
                connection_type=connection_type,
                reason=reason,
                during_enumeration=during_enumeration,
                exc_info=True,
            )
            _record_sync_outcome(
                session,
                connection_id=connection_id,
                connection_type=connection_type,
                attempted_at=now,
                reason=reason,
                table_count=None,  # the attempt never produced a count — leave it be
            )
        else:
            total += synced
            _record_sync_outcome(
                session,
                connection_id=connection_id,
                connection_type=connection_type,
                attempted_at=now,
                reason=None,
                table_count=synced,
            )
    return total
