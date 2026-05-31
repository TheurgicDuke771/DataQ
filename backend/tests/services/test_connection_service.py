"""Connection service tests against a real Postgres (db_session).

CRUD + secret write-through use a fake in-memory SecretStore; the connectivity
test monkeypatches the adapter so no live warehouse is needed. Skips without
TEST_DATABASE_URL (CI provides an ephemeral Postgres).
"""

import uuid
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from backend.app.core.secrets import SecretNotFoundError, SecretWriteError
from backend.app.db.models import Connection, User
from backend.app.services import connection_service as svc
from backend.app.services.connection_service import (
    ConnectionConfigInvalidError,
    ConnectionConflictError,
    ConnectionNotFoundError,
    ConnectionSecretWriteError,
    ConnectionTestFailedError,
)

_SF_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}

_ADF_CONFIG = {
    "subscription_id": "00000000-0000-0000-0000-000000000001",
    "resource_group": "rg-data",
    "factory_name": "example-adf-preprod",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "client_id": "00000000-0000-0000-0000-0000000000bb",
}


class FakeStore:
    """In-memory SecretStore for write-through assertions."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value


class _PassAdapter:
    def validate_config(self, raw: dict[str, Any]) -> BaseModel:
        return BaseModel()

    def test(self, raw: dict[str, Any], secret: str) -> None:
        return None


class _FailAdapter(_PassAdapter):
    def test(self, raw: dict[str, Any], secret: str) -> None:
        raise RuntimeError("warehouse unreachable")


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def _create(db_session: Any, store: FakeStore, **overrides: Any) -> Connection:
    user = overrides.pop("user", None) or _user(db_session)
    kwargs: dict[str, Any] = {
        "name": "finance-dev",
        "conn_type": "snowflake",
        "env": "dev",
        "config": dict(_SF_CONFIG),
        "secret": "p@ss",
        "created_by": user.id,
        "secret_store": store,
    }
    kwargs.update(overrides)
    return svc.create_connection(db_session, **kwargs)


# ───────────────────────── create ──────────────────────────────────


def test_create_persists_row_and_writes_secret(db_session: Any) -> None:
    store = FakeStore()
    conn = _create(db_session, store)

    assert conn.id is not None
    assert conn.type == "snowflake"
    assert conn.config["account"] == "ab12345.eu-west-1"
    # secret written under conn-<id>, only the ref is on the row
    assert conn.secret_ref == f"conn-{conn.id}"
    assert store.data[conn.secret_ref] == "p@ss"


def test_create_without_secret_leaves_secret_ref_null(db_session: Any) -> None:
    store = FakeStore()
    conn = _create(db_session, store, secret=None)
    assert conn.secret_ref is None
    assert store.data == {}


def test_create_unknown_type_raises_config_invalid(db_session: Any) -> None:
    with pytest.raises(ConnectionConfigInvalidError):
        _create(db_session, FakeStore(), conn_type="mssql")


def test_create_invalid_config_raises_config_invalid(db_session: Any) -> None:
    bad = {k: v for k, v in _SF_CONFIG.items() if k != "account"}
    with pytest.raises(ConnectionConfigInvalidError):
        _create(db_session, FakeStore(), config=bad)


def test_create_invalid_env_raises_config_invalid(db_session: Any) -> None:
    with pytest.raises(ConnectionConfigInvalidError, match="invalid env"):
        _create(db_session, FakeStore(), env="staging")


def test_create_duplicate_name_env_raises_conflict(db_session: Any) -> None:
    store = FakeStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="dup", env="dev")
    with pytest.raises(ConnectionConflictError):
        _create(db_session, store, user=user, name="dup", env="dev")


def test_create_same_name_different_env_is_allowed(db_session: Any) -> None:
    store = FakeStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="shared", env="dev")
    other = _create(db_session, store, user=user, name="shared", env="qa")
    assert other.env == "qa"


# ───────────────────────── read / list ─────────────────────────────


def test_get_returns_connection(db_session: Any) -> None:
    conn = _create(db_session, FakeStore())
    assert svc.get_connection(db_session, conn.id).id == conn.id


def test_get_unknown_raises_not_found(db_session: Any) -> None:
    with pytest.raises(ConnectionNotFoundError):
        svc.get_connection(db_session, uuid.uuid4())


def test_list_filters_by_type_and_env(db_session: Any) -> None:
    store = FakeStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="sf-dev", env="dev")
    _create(db_session, store, user=user, name="sf-qa", env="qa")

    assert {c.name for c in svc.list_connections(db_session, conn_type="snowflake")} == {
        "sf-dev",
        "sf-qa",
    }
    assert [c.name for c in svc.list_connections(db_session, env="qa")] == ["sf-qa"]


# ───────────────────────── update ──────────────────────────────────


def test_update_changes_name_and_config(db_session: Any) -> None:
    conn = _create(db_session, FakeStore())
    updated = svc.update_connection(
        db_session,
        conn.id,
        name="renamed",
        config={**_SF_CONFIG, "warehouse": "WH_BIG"},
        secret_store=FakeStore(),
    )
    assert updated.name == "renamed"
    assert updated.config["warehouse"] == "WH_BIG"


def test_update_rotates_secret(db_session: Any) -> None:
    store = FakeStore()
    conn = _create(db_session, store)
    svc.update_connection(db_session, conn.id, secret="rotated", secret_store=store)
    assert store.data[conn.secret_ref] == "rotated"


def test_update_invalid_config_raises(db_session: Any) -> None:
    conn = _create(db_session, FakeStore())
    with pytest.raises(ConnectionConfigInvalidError):
        svc.update_connection(
            db_session, conn.id, config={"account": "only"}, secret_store=FakeStore()
        )


def test_update_name_collision_raises_conflict(db_session: Any) -> None:
    store = FakeStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="taken", env="dev")
    other = _create(db_session, store, user=user, name="free", env="dev")
    with pytest.raises(ConnectionConflictError):
        svc.update_connection(db_session, other.id, name="taken", secret_store=store)


# ───────────────────────── delete ──────────────────────────────────


def test_delete_removes_row(db_session: Any) -> None:
    conn = _create(db_session, FakeStore())
    svc.delete_connection(db_session, conn.id)
    with pytest.raises(ConnectionNotFoundError):
        svc.get_connection(db_session, conn.id)


def test_delete_unknown_raises_not_found(db_session: Any) -> None:
    with pytest.raises(ConnectionNotFoundError):
        svc.delete_connection(db_session, uuid.uuid4())


# ───────────────────────── test connectivity ───────────────────────


def test_test_connection_passes(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore()
    conn = _create(db_session, store)
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    svc.test_connection(db_session, conn.id, secret_store=store)  # no raise


def test_test_connection_adapter_failure_raises(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeStore()
    conn = _create(db_session, store)
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    with pytest.raises(ConnectionTestFailedError) as excinfo:
        svc.test_connection(db_session, conn.id, secret_store=store)
    # client message must NOT echo the adapter exception (DSN/secret leak guard);
    # the original is preserved only as __cause__ for server-side tracebacks.
    assert "warehouse unreachable" not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_test_connection_without_secret_raises(db_session: Any) -> None:
    conn = _create(db_session, FakeStore(), secret=None)
    with pytest.raises(ConnectionTestFailedError, match="no stored credential"):
        svc.test_connection(db_session, conn.id, secret_store=FakeStore())


def test_test_connection_missing_secret_in_store_raises(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _create(db_session, FakeStore())  # secret written to a different store
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    with pytest.raises(ConnectionTestFailedError, match="could not be resolved"):
        svc.test_connection(db_session, conn.id, secret_store=FakeStore())


# ──────────────── orchestrator (type, env) singleton guard (#72) ────────────


def _create_adf(db_session: Any, store: FakeStore, **overrides: Any) -> Connection:
    kwargs: dict[str, Any] = {
        "name": "adf-conn",
        "conn_type": "adf",
        "env": "dev",
        "config": dict(_ADF_CONFIG),
        "secret": "sp-secret",
    }
    kwargs.update(overrides)
    return _create(db_session, store, **kwargs)


def test_second_adf_same_env_raises_conflict(db_session: Any) -> None:
    store = FakeStore()
    user = _user(db_session)
    _create_adf(db_session, store, user=user, name="adf-a", env="dev")
    # different name, same (type, env) → the partial unique index must fire,
    # not the (name, env) constraint.
    with pytest.raises(ConnectionConflictError, match="orchestration connection of type 'adf'"):
        _create_adf(db_session, store, user=user, name="adf-b", env="dev")


def test_adf_in_different_env_is_allowed(db_session: Any) -> None:
    store = FakeStore()
    user = _user(db_session)
    _create_adf(db_session, store, user=user, name="adf-dev", env="dev")
    other = _create_adf(db_session, store, user=user, name="adf-qa", env="qa")
    assert other.env == "qa"


def test_two_snowflakes_same_env_not_blocked_by_orchestrator_index(db_session: Any) -> None:
    # Datasources are excluded from the partial index: many Snowflake
    # connections per env are legitimate (distinct databases).
    store = FakeStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="sf-one", env="dev")
    second = _create(db_session, store, user=user, name="sf-two", env="dev")
    assert second.type == "snowflake"


# ──────────── secret-store write failure → 502 (not 500) (#87) ───────────────


class _WriteFailStore(FakeStore):
    """SecretStore whose set() fails — simulates Key Vault unreachable."""

    def set(self, name: str, value: str) -> None:
        raise SecretWriteError("key vault unreachable")


def test_create_secret_write_failure_raises_502_and_rolls_back(db_session: Any) -> None:
    with pytest.raises(ConnectionSecretWriteError) as excinfo:
        _create(db_session, _WriteFailStore())
    assert excinfo.value.status_code == 502
    assert isinstance(excinfo.value.__cause__, SecretWriteError)
    # the half-inserted row must be rolled back, not left dangling
    assert db_session.scalars(select(Connection)).all() == []


def test_update_secret_write_failure_raises_502(db_session: Any) -> None:
    conn = _create(db_session, FakeStore())  # created fine with a working store
    with pytest.raises(ConnectionSecretWriteError) as excinfo:
        svc.update_connection(db_session, conn.id, secret="rotated", secret_store=_WriteFailStore())
    assert excinfo.value.status_code == 502
    assert isinstance(excinfo.value.__cause__, SecretWriteError)
