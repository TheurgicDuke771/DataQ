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
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Connection
from backend.app.services.asset_identity import resolve_asset_identity

log = get_logger(__name__)


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
        stmt = (
            pg_insert(Asset)
            .values(
                namespace=identity.namespace,
                name=identity.name,
                env=connection.env,
                connection_id=connection.id,
            )
            .on_conflict_do_update(
                index_elements=["namespace", "name"],
                set_={
                    "last_seen": func.now(),
                    "env": connection.env,
                    "connection_id": connection.id,
                },
            )
            .returning(Asset.id)
        )
        asset_id = session.execute(stmt).scalar_one()
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
