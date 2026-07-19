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
from backend.app.db.models import Asset, Connection, ConnectionVersion, Suite, User
from backend.app.services import connection_service as svc
from backend.app.services import suite_service
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

_AIRFLOW_CONFIG = {"base_url": "https://airflow.example.com", "auth_type": "token"}

_ADLS_CONFIG = {"account_url": "https://acct.blob.core.windows.net", "container": "data"}

_S3_CONFIG = {"bucket": "dataq-lake", "region": "eu-west-1", "access_key_id": "AKIAEXAMPLE"}

_UC_CONFIG = {
    "workspace_url": "https://adb-1234.5.azuredatabricks.net",
    "warehouse_id": "abc123def456",
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

    def delete(self, name: str) -> None:
        self.data.pop(name, None)


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


def test_update_config_reresolves_bound_suite_assets(db_session: Any) -> None:
    """A config change that moves the OpenLineage identity re-points every targeted
    suite on the connection at the new asset (ADR 0034) — never a stale asset_id."""
    conn = _create(db_session, FakeStore())  # _SF_CONFIG: database=ANALYTICS
    suite = suite_service.create_suite(
        db_session,
        name="orders-suite",
        description=None,
        connection_id=conn.id,
        created_by=_user(db_session).id,
        target={"table": "orders", "schema": "sales"},
    )
    assert suite.asset_id is not None
    assert db_session.get(Asset, suite.asset_id).name == "ANALYTICS.SALES.ORDERS"

    svc.update_connection(
        db_session,
        conn.id,
        config={**_SF_CONFIG, "database": "WAREHOUSE"},
        secret_store=FakeStore(),
    )

    db_session.expire_all()
    refreshed = db_session.get(Suite, suite.id)
    assert refreshed.asset_id is not None
    assert db_session.get(Asset, refreshed.asset_id).name == "WAREHOUSE.SALES.ORDERS"


def test_update_rotates_secret(db_session: Any) -> None:
    store = FakeStore()
    conn = _create(db_session, store)
    svc.update_connection(db_session, conn.id, secret="rotated", secret_store=store)
    assert conn.secret_ref is not None
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


def test_delete_removes_row_and_secret(db_session: Any) -> None:
    store = FakeStore()
    conn = _create(db_session, store)
    ref = conn.secret_ref
    assert ref in store.data  # credential was written through on create
    svc.delete_connection(db_session, conn.id, secret_store=store)
    with pytest.raises(ConnectionNotFoundError):
        svc.get_connection(db_session, conn.id)
    assert ref not in store.data  # #372: orphaned credential removed on delete


def test_delete_unknown_raises_not_found(db_session: Any) -> None:
    with pytest.raises(ConnectionNotFoundError):
        svc.delete_connection(db_session, uuid.uuid4(), secret_store=FakeStore())


def test_delete_with_dependent_suites_raises_409_not_500(db_session: Any) -> None:
    """#753: a connection still referenced by suites must 409 with the dependents
    named (bounded sample + true total), never surface the raw FK violation."""
    from backend.app.services import suite_service

    store = FakeStore()
    conn = _create(db_session, store)
    owner = _user(db_session)
    suite = suite_service.create_suite(
        db_session,
        name="depends-on-conn",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target=None,
    )
    db_session.commit()

    with pytest.raises(svc.ConnectionInUseError) as exc:
        svc.delete_connection(db_session, conn.id, secret_store=store)
    detail = exc.value.detail
    assert detail["total"] == 1
    assert detail["truncated"] is False
    assert detail["suites"] == [{"name": "depends-on-conn", "id": str(suite.id)}]
    # The connection survives, credential untouched.
    assert svc.get_connection(db_session, conn.id).id == conn.id
    assert conn.secret_ref in store.data

    # Removing the dependent unblocks the delete.
    suite_service.delete_suite(db_session, suite.id)
    svc.delete_connection(db_session, conn.id, secret_store=store)
    with pytest.raises(ConnectionNotFoundError):
        svc.get_connection(db_session, conn.id)


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


def test_second_airflow_same_env_raises_conflict(db_session: Any) -> None:
    # The orchestrator singleton guard covers airflow too (partial index predicate
    # is `type IN ('adf','airflow')`), so the second provider type is guarded
    # without any new code.
    store = FakeStore()
    user = _user(db_session)
    kwargs = {
        "conn_type": "airflow",
        "env": "dev",
        "config": dict(_AIRFLOW_CONFIG),
        "secret": "tok",
    }
    _create(db_session, store, user=user, name="airflow-a", **kwargs)
    with pytest.raises(ConnectionConflictError, match="orchestration connection of type 'airflow'"):
        _create(db_session, store, user=user, name="airflow-b", **kwargs)


def test_adf_and_airflow_coexist_in_same_env(db_session: Any) -> None:
    # The guard is per-(type, env): one ADF *and* one Airflow in the same env is
    # fine — they're distinct provider types.
    store = FakeStore()
    user = _user(db_session)
    _create_adf(db_session, store, user=user, name="adf", env="dev")
    airflow = _create(
        db_session,
        store,
        user=user,
        name="airflow",
        conn_type="airflow",
        env="dev",
        config=dict(_AIRFLOW_CONFIG),
        secret="tok",
    )
    assert airflow.type == "airflow"


# ──────────────── other datasource types (registry wiring) ──────────


def test_create_adls_connection_validates_and_persists(db_session: Any) -> None:
    # Exercises the adls_gen2 registry entry + AdlsConfig validation through the
    # generic create path (no datasource-type branching in the service).
    store = FakeStore()
    user = _user(db_session)
    conn = _create(
        db_session,
        store,
        user=user,
        name="lake-dev",
        conn_type="adls_gen2",
        env="dev",
        config=dict(_ADLS_CONFIG),
        secret="sv=sas-token",
    )
    assert conn.type == "adls_gen2"
    assert conn.config["container"] == "data"
    # datasources are NOT orchestrators: many per env is fine (no singleton guard)
    second = _create(
        db_session,
        store,
        user=user,
        name="lake-dev-2",
        conn_type="adls_gen2",
        env="dev",
        config=dict(_ADLS_CONFIG),
        secret="sv=sas-token",
    )
    assert second.type == "adls_gen2"


def test_create_s3_connection_validates_and_persists(db_session: Any) -> None:
    # Exercises the s3 registry entry + S3Config validation through the generic
    # create path.
    store = FakeStore()
    conn = _create(
        db_session,
        store,
        name="bucket-dev",
        conn_type="s3",
        env="dev",
        config=dict(_S3_CONFIG),
        secret="sekret-access-key",
    )
    assert conn.type == "s3"
    assert conn.config["bucket"] == "dataq-lake"


def test_create_unity_catalog_connection_validates_and_persists(db_session: Any) -> None:
    # Exercises the unity_catalog registry entry + UnityCatalogConfig validation
    # through the generic create path.
    store = FakeStore()
    conn = _create(
        db_session,
        store,
        name="uc-dev",
        conn_type="unity_catalog",
        env="dev",
        config=dict(_UC_CONFIG),
        secret="dapi-pat-token",
    )
    assert conn.type == "unity_catalog"
    assert conn.config["warehouse_id"] == "abc123def456"


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

    def delete(self, name: str) -> None:
        pass


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


# ───────────────────────── version history ─────────────────────────


def _versions(db_session: Any, conn_id: uuid.UUID) -> list[ConnectionVersion]:
    return list(
        db_session.scalars(
            select(ConnectionVersion)
            .where(ConnectionVersion.connection_id == conn_id)
            .order_by(ConnectionVersion.version_no)
        )
    )


def test_create_records_v1_snapshot(db_session: Any) -> None:
    user = _user(db_session)
    conn = _create(db_session, FakeStore(), user=user)
    versions = _versions(db_session, conn.id)
    assert len(versions) == 1
    v1 = versions[0]
    assert v1.version_no == 1
    assert v1.name == conn.name
    assert v1.type == conn.type
    assert v1.env == conn.env
    assert v1.config == conn.config
    assert v1.changed_by == user.id


def test_snapshot_omits_credential(db_session: Any) -> None:
    """The secret must never be copied into history — only non-secret config."""
    conn = _create(db_session, FakeStore(), secret="super-secret")
    v1 = _versions(db_session, conn.id)[0]
    # the snapshot has no secret column at all; the live value never leaks into it
    assert "super-secret" not in str(v1.config)
    assert not hasattr(v1, "secret_ref")


def test_update_name_or_config_records_new_version(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _create(db_session, FakeStore(), user=actor)
    svc.update_connection(
        db_session,
        conn.id,
        name="renamed",
        config={**_SF_CONFIG, "warehouse": "WH_BIG"},
        secret_store=FakeStore(),
        actor_id=actor.id,
    )
    versions = _versions(db_session, conn.id)
    assert [v.version_no for v in versions] == [1, 2]
    assert versions[1].name == "renamed"
    assert versions[1].config["warehouse"] == "WH_BIG"
    assert versions[1].changed_by == actor.id


def test_secret_only_update_records_no_version(db_session: Any) -> None:
    """Credential rotation is not config history — no new snapshot (mirrors reauth)."""
    conn = _create(db_session, FakeStore())
    store = FakeStore()
    store.set(f"conn-{conn.id}", "old")
    svc.update_connection(db_session, conn.id, secret="rotated", secret_store=store)
    assert [v.version_no for v in _versions(db_session, conn.id)] == [1]  # still just the create


def test_noop_update_records_no_version(db_session: Any) -> None:
    """A PATCH that re-sends the current name/config (no net change) must not mint
    a duplicate version — `is_modified` reports no change."""
    conn = _create(db_session, FakeStore())
    svc.update_connection(
        db_session,
        conn.id,
        name=conn.name,  # unchanged
        config=dict(conn.config),  # equal value
        secret_store=FakeStore(),
    )
    assert [v.version_no for v in _versions(db_session, conn.id)] == [1]


def test_create_without_secret_still_snapshots_v1(db_session: Any) -> None:
    """The credential-less create path still records v1 (conn.id is flushed before
    the snapshot regardless of whether a secret is written)."""
    conn = _create(db_session, FakeStore(), secret=None)
    versions = _versions(db_session, conn.id)
    assert [v.version_no for v in versions] == [1]
    assert versions[0].connection_id == conn.id


def test_list_connection_versions_newest_first_with_author(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _create(db_session, FakeStore(), user=actor)
    svc.update_connection(
        db_session, conn.id, name="v2", secret_store=FakeStore(), actor_id=actor.id
    )
    versions = svc.list_connection_versions(db_session, conn.id)
    assert [v.version_no for v in versions] == [2, 1]  # newest first
    assert versions[0].changed_by_name == actor.email  # eager-loaded author


def test_list_connection_versions_unknown_connection_404(db_session: Any) -> None:
    with pytest.raises(ConnectionNotFoundError):
        svc.list_connection_versions(db_session, uuid.uuid4())


def test_delete_connection_cascades_versions(db_session: Any) -> None:
    """Cascade delete is accepted policy — history is not retained past deletion."""
    conn = _create(db_session, FakeStore())
    assert len(_versions(db_session, conn.id)) == 1
    svc.delete_connection(db_session, conn.id, secret_store=FakeStore())
    assert _versions(db_session, conn.id) == []
