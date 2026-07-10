"""asset_service tests against a real Postgres (db_session).

`resolve_and_upsert_asset` is the write-time hook that turns a suite's target
into an `assets` row (ADR 0034). It must be an insert-or-reuse keyed on the
OpenLineage `(namespace, name)` identity, and it must be fail-soft — an
unresolvable target (orchestration connection, garbage config) returns None
without raising. Skips without TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, update

from backend.app.db.models import Asset, Connection, User
from backend.app.services.asset_service import resolve_and_upsert_asset

_SF_CONFIG = {
    "account": "ab12345.eu-west-1",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
}


def _user(db_session: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(u)
    db_session.flush()
    return u


def _connection(
    db_session: Any,
    *,
    type_: str = "snowflake",
    env: str = "dev",
    config: dict[str, Any] | None = None,
) -> Connection:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type=type_,
        env=env,
        config=_SF_CONFIG if config is None else config,
        secret_ref="kv-x",
        created_by=_user(db_session).id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def test_upsert_creates_row_and_returns_id(db_session: Any) -> None:
    conn = _connection(db_session)
    asset_id = resolve_and_upsert_asset(db_session, conn, {"table": "orders", "schema": "sales"})

    assert asset_id is not None
    asset = db_session.get(Asset, asset_id)
    assert asset is not None
    assert asset.namespace == "snowflake://ab12345.eu-west-1"  # hyphen → OL passthrough
    assert asset.name == "ANALYTICS.SALES.ORDERS"  # db config + target schema + table, uppercased
    assert asset.env == "dev"
    assert asset.connection_id == conn.id


def test_second_call_same_identity_reuses_row_and_bumps_last_seen(db_session: Any) -> None:
    conn = _connection(db_session)
    target = {"table": "orders", "schema": "sales"}

    first = resolve_and_upsert_asset(db_session, conn, target)
    assert first is not None

    # Backdate last_seen so the upsert's `SET last_seen = now()` is observable.
    # (Postgres now() is transaction-start time — fixed across both upserts in this
    # single-transaction test — so a same-now comparison can't show the bump; a far
    # past baseline can.)
    stale = datetime(2000, 1, 1, tzinfo=UTC)
    db_session.execute(update(Asset).where(Asset.id == first).values(last_seen=stale))

    second = resolve_and_upsert_asset(db_session, conn, target)

    assert second == first  # same (namespace, name) → same row, not a duplicate
    assert db_session.scalar(select(func.count()).select_from(Asset)) == 1
    assert db_session.get(Asset, first).last_seen > stale  # SET last_seen=now() ran


def test_different_env_or_connection_same_identity_reuses_row(db_session: Any) -> None:
    """Identity is (namespace, name) only — env/connection are provenance, not
    identity. Two connections resolving the same identity share one asset row,
    and the second upsert overwrites env/connection_id (last-writer provenance)."""
    conn_a = _connection(db_session, env="dev")
    # A different connection (different env) but the SAME account/db → same identity.
    conn_b = _connection(db_session, env="qa")
    target = {"table": "orders", "schema": "sales"}

    a = resolve_and_upsert_asset(db_session, conn_a, target)
    b = resolve_and_upsert_asset(db_session, conn_b, target)

    assert a == b
    assert db_session.scalar(select(func.count()).select_from(Asset)) == 1
    refreshed = db_session.get(Asset, a)
    assert refreshed.env == "qa"  # last upsert wins for the provenance columns
    assert refreshed.connection_id == conn_b.id


def test_distinct_snowflake_accounts_are_distinct_assets(db_session: Any) -> None:
    """DEV vs QA on different accounts are two assets by design (ADR 0034) —
    grouping across envs is a UI concern over `env`, never an identity merge."""
    dev = _connection(db_session, env="dev", config={**_SF_CONFIG, "account": "dev00001"})
    qa = _connection(db_session, env="qa", config={**_SF_CONFIG, "account": "qa00001"})
    target = {"table": "orders", "schema": "sales"}

    a = resolve_and_upsert_asset(db_session, dev, target)
    b = resolve_and_upsert_asset(db_session, qa, target)

    assert a != b
    assert db_session.scalar(select(func.count()).select_from(Asset)) == 2


def test_targetless_returns_none_no_row(db_session: Any) -> None:
    conn = _connection(db_session)
    assert resolve_and_upsert_asset(db_session, conn, None) is None
    assert resolve_and_upsert_asset(db_session, conn, {}) is None
    assert db_session.scalar(select(func.count()).select_from(Asset)) == 0


def test_unresolvable_orchestration_connection_returns_none_no_raise(db_session: Any) -> None:
    """An orchestration-type connection has no asset identity — fail-soft to None."""
    conn = _connection(db_session, type_="adf", config={})
    assert resolve_and_upsert_asset(db_session, conn, {"table": "orders"}) is None
    assert db_session.scalar(select(func.count()).select_from(Asset)) == 0


def test_upsert_db_error_returns_none_no_raise(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DB hiccup during the upsert (identity resolved fine) is still fail-soft:
    the save must not 500 on it."""
    conn = _connection(db_session)  # resolvable identity
    # Touch the attributes the resolver reads so they're loaded before we break
    # execute (post-commit they're expired; a lazy refresh would also hit _boom).
    _ = (conn.type, conn.config, conn.id, conn.env)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(db_session, "execute", _boom)
    assert (
        resolve_and_upsert_asset(db_session, conn, {"table": "orders", "schema": "sales"}) is None
    )


def test_garbage_config_returns_none_no_raise(db_session: Any) -> None:
    """A datasource connection whose config is missing the keys the resolver needs
    (legacy/half-configured) fails soft rather than blocking the caller."""
    conn = _connection(db_session, type_="snowflake", config={"account": "ab12345.eu-west-1"})
    # No database/schema in config → resolution raises ValueError internally → None.
    assert resolve_and_upsert_asset(db_session, conn, {"table": "orders"}) is None
    assert db_session.scalar(select(func.count()).select_from(Asset)) == 0
