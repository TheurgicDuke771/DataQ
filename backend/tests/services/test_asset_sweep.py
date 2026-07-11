"""Tests for the orphan-asset sweep (`asset_service.sweep_orphan_assets`, #770).

DB-backed (real Postgres): the sweep is a reference-guarded, time-windowed
delete, so it's exercised against the real engine — including the `EXISTS`
subqueries over `suites` / `runs` / `lineage_edges`, which SQLite can't host
(JSONB/UUID). Verifies each reference kind protects its asset, the
`last_seen` threshold boundary, the empty/no-op case, and chunked deletes.

Every test captures the ids it needs into plain `uuid.UUID` variables right at
creation (before any `commit()`), never touching an ORM object's attributes
afterwards: `sweep_orphan_assets` commits internally, which expires every
loaded instance (`expire_on_commit` default) and, for a row the sweep just
deleted, a later attribute touch on that same Python object raises
`ObjectDeletedError` rather than a clean "it's gone" signal — plain ids
sidestep the identity-map/session-lifecycle noise entirely.

Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from backend.app.db.models import Asset, Connection, LineageEdge, Run, Suite, User
from backend.app.services import asset_service

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _user_id(db: Any) -> uuid.UUID:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex.com")
    db.add(u)
    db.flush()
    return u.id


def _connection(db: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (connection_id, created_by)."""
    owner_id = _user_id(db)
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv-x",
        created_by=owner_id,
    )
    db.add(conn)
    db.flush()
    return conn.id, owner_id


def _asset(db: Any, *, last_seen: datetime, tag: str | None = None) -> uuid.UUID:
    suffix = tag or uuid.uuid4().hex[:8]
    a = Asset(namespace=f"ns-{suffix}", name=f"name-{suffix}", last_seen=last_seen)
    db.add(a)
    db.flush()
    return a.id


def _suite(
    db: Any, connection_id: uuid.UUID, created_by: uuid.UUID, *, asset_id: uuid.UUID | None = None
) -> uuid.UUID:
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=connection_id,
        created_by=created_by,
        target={"table": "T"},
        asset_id=asset_id,
    )
    db.add(suite)
    db.flush()
    return suite.id


def _run(db: Any, suite_id: uuid.UUID, *, asset_id: uuid.UUID | None = None) -> uuid.UUID:
    run = Run(suite_id=suite_id, status="succeeded", asset_id=asset_id)
    db.add(run)
    db.flush()
    return run.id


def _sweep(db: Any, *, retention_days: int = 30, chunk_size: int = 500) -> int:
    return asset_service.sweep_orphan_assets(
        db, retention_days=retention_days, now=NOW, chunk_size=chunk_size
    )


def _stale(days: int = 60) -> datetime:
    return NOW - timedelta(days=days)


def _asset_count(db: Any) -> int:
    return db.scalar(select(func.count()).select_from(Asset)) or 0


def _exists(db: Any, asset_id: uuid.UUID) -> bool:
    return db.scalar(select(Asset.id).where(Asset.id == asset_id)) is not None


def test_unreferenced_stale_asset_is_swept(db_session: Any) -> None:
    orphan_id = _asset(db_session, last_seen=_stale())
    db_session.commit()

    swept = _sweep(db_session)

    assert swept == 1
    assert not _exists(db_session, orphan_id)


def test_referenced_by_suite_never_swept(db_session: Any) -> None:
    conn_id, owner_id = _connection(db_session)
    asset_id = _asset(db_session, last_seen=_stale())
    _suite(db_session, conn_id, owner_id, asset_id=asset_id)
    db_session.commit()

    swept = _sweep(db_session)

    assert swept == 0
    assert _exists(db_session, asset_id)


def test_referenced_by_run_never_swept(db_session: Any) -> None:
    conn_id, owner_id = _connection(db_session)
    asset_id = _asset(db_session, last_seen=_stale())
    # The run's own suite resolves to a different (unreferenced by this test)
    # asset — the run.asset_id reference is what must protect `asset_id`.
    suite_id = _suite(db_session, conn_id, owner_id)
    _run(db_session, suite_id, asset_id=asset_id)
    db_session.commit()

    swept = _sweep(db_session)

    assert swept == 0
    assert _exists(db_session, asset_id)


def test_referenced_by_lineage_upstream_never_swept(db_session: Any) -> None:
    conn_id, _owner_id = _connection(db_session)
    upstream_id = _asset(db_session, last_seen=_stale())
    downstream_id = _asset(db_session, last_seen=_stale())
    db_session.add(
        LineageEdge(
            upstream_asset_id=upstream_id,
            downstream_asset_id=downstream_id,
            source="dbt",
            connection_id=conn_id,
        )
    )
    db_session.commit()

    swept = _sweep(db_session)

    # Both endpoints are referenced by the same edge, so neither is swept.
    assert swept == 0
    assert _exists(db_session, upstream_id)
    assert _exists(db_session, downstream_id)


