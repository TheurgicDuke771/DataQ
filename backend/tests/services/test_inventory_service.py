"""The #919 inventory sync — opt-in gating, caps, fail-softness, and the sweep
interplay ADR 0040 leans on (last_seen advancement IS the lifecycle mechanism)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.core.secrets import SecretStore
from backend.app.db.models import Asset, Connection, User
from backend.app.services import asset_service, inventory_service
from backend.app.services.asset_identity import AssetIdentity


def _store() -> SecretStore:
    from typing import cast

    class _NoopStore:
        def get(self, name: str) -> str:
            return "sekret"

    return cast("SecretStore", _NoopStore())


def _user(db_session: Any) -> User:
    user: User | None = db_session.scalars(select(User)).first()
    if user is None:
        user = User(aad_object_id=f"aad-{uuid.uuid4().hex[:8]}", email="t@x.io")
        db_session.add(user)
        db_session.flush()
    return user


def _connection(db_session: Any, *, opted_in: bool, conn_type: str = "snowflake") -> Connection:
    config: dict[str, Any] = {"account": "ACC-1", "database": "DATAQ_DB"}
    if opted_in:
        config["inventory_sync"] = True
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config=config,
        secret_ref="ref",
        created_by=_user(db_session).id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


class _FakeProvider:
    def __init__(self, identities: tuple[AssetIdentity, ...], fail: bool = False) -> None:
        self.identities = identities
        self.fail = fail
        self.seen_limit: int | None = None

    def enumerate_tables(
        self, conn: object, *, connection_config: dict[str, object], limit: int | None = None
    ) -> tuple[AssetIdentity, ...]:
        if self.fail:
            raise RuntimeError("warehouse unreachable")
        self.seen_limit = limit
        return self.identities[:limit] if limit is not None else self.identities


@contextmanager
def _fake_open_connection(connection: Connection, secret_store: Any) -> Any:
    yield object()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeProvider]:
    """Wire the fake connection opener + a per-test provider registry."""
    providers: dict[str, _FakeProvider] = {}
    from backend.app.services import profile_service

    monkeypatch.setattr(profile_service, "_open_connection", _fake_open_connection)
    monkeypatch.setattr(
        inventory_service,
        "get_warehouse_lineage_provider",
        lambda conn_type: providers.get(conn_type),
    )
    return providers


def _idents(*names: str) -> tuple[AssetIdentity, ...]:
    return tuple(AssetIdentity(namespace="snowflake://ACC-1", name=n) for n in names)


class TestOptInAndSync:
    def test_only_opted_in_connections_sync(self, db_session: Any, wired: Any) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        _connection(db_session, opted_in=True)
        _connection(db_session, opted_in=False)

        total = inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        assert total == 1
        names = set(db_session.scalars(select(Asset.name)).all())
        assert names == {"DATAQ_DB.REFERENCE.LOOKUP"}

    def test_synced_asset_carries_connection_provenance(self, db_session: Any, wired: Any) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        conn = _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        asset = db_session.scalars(select(Asset)).one()
        assert asset.connection_id == conn.id
        assert asset.env == "dev"

    def test_one_broken_connection_never_starves_the_rest(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.A.T"))
        wired["unity_catalog"] = _FakeProvider((), fail=True)
        _connection(db_session, opted_in=True)
        uc = _connection(db_session, opted_in=True, conn_type="unity_catalog")
        uc.config = {**uc.config, "inventory_sync": True}
        db_session.flush()

        total = inventory_service.sync_asset_inventory(db_session, secret_store=_store())
        assert total == 1  # the healthy connection synced; the broken one logged

    def test_unregistered_type_is_loud_not_silent(self, db_session: Any, wired: Any) -> None:
        conn = _connection(db_session, opted_in=True)
        with pytest.raises(ValueError, match="no table enumerator"):
            inventory_service.sync_connection_inventory(
                db_session, connection=conn, secret_store=_store()
            )


class TestCap:
    def test_overflow_syncs_first_cap_in_order_and_logs(
        self, db_session: Any, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASSET_INVENTORY_MAX_TABLES", "2")
        from backend.app.core.config import get_settings

        get_settings.cache_clear()
        try:
            wired["snowflake"] = _FakeProvider(
                _idents("DATAQ_DB.A.T1", "DATAQ_DB.A.T2", "DATAQ_DB.A.T3")
            )
            conn = _connection(db_session, opted_in=True)
            synced = inventory_service.sync_connection_inventory(
                db_session, connection=conn, secret_store=_store()
            )
            assert synced == 2
            # cap+1 requested so overflow is detectable, never silently absorbed
            assert wired["snowflake"].seen_limit == 3
            names = sorted(db_session.scalars(select(Asset.name)).all())
            assert names == ["DATAQ_DB.A.T1", "DATAQ_DB.A.T2"]
        finally:
            get_settings.cache_clear()


class TestSweepInterplay:
    """The ADR 0040 lifecycle claim, pinned: a synced table never becomes a sweep
    candidate while it exists; a dropped table freezes and ages out."""

    def test_sync_keeps_a_live_table_out_of_the_sweep(self, db_session: Any, wired: Any) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        # Backdate last_seen past retention — then a fresh sync tick runs.
        asset = db_session.scalars(select(Asset)).one()
        asset.last_seen = datetime.now(UTC) - timedelta(days=40)
        db_session.flush()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        swept = asset_service.sweep_orphan_assets(db_session, retention_days=30)
        assert swept == 0
        assert db_session.scalars(select(Asset)).one().name == "DATAQ_DB.REFERENCE.LOOKUP"

    def test_a_dropped_table_ages_out_through_the_existing_sweep(
        self, db_session: Any, wired: Any
    ) -> None:
        wired["snowflake"] = _FakeProvider(_idents("DATAQ_DB.REFERENCE.LOOKUP"))
        _connection(db_session, opted_in=True)
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        # The table disappears from the warehouse: later syncs no longer return
        # it, so last_seen freezes...
        wired["snowflake"].identities = ()
        asset = db_session.scalars(select(Asset)).one()
        asset.last_seen = datetime.now(UTC) - timedelta(days=40)
        db_session.flush()
        inventory_service.sync_asset_inventory(db_session, secret_store=_store())

        # ...and the existing #770 sweep retires it. No new mechanism.
        swept = asset_service.sweep_orphan_assets(db_session, retention_days=30)
        assert swept == 1
        assert db_session.scalars(select(Asset)).first() is None
