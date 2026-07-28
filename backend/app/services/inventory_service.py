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

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Connection
from backend.app.lineage.warehouse import get_warehouse_lineage_provider
from backend.app.services.asset_service import upsert_assets

log = get_logger(__name__)

#: Connection types the inventory sync can enumerate (the warehouse half of the
#: ADR 0040 seam; flat-file/iceberg are recorded non-goals — path-grain floods).
INVENTORY_TYPES = ("snowflake", "unity_catalog")


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
        # cap+1 so overflow is detectable; the enumerator itself stays cap-blind
        # (ADR 0040 — the caller owns the honesty of any truncation).
        identities = provider.enumerate_tables(
            conn,
            connection_config=dict(connection.config),
            limit=(cap + 1) if cap > 0 else None,
        )

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


def sync_asset_inventory(session: Session, *, secret_store: SecretStore) -> int:
    """One sweep over every opted-in connection; returns total tables synced.

    Fail-soft per connection (the lineage-refresh discipline): an unreachable
    warehouse logs a classified line and the sweep continues — and the failure
    is per-connection VISIBLE in logs rather than aborting the beat task.
    """
    connections = (
        session.scalars(select(Connection).where(Connection.type.in_(INVENTORY_TYPES)))
        .unique()
        .all()
    )
    total = 0
    for connection in connections:
        if not inventory_opted_in(connection):
            continue
        try:
            total += sync_connection_inventory(
                session, connection=connection, secret_store=secret_store
            )
        except Exception:
            session.rollback()
            log.warning(
                "inventory_sync_connection_failed",
                connection_id=str(connection.id),
                connection_type=connection.type,
                exc_info=True,
            )
    return total
