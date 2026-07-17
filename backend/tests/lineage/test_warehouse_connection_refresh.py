"""Per-connection warehouse-lineage refresh + persistence tests (#858, slice 3).

`refresh_connection_lineage` is the beat task's unit: open the datasource, refresh the
edge cache, and persist the refresh state onto the connection (watermark / tier /
degraded reason / classified error). Tested against the real DB with the datasource
`_open_connection` seam faked.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.app.db.models import Connection, User
from backend.app.lineage import warehouse_refresh
from backend.app.lineage.warehouse import (
    LineageEdgePair,
    LineageTier,
    WarehouseLineageResult,
    WarehouseLineageUnavailableError,
)
from backend.app.services.asset_identity import AssetIdentity


class _FakeStore:
    def get(self, name: str) -> str:
        return "secret"

    def set(self, name: str, value: str) -> None:  # pragma: no cover - read-only double
        raise NotImplementedError

    def delete(self, name: str) -> None:  # pragma: no cover
        raise NotImplementedError


def _ident(name: str) -> AssetIdentity:
    return AssetIdentity(namespace="snowflake://ACCT", name=f"DB.S.{name}")


class _StubProvider:
    def __init__(
        self, result: WarehouseLineageResult | Exception, *, source: str, is_incremental: bool
    ) -> None:
        self._result = result
        self.source = source
        self.is_incremental = is_incremental

    def fetch_edges(self, conn: object, *, connection_config: Any, since: Any = None) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def sf_connection(db_session: Session) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ACCT"},
        secret_ref="ref",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _patch(
    monkeypatch: pytest.MonkeyPatch, provider: Any, *, open_raises: Exception | None = None
) -> None:
    """Point refresh_connection_lineage at ``provider`` and a fake _open_connection."""
    monkeypatch.setattr(
        warehouse_refresh, "get_warehouse_lineage_provider", lambda conn_type: provider
    )

    @contextlib.contextmanager
    def _fake_open(connection: Any, secret_store: Any) -> Any:
        if open_raises is not None:
            raise open_raises
        yield object()

    import backend.app.services.profile_service as profile_service

    monkeypatch.setattr(profile_service, "_open_connection", _fake_open)


def test_snapshot_refresh_persists_tier_and_no_watermark(
    sf_connection: Connection, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _StubProvider(
        WarehouseLineageResult(
            edges=(LineageEdgePair(_ident("A"), _ident("B")),),
            tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
            degraded_reason="view-level only",
        ),
        source="snowflake",
        is_incremental=False,
    )
    _patch(monkeypatch, provider)

    outcome = warehouse_refresh.refresh_connection_lineage(
        db_session, connection=sf_connection, secret_store=_FakeStore()
    )
    assert outcome is not None and outcome.live_edges == 1
    db_session.refresh(sf_connection)
    assert sf_connection.lineage_last_tier == str(LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES)
    assert sf_connection.lineage_degraded_reason == "view-level only"
    assert sf_connection.lineage_last_refresh_at is not None
    assert sf_connection.lineage_last_error is None
    assert sf_connection.lineage_watermark is None  # snapshot source keeps it NULL


def test_incremental_refresh_advances_watermark(
    sf_connection: Connection, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark = datetime(2026, 7, 1, 12, tzinfo=UTC)
    provider = _StubProvider(
        WarehouseLineageResult(
            edges=(LineageEdgePair(_ident("A"), _ident("B")),),
            tier=LineageTier.UNITY_CATALOG_SYSTEM_ACCESS,
            new_watermark=mark,
        ),
        source="unity_catalog",
        is_incremental=True,
    )
    _patch(monkeypatch, provider)

    warehouse_refresh.refresh_connection_lineage(
        db_session, connection=sf_connection, secret_store=_FakeStore()
    )
    db_session.refresh(sf_connection)
    assert sf_connection.lineage_watermark == mark
    assert sf_connection.lineage_last_tier == str(LineageTier.UNITY_CATALOG_SYSTEM_ACCESS)


def test_open_failure_records_classified_error_not_raw(
    sf_connection: Connection, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _StubProvider(
        WarehouseLineageResult.empty(LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES),
        source="snowflake",
        is_incremental=False,
    )
    # A raw exception carrying a credential-shaped string — the stored reason must be
    # classified, never this text.
    _patch(
        monkeypatch,
        provider,
        open_raises=RuntimeError("could not connect: password=SUPERSECRET host=acct"),
    )
    outcome = warehouse_refresh.refresh_connection_lineage(
        db_session, connection=sf_connection, secret_store=_FakeStore()
    )
    assert outcome is None
    db_session.refresh(sf_connection)
    assert sf_connection.lineage_last_error is not None
    assert "SUPERSECRET" not in sf_connection.lineage_last_error  # classified, not raw
    assert sf_connection.lineage_last_refresh_at is not None


def test_unavailable_records_error_and_leaves_cache(
    sf_connection: Connection, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _StubProvider(
        WarehouseLineageUnavailableError("missing grant on SNOWFLAKE db"),
        source="snowflake",
        is_incremental=False,
    )
    _patch(monkeypatch, provider)
    outcome = warehouse_refresh.refresh_connection_lineage(
        db_session, connection=sf_connection, secret_store=_FakeStore()
    )
    assert outcome is None
    db_session.refresh(sf_connection)
    assert sf_connection.lineage_last_error is not None


def test_non_warehouse_type_is_noop(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, None)  # registry returns None for a non-warehouse type
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    adls = Connection(
        name="adls", type="adls_gen2", env="dev", config={}, secret_ref="r", created_by=user.id
    )
    db_session.add(adls)
    db_session.flush()
    assert (
        warehouse_refresh.refresh_connection_lineage(
            db_session, connection=adls, secret_store=_FakeStore()
        )
        is None
    )
    db_session.refresh(adls)
    assert adls.lineage_last_refresh_at is None  # no state written
