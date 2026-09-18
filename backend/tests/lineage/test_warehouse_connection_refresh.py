"""Per-connection warehouse-lineage refresh + persistence tests (#858, slice 3)."""

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
from backend.tests.support.fake_secret_store import FakeSecretStore


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
        db_session,
        connection=sf_connection,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
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
        db_session,
        connection=sf_connection,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
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
        db_session,
        connection=sf_connection,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
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
        db_session,
        connection=sf_connection,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
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
            db_session,
            connection=adls,
            secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        )
        is None
    )
    db_session.refresh(adls)
    assert adls.lineage_last_refresh_at is None  # no state written


def test_a_healthy_pruning_connection_is_not_reported_as_suspended(
    sf_connection: Connection, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full path, both layers: refresh -> persisted state -> `warehouse_lineage_status`.

    The suspension is DERIVED by comparing `lineage_last_refresh_at` against the prune
    marker, and the two used to come from different clocks — `clock_timestamp()` inside
    `_persist` for the marker, then a strictly-later `datetime.now(UTC)` for the refresh
    stamp — so a perfectly healthy, fully-pruning connection compared as permanently
    suspended. Every unit fixture set the two timestamps exactly EQUAL, a state the writer
    never produced, so the defect was invisible to them by construction.
    """
    from backend.app.services.asset_view_service import warehouse_lineage_status

    provider = _StubProvider(
        WarehouseLineageResult(
            edges=(LineageEdgePair(_ident("A"), _ident("B")),),
            tier=LineageTier.SNOWFLAKE_GET_LINEAGE,
        ),
        source="snowflake",
        is_incremental=False,
    )
    _patch(monkeypatch, provider)

    outcome = warehouse_refresh.refresh_connection_lineage(
        db_session,
        connection=sf_connection,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
    )
    assert outcome is not None and outcome.prune_suspended is False
    db_session.refresh(sf_connection)
    assert sf_connection.lineage_last_authoritative_refresh_at is not None
    assert sf_connection.lineage_last_refresh_at is not None
    assert (
        sf_connection.lineage_last_refresh_at <= sf_connection.lineage_last_authoritative_refresh_at
    )

    reported = [
        s for s in warehouse_lineage_status(db_session) if s.connection_id == sf_connection.id
    ]
    assert reported == [], f"a healthy pruning connection was reported: {reported}"


def test_a_partial_refresh_is_reported_as_suspended_end_to_end(
    sf_connection: Connection, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same path — the surface must still fire when it should, or
    the fix above could have been "never report anything".
    """
    from backend.app.services.asset_view_service import warehouse_lineage_status

    clean = _StubProvider(
        WarehouseLineageResult(
            edges=(LineageEdgePair(_ident("A"), _ident("B")),),
            tier=LineageTier.SNOWFLAKE_GET_LINEAGE,
        ),
        source="snowflake",
        is_incremental=False,
    )
    _patch(monkeypatch, clean)
    warehouse_refresh.refresh_connection_lineage(
        db_session,
        connection=sf_connection,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
    )
    db_session.refresh(sf_connection)
    pruned_at = sf_connection.lineage_last_authoritative_refresh_at
    assert pruned_at is not None

    partial = _StubProvider(
        WarehouseLineageResult(
            edges=(LineageEdgePair(_ident("A"), _ident("B")),),
            tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
            degraded_reason="view-level lineage only — get_lineage: call failed (RuntimeError)",
            prunable=False,
        ),
        source="snowflake",
        is_incremental=False,
    )
    _patch(monkeypatch, partial)
    outcome = warehouse_refresh.refresh_connection_lineage(
        db_session,
        connection=sf_connection,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
    )
    assert outcome is not None and outcome.prune_suspended is True
    db_session.refresh(sf_connection)

    reported = [
        s for s in warehouse_lineage_status(db_session) if s.connection_id == sf_connection.id
    ]
    assert len(reported) == 1
    assert reported[0].prune_suspended is True
    assert reported[0].prune_suspended_since == pruned_at