def test_referenced_by_lineage_downstream_never_swept(db_session: Any) -> None:
    """Isolates the downstream leg: the upstream endpoint is fresh (would never be
    a sweep candidate on its own), so only the downstream `EXISTS` guard can be
    what's keeping the downstream row alive."""
    conn_id, _owner_id = _connection(db_session)
    upstream_id = _asset(db_session, last_seen=NOW)  # fresh — not a candidate anyway
    downstream_id = _asset(db_session, last_seen=_stale())
    db_session.add(
        LineageEdge(
            upstream_asset_id=upstream_id,
            downstream_asset_id=downstream_id,
            source="dbt",
            connection_id=conn_id,
        )
    )
    db_session.commit()

    swept = _sweep(db_session)

    assert swept == 0
    assert _exists(db_session, downstream_id)


def test_threshold_boundary_just_inside_window_not_swept(db_session: Any) -> None:
    """last_seen at exactly `retention_days` minus a second — still within the
    window — must survive."""
    asset_id = _asset(db_session, last_seen=NOW - timedelta(days=30) + timedelta(seconds=1))
    db_session.commit()

    assert _sweep(db_session, retention_days=30) == 0
    assert _exists(db_session, asset_id)


def test_threshold_boundary_just_outside_window_swept(db_session: Any) -> None:
    """last_seen at exactly `retention_days` plus a second past the cutoff — swept."""
    asset_id = _asset(db_session, last_seen=NOW - timedelta(days=30) - timedelta(seconds=1))
    db_session.commit()

    assert _sweep(db_session, retention_days=30) == 1
    assert not _exists(db_session, asset_id)


def test_empty_sweep_is_a_no_op(db_session: Any) -> None:
    """No assets at all — the sweep touches nothing and returns 0."""
    assert _sweep(db_session) == 0


def test_fresh_asset_not_swept_even_when_unreferenced(db_session: Any) -> None:
    fresh_id = _asset(db_session, last_seen=NOW - timedelta(days=1))
    db_session.commit()

    assert _sweep(db_session, retention_days=30) == 0
    assert _exists(db_session, fresh_id)


def test_disabled_when_retention_non_positive(db_session: Any) -> None:
    orphan_id = _asset(db_session, last_seen=_stale(days=9999))
    db_session.commit()

    assert asset_service.sweep_orphan_assets(db_session, retention_days=0, now=NOW) == 0
    assert asset_service.sweep_orphan_assets(db_session, retention_days=-5, now=NOW) == 0
    assert _exists(db_session, orphan_id)


def test_chunked_delete_sweeps_all_candidates_across_multiple_chunks(db_session: Any) -> None:
    """5 orphaned assets swept with chunk_size=2 forces 3 DELETE statements
    (2 + 2 + 1) — every candidate is still removed and the count is exact."""
    orphan_ids = [_asset(db_session, last_seen=_stale(), tag=f"chunk-{i}") for i in range(5)]
    db_session.commit()

    swept = _sweep(db_session, chunk_size=2)

    assert swept == 5
    for orphan_id in orphan_ids:
        assert not _exists(db_session, orphan_id)
    assert _asset_count(db_session) == 0


def test_chunking_does_not_touch_referenced_assets_mixed_in(db_session: Any) -> None:
    """A referenced asset sitting among several orphans in the same sweep survives
    while its stale, unreferenced siblings are removed — proves the reference
    guard is applied before chunking, not just at the edges of the batch."""
    conn_id, owner_id = _connection(db_session)
    keep_id = _asset(db_session, last_seen=_stale(), tag="keep")
    _suite(db_session, conn_id, owner_id, asset_id=keep_id)
    orphan_ids = [_asset(db_session, last_seen=_stale(), tag=f"drop-{i}") for i in range(4)]
    db_session.commit()

    swept = _sweep(db_session, chunk_size=2)

    assert swept == 4
    assert _exists(db_session, keep_id)
    for orphan_id in orphan_ids:
        assert not _exists(db_session, orphan_id)


def test_every_asset_fk_has_a_sweep_guard() -> None:
    """Schema-introspection enforcement of `_SWEEP_REFERENCE_GUARDS` (#770).

    Any FK into ``assets.id`` (e.g. #761's ``incidents.asset_id``) must carry a
    sweep guard, or the janitor silently over-deletes referenced assets. This
    test turns that from a code-comment checklist into a build failure.
    """
    from backend.app.db.models import Asset
    from backend.app.services.asset_service import _SWEEP_REFERENCE_GUARDS

    fk_refs = {
        (fk.parent.table.name, fk.parent.name)
        for table in Asset.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == "assets" and fk.column.name == "id"
    }
    assert fk_refs == set(_SWEEP_REFERENCE_GUARDS)


def test_referenced_by_incident_never_swept(db_session: Any) -> None:
    """#761: incident history (any state) pins its asset — the FK is CASCADE, so
    sweeping here would silently wipe incidents."""
    from backend.app.db.models import Check, Incident

    kept = _asset(db_session, last_seen=_stale(), tag="incident-ref")
    conn_id, user_id = _connection(db_session)
    suite_id = _suite(db_session, conn_id, user_id)
    check = Check(
        suite_id=suite_id,
        name="incident-anchor",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db_session.add(check)
    db_session.flush()
    db_session.add(
        Incident(
            asset_id=kept,
            check_id=check.id,
            suite_id=suite_id,
            status="resolved",
            resolved_by="user",
        )
    )
    db_session.commit()

    swept = _sweep(db_session)
    assert swept == 0
    assert db_session.get(Asset, kept) is not None
