"""Service-level tests for `asset_view_service` — the branches the HTTP authz
matrix (tests/api/test_assets.py) doesn't reach: metadata partial-update
semantics, an asset with no composing suites, and the empty-input short-circuits.

Skips without TEST_DATABASE_URL (JSONB/UUID need real Postgres)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.app.db.models import Asset, Connection, User
from backend.app.services import asset_view_service as svc
from backend.app.services import suite_service


def _user(db: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db.add(u)
    db.flush()
    return u


def _conn(db: Any, owner: User) -> Connection:
    c = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(c)
    db.commit()
    return c


def test_list_empty_when_no_visible_suites(db_session: Any) -> None:
    user = _user(db_session)
    assert svc.list_visible_assets(db_session, user_id=user.id) == []


def test_summarize_asset_with_no_suites(db_session: Any) -> None:
    """An orphan asset (e.g. a dbt-lineage-only node) summarizes to an empty,
    no-run health — never raises, so the admin PATCH response works on it."""
    asset = Asset(namespace="snowflake://x", name="ORPHAN")
    db_session.add(asset)
    db_session.commit()
    admin = _user(db_session)
    summary = svc.summarize_asset(db_session, asset, user_id=admin.id, include_all=True)
    assert summary.suite_count == 0
    assert summary.worst_severity is None
    assert summary.last_run_at is None
    assert summary.checks_total == 0


def test_update_metadata_partial_leaves_untouched(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _conn(db_session, owner)
    suite = suite_service.create_suite(
        db_session,
        name="S",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )
    asset_id = suite.asset_id
    assert asset_id is not None

    # Set description only — owner stays NULL (set_owner=False).
    svc.update_asset_metadata(db_session, asset_id, description="v1", set_description=True)
    asset = db_session.get(Asset, asset_id)
    assert asset.description == "v1"
    assert asset.owner_user_id is None

    # Set owner only — description untouched (still v1).
    svc.update_asset_metadata(db_session, asset_id, owner_user_id=owner.id, set_owner=True)
    db_session.refresh(asset)
    assert asset.owner_user_id == owner.id
    assert asset.description == "v1"

    # Explicit clear of description to None (set_description=True, value None).
    svc.update_asset_metadata(db_session, asset_id, description=None, set_description=True)
    db_session.refresh(asset)
    assert asset.description is None


def test_update_metadata_unknown_raises(db_session: Any) -> None:
    with pytest.raises(svc.AssetNotFoundError):
        svc.update_asset_metadata(db_session, uuid.uuid4(), description="x", set_description=True)


def test_get_unknown_asset_raises(db_session: Any) -> None:
    user = _user(db_session)
    with pytest.raises(svc.AssetNotFoundError):
        svc.get_visible_asset(db_session, uuid.uuid4(), user_id=user.id)
