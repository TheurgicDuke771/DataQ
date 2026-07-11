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

Also hosts the orphan-asset sweep (#770) — the periodic-janitor counterpart to
the resolution/upsert path above: assets accrete (ADR 0034's "last_seen + a
sweep, not deletes" posture), so `sweep_orphan_assets` is what actually retires
a row once nothing references it any more.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, exists, func, select, tuple_
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

# Orphan-sweep deletes are batched into one DELETE per this many candidate ids —
# mirrors _ASSET_CHUNK so a single sweep tick can't hold one giant transaction
# open over an assets table that has grown large.
_ORPHAN_SWEEP_CHUNK = 500

# Reference guards for the orphan sweep (#770): every FK into ``assets.id`` must
# have a ``(table, column)`` row here. This registry drives BOTH the sweep's
# NOT-EXISTS predicate and the schema-introspection test
# (`test_asset_sweep.py::test_every_asset_fk_has_a_sweep_guard`), so a new FK —
# e.g. #761 ``incidents.asset_id`` — fails the build until its guard is added.
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


def sweep_orphan_assets(
    session: Session,
    *,
    retention_days: int,
    now: datetime | None = None,
    chunk_size: int = _ORPHAN_SWEEP_CHUNK,
) -> int:
    """Delete `assets` rows past `retention_days` that nothing still references (#770).

    ADR 0034's accepted cleanup posture: asset rows accrete (a suite retargets
    away, a dbt model is dropped from the manifest, ...) and are never deleted on
    the write path — `last_seen` simply stops advancing. This sweep is what
    eventually retires them. A row is a sweep candidate only when BOTH hold:

    - ``last_seen`` is older than ``retention_days`` (the frozen-timestamp signal
      that nothing resolves/refreshes it any more);
    - it is **unreferenced** — see the guard list below.

    ``retention_days`` must be generous: it has to comfortably outlive the
    slowest suite schedule and the lineage-refresh poll cadence, or a
    legitimately-live asset would be swept and immediately re-created on the next
    refresh (default 30 via ``ASSET_ORPHAN_RETENTION_DAYS``).
    ``retention_days <= 0`` disables the sweep (returns 0 without touching the
    DB) — a clean off-switch, mirroring the other beat janitors
    (``reap_stuck_runs`` / ``purge_expired_sample_failures``).

    **Reference guard — an enforced registry, not a checklist.** `Suite.
    asset_id` / `Run.asset_id` are ``ON DELETE SET NULL`` and the lineage-edge
    FKs are ``ON DELETE CASCADE``, so the schema alone would happily let a
    referenced asset be deleted (or its edges be cascade-wiped); the
    ``_SWEEP_REFERENCE_GUARDS`` registry is what actually protects them. Every
    FK into ``assets.id`` needs a registry row — the schema-introspection test
    (``test_every_asset_fk_has_a_sweep_guard``) fails the build when a new FK
    (e.g. #761 ``incidents.asset_id``) lands without one, so a silent
    over-delete cannot ship unnoticed.

    Deletes run as chunked ``DELETE … WHERE id IN (SELECT … LIMIT n)``
    statements that carry the FULL predicate (staleness + every guard), each
    committed before the next — a large sweep (e.g. after a bulk lineage-source
    removal) never holds one giant DELETE or a sweep-long transaction open, and
    a candidate that gains a reference mid-sweep is re-checked by its own chunk's
    subquery rather than deleted from a stale snapshot. The residual race is a
    single statement wide (a reference committed in the same instant a ≥30-day
    stale asset's chunk deletes it); its blast radius is bounded — suites/runs
    go ``SET NULL`` and the next save/refresh re-creates the asset row via the
    normal upsert path. Returns the number of assets actually swept.
    """
    if retention_days <= 0:
        return 0
    moment = now or _now()
    cutoff = moment - timedelta(days=retention_days)

    swept = 0
    while True:
        candidate_chunk = (
            select(Asset.id)
            .where(Asset.last_seen < cutoff, *_sweep_guard_clauses())
            .limit(chunk_size)
            .scalar_subquery()
        )
        # session.execute(<DML>) returns a CursorResult; the typed overload widens
        # it to Result (no rowcount), so cast to read the affected-row count —
        # same pattern as `run_service.purge_expired_sample_failures`.
        result = cast(
            CursorResult[Any],
            session.execute(delete(Asset).where(Asset.id.in_(candidate_chunk))),
        )
        deleted = result.rowcount or 0
        session.commit()
        swept += deleted
        if deleted < chunk_size:
            break
    log.info(
        "orphan_assets_swept",
        count=swept,
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
    )
    return swept
