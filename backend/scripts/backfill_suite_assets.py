"""One-time backfill: link every targeted suite that has no asset to the asset of its table.

Run once, against each deployment, after the contract-import fix lands:

    python -m backend.scripts.backfill_suite_assets

## Why this exists

A suite created by importing a data contract was saved without resolving its target to an
asset, unlike a suite created any other way. A run takes its asset from its suite, so those
suites' runs opened no incidents, did not appear on the asset pages, and their reads got no
masking from the table's warehouse tags. The import now resolves the asset; this links the
suites created before it did.

Idempotent: it only touches suites with a target and no asset, and resolving is fail-soft (a
target that cannot be resolved stays unlinked and is counted). Past runs keep the asset they
were stamped with; only runs from now on carry the link.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Connection, Suite
from backend.app.db.session import get_session
from backend.app.services.asset_service import resolve_and_upsert_asset


def backfill(session: Session) -> tuple[int, int]:
    """``(linked, unresolved)`` over every targeted suite with no asset."""
    suites = list(
        session.scalars(select(Suite).where(Suite.target.isnot(None), Suite.asset_id.is_(None)))
    )
    linked = unresolved = 0
    connections: dict[object, Connection | None] = {}
    for suite in suites:
        if suite.connection_id not in connections:
            connections[suite.connection_id] = session.get(Connection, suite.connection_id)
        connection = connections[suite.connection_id]
        asset_id = (
            resolve_and_upsert_asset(session, connection, suite.target) if connection else None
        )
        if asset_id is None:
            unresolved += 1
            continue
        suite.asset_id = asset_id
        linked += 1
    session.commit()
    return linked, unresolved


def main() -> int:
    session = get_session()
    try:
        linked, unresolved = backfill(session)
    finally:
        session.close()
    print(f"linked {linked} suite(s) to their asset; {unresolved} target(s) could not be resolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
