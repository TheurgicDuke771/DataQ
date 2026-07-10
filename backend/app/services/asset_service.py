"""Resolve a suite's target to a first-class `assets` row (ADR 0034, gap G-d).

The write-time companion to `asset_identity.resolve_asset_identity` (the pure
resolver): it takes the resolved OpenLineage `(namespace, name)` identity and
upserts the durable `assets` row keyed on that identity, returning the asset id
the suite / run links to.

**Fail-soft is the contract.** Asset resolution is a browse/reason convenience
layered over the execution model — it must NEVER fail a suite save or a run
dispatch. Every entry point here swallows exceptions (bad/legacy config, a
targetless suite, an orchestration-type connection with no asset identity),
logs a structlog warning, and returns ``None`` so the caller leaves
``asset_id`` NULL and carries on. Precedent: `alerting.builder` deliberately
never raises into the run path.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Connection
from backend.app.services.asset_identity import resolve_asset_identity

log = get_logger(__name__)


# Assets are batched into one multi-row INSERT per this many rows (the lineage
# refresh materializes every manifest node — thousands at real scale).
_ASSET_CHUNK = 500


def _conflict_set(stmt: PgInsert, *, preserve_provenance: bool) -> dict[str, Any]:
    """The ON CONFLICT SET clause, provenance-preserving or overwriting.

    Always references the *would-be-inserted* row via ``stmt.excluded`` (correct for
    both the single- and multi-row insert). ``preserve_provenance`` (the lineage
    caller): keep the row's existing ``env``/``connection_id`` when already set —
    ``COALESCE(existing, new)`` — so a dbt refresh never flips a datasource-resolved
    asset's provenance to the dbt orchestration connection. Otherwise (the
    suite-resolution caller): overwrite with the resolving connection's values
    (last-writer-wins, the historical behaviour).
    """
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
    """Insert-or-reuse an `assets` row keyed on ``(namespace, name)``; return its id.

    The single-row low-level upsert `resolve_and_upsert_asset` (suite-target
    resolution) uses, so the ON CONFLICT shape and the savepoint fail-soft posture
    live in one place. On an existing identity it refreshes ``last_seen`` and
    (unless ``preserve_provenance``) ``env`` / ``connection_id`` (provenance hint).

    ``preserve_provenance=True`` keeps an already-set ``env``/``connection_id`` on
    conflict (``COALESCE`` — for lineage materialization, which must not clobber a
    datasource-resolved asset's provenance); the default overwrites (suite path).

    Wrapped in a **savepoint** (nested transaction): a genuine DB error here rolls
    back only this savepoint, leaving the outer transaction healthy so the
    caller's commit still succeeds — the fail-soft "never blocks the save/refresh"
    contract. The caller decides whether to catch (a bad identity) or let it
    propagate.
    """
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
    """Batch insert-or-reuse `assets`; return ``{(namespace, name): id}`` for every row.

    The many-row companion to :func:`upsert_asset` for `lineage.edges` (which
    materializes an asset per manifest node — thousands at real scale). Each ``rows``
    dict is ``{namespace, name, env, connection_id}``. Chunked into multi-row
    ``INSERT … ON CONFLICT DO UPDATE`` statements (``chunk_size`` rows each), then the
    id map is built from a **follow-up SELECT** on ``(namespace, name)`` rather than
    ``RETURNING`` — Postgres does not guarantee multi-row ``RETURNING`` order matches
    the VALUES order under ``ON CONFLICT``, so a positional zip would silently
    mis-map ids (correctness over cleverness).

    **All-or-nothing, NOT per-row savepoint-isolated** (unlike :func:`upsert_asset`):
    a chunk is one statement, so a DB error aborts the whole refresh. That matches the
    lineage caller's tested contract — `refresh_dbt_edges` wraps this fail-open and
    rolls the transaction back on any error, writing nothing rather than a partial
    graph.
    """
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
    """Resolve ``target`` to an OpenLineage asset identity and upsert its row.

    Returns the asset id for the suite / run to link, or ``None`` when the
    target is absent or cannot be resolved (fail-soft — never raises). On a
    known identity, inserts the asset or, if it already exists, refreshes its
    ``last_seen`` / ``env`` / ``connection_id`` (provenance hint) — an
    insert-or-reuse keyed on ``(namespace, name)``.
    """
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
