"""Resolve a suite's target to a first-class `assets` row (ADR 0034, gap G-d)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, func, select, tuple_
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.chunked_dml import CHUNK_SIZE, chunked_dml
from backend.app.db.models import Asset, Connection
from backend.app.services.asset_identity import resolve_asset_identity

log = get_logger(__name__)


# Assets are batched into one multi-row INSERT per this many rows (the lineage refresh materializes
# every manifest node — thousands at real scale).
_ASSET_CHUNK = 500

# Reference guards for the orphan sweep (#770): every FK into ``assets.id`` must have a ``(table,
# column)`` row here.
_SWEEP_REFERENCE_GUARDS: tuple[tuple[str, str], ...] = (
    ("suites", "asset_id"),
    ("runs", "asset_id"),
    ("lineage_edges", "upstream_asset_id"),
    ("lineage_edges", "downstream_asset_id"),
    # #761: incidents CASCADE from their asset — an asset with incident history
    # (open or resolved) is never swept, or that history would be silently wiped.
    ("incidents", "asset_id"),
)


def _sweep_guard_clauses() -> list[Any]:
    """NOT-EXISTS clause per registered reference guard, built from metadata."""
    tables = Asset.metadata.tables
    return [
        ~exists().where(tables[table_name].c[column_name] == Asset.id)
        for table_name, column_name in _SWEEP_REFERENCE_GUARDS
    ]


def _now() -> datetime:
    return datetime.now(UTC)


def _conflict_set(stmt: PgInsert, *, preserve_provenance: bool) -> dict[str, Any]:
    """The ON CONFLICT SET clause, provenance-preserving or overwriting."""
    if preserve_provenance:
        return {
            "last_seen": func.now(),
            "env": func.coalesce(Asset.env, stmt.excluded.env),
            "connection_id": func.coalesce(Asset.connection_id, stmt.excluded.connection_id),
        }
    return {
        "last_seen": func.now(),
        "env": stmt.excluded.env,
        "connection_id": stmt.excluded.connection_id,
    }


def upsert_asset(
    session: Session,
    *,
    namespace: str,
    name: str,
    env: str | None,
    connection_id: uuid.UUID | None,
    preserve_provenance: bool = False,
) -> uuid.UUID:
    """Insert-or-reuse an `assets` row keyed on ``(namespace, name)``; return its id."""
    stmt = pg_insert(Asset).values(
        namespace=namespace, name=name, env=env, connection_id=connection_id
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["namespace", "name"],
        set_=_conflict_set(stmt, preserve_provenance=preserve_provenance),
    ).returning(Asset.id)
    with session.begin_nested():
        return session.execute(upsert).scalar_one()


def upsert_assets(
    session: Session,
    rows: Sequence[dict[str, Any]],
    *,
    preserve_provenance: bool = False,
    chunk_size: int = _ASSET_CHUNK,
) -> dict[tuple[str, str], uuid.UUID]:
    """Batch insert-or-reuse `assets`; return ``{(namespace, name): id}`` for every row."""
    if not rows:
        return {}
    for start in range(0, len(rows), chunk_size):
        chunk = list(rows[start : start + chunk_size])
        stmt = pg_insert(Asset).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["namespace", "name"],
                set_=_conflict_set(stmt, preserve_provenance=preserve_provenance),
            )
        )
    keys = list({(r["namespace"], r["name"]) for r in rows})
    result = session.execute(
        select(Asset.namespace, Asset.name, Asset.id).where(
            tuple_(Asset.namespace, Asset.name).in_(keys)
        )
    )
    return {(ns, name): aid for ns, name, aid in result}


def resolve_and_upsert_asset(
    session: Session, connection: Connection, target: dict[str, Any] | None
) -> uuid.UUID | None:
    """Resolve ``target`` to an OpenLineage asset identity and upsert its row."""
    if not target:
        return None
    try:
        identity = resolve_asset_identity(connection.type, connection.config, target)
    except Exception as exc:  # fail-soft: a bad/legacy target must not block the save
        log.warning(
            "asset_resolution_failed",
            connection_id=str(connection.id),
            connection_type=connection.type,
            error=str(exc),
        )
        return None
    try:
        asset_id = upsert_asset(
            session,
            namespace=identity.namespace,
            name=identity.name,
            env=connection.env,
            connection_id=connection.id,
        )
    except Exception as exc:  # fail-soft: a DB hiccup here must not block the save
        log.warning(
            "asset_upsert_failed",
            namespace=identity.namespace,
            name=identity.name,
            error=str(exc),
        )
        return None
    log.info(
        "asset_resolved",
        asset_id=str(asset_id),
        namespace=identity.namespace,
        name=identity.name,
    )
    return asset_id


def sweep_orphan_assets(
    session: Session,
    *,
    retention_days: int,
    now: datetime | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """Delete `assets` rows past `retention_days` that nothing still references (#770)."""
    if retention_days <= 0:
        return 0
    moment = now or _now()
    cutoff = moment - timedelta(days=retention_days)

    def _build_statement() -> Any:
        candidate_chunk = (
            select(Asset.id)
            .where(Asset.last_seen < cutoff, *_sweep_guard_clauses())
            .order_by(Asset.last_seen)
            .limit(chunk_size)
            .scalar_subquery()
        )
        return delete(Asset).where(
            Asset.id.in_(candidate_chunk),
            Asset.last_seen < cutoff,
            *_sweep_guard_clauses(),
        )

    swept = chunked_dml(session, _build_statement, chunk_size=chunk_size)
    log.info(
        "orphan_assets_swept",
        count=swept,
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
    )
    return swept
