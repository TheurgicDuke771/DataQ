"""Connection service tests against a real Postgres (db_session).

CRUD + secret write-through use a fake in-memory SecretStore; the connectivity
test monkeypatches the adapter so no live warehouse is needed. Skips without
TEST_DATABASE_URL (CI provides an ephemeral Postgres).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from backend.app.core.secret_names import connection_secret_ref
from backend.app.core.secrets import SecretWriteError
from backend.app.db.models import Asset, Connection, ConnectionVersion, Run, Suite, User
from backend.app.services import connection_service as svc
from backend.app.services import suite_service
from backend.app.services.connection_service import (
    ConnectionConfigInvalidError,
    ConnectionConflictError,
    ConnectionNotFoundError,
    ConnectionSecretWriteError,
    ConnectionTestFailedError,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

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


class _PassAdapter:
    def validate_config(self, raw: dict[str, Any]) -> Any:
        # `_validated_config` only checks that this doesn't raise — the return
        # value is unused, and `BaseModel()` (the shape this once returned) is
        # a Pydantic v2 error to instantiate directly. That was dead code until
        # `test_draft_connection` (#351) became the first path in this file to
        # actually call `_validated_config` with a monkeypatched adapter.
        return None

    def test(self, raw: dict[str, Any], secret: str) -> None:
        return None


class _FailAdapter(_PassAdapter):
    def test(self, raw: dict[str, Any], secret: str) -> None:
        raise RuntimeError("warehouse unreachable")


class _OptionalSecretAdapter(_PassAdapter):
    """A `secret_optional=True` stand-in (the Iceberg/dbt shape, #351) — proves
    `test_connection`/`test_draft_connection` hand a credential-less adapter a
    real `None`, never a placeholder, and never 502 for the missing secret.
    """

    secret_optional = True

    def __init__(self) -> None:
        self.received_secret: str | None | object = "UNSET"

    def test(self, raw: dict[str, Any], secret: str | None) -> None:
        self.received_secret = secret


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"dev-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def _create(db_session: Any, store: FakeSecretStore, **overrides: Any) -> Connection:
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
    store = FakeSecretStore()
    conn = _create(db_session, store)

    assert conn.id is not None
    assert conn.type == "snowflake"
    assert conn.config["account"] == "ab12345.eu-west-1"
    # The ref is READABLE (ADR 0039 / #1060) — an operator has to find this entry
    # in the vault by eye to rotate it. Asserted via the generator rather than a
    # literal so a format tweak doesn't break unrelated tests.
    assert conn.secret_ref == connection_secret_ref(
        connection_id=conn.id, env=conn.env, name=conn.name, conn_type=conn.type
    )
    assert "finance" in conn.secret_ref  # the human-meaningful part actually survives
    assert store.data[conn.secret_ref] == "p@ss"


def test_create_without_secret_leaves_secret_ref_null(db_session: Any) -> None:
    store = FakeSecretStore()
    conn = _create(db_session, store, secret=None)
    assert conn.secret_ref is None
    assert store.data == {}


def test_create_unknown_type_raises_config_invalid(db_session: Any) -> None:
    with pytest.raises(ConnectionConfigInvalidError):
        _create(db_session, FakeSecretStore(), conn_type="mssql")


def test_create_invalid_config_raises_config_invalid(db_session: Any) -> None:
    bad = {k: v for k, v in _SF_CONFIG.items() if k != "account"}
    with pytest.raises(ConnectionConfigInvalidError):
        _create(db_session, FakeSecretStore(), config=bad)


def test_create_invalid_env_raises_config_invalid(db_session: Any) -> None:
    with pytest.raises(ConnectionConfigInvalidError, match="invalid env"):
        _create(db_session, FakeSecretStore(), env="staging")


def test_create_duplicate_name_env_raises_conflict(db_session: Any) -> None:
    store = FakeSecretStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="dup", env="dev")
    with pytest.raises(ConnectionConflictError):
        _create(db_session, store, user=user, name="dup", env="dev")


def test_create_same_name_different_env_is_allowed(db_session: Any) -> None:
    store = FakeSecretStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="shared", env="dev")
    other = _create(db_session, store, user=user, name="shared", env="qa")
    assert other.env == "qa"


# ───────────────────────── read / list ─────────────────────────────


def test_get_returns_connection(db_session: Any) -> None:
    conn = _create(db_session, FakeSecretStore())
    assert svc.get_connection(db_session, conn.id).id == conn.id


def test_get_unknown_raises_not_found(db_session: Any) -> None:
    with pytest.raises(ConnectionNotFoundError):
        svc.get_connection(db_session, uuid.uuid4())


def test_list_filters_by_type_and_env(db_session: Any) -> None:
    store = FakeSecretStore()
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
    conn = _create(db_session, FakeSecretStore())
    updated = svc.update_connection(
        db_session,
        conn.id,
        name="renamed",
        config={**_SF_CONFIG, "warehouse": "WH_BIG"},
        secret_store=FakeSecretStore(),
    )
    assert updated.name == "renamed"
    assert updated.config["warehouse"] == "WH_BIG"


def test_turning_inventory_sync_off_clears_its_outcome_state(db_session: Any) -> None:
    """#1104: the three `inventory_sync_*` columns describe the last sync ATTEMPT, so
    when the toggle goes off they must go blank — a failing state must never outlive
    its cause. Cleared here rather than only in the daily sweep because the sweep
    cannot see the window: toggled off at 09:00 and back on at 09:05 never presents an
    opted-out row to the next tick, and every reader would meanwhile be told the
    connection is "failing since <old date>" for a sync nobody has attempted since."""
    from datetime import UTC, datetime

    conn = _create(db_session, FakeSecretStore(), config={**_SF_CONFIG, "inventory_sync": True})
    conn.inventory_sync_last_attempted_at = datetime.now(UTC)
    conn.inventory_sync_last_error = "Inventory sync was rejected."
    conn.inventory_sync_failing_since = datetime.now(UTC)
    db_session.flush()

    updated = svc.update_connection(
        db_session, conn.id, config=dict(_SF_CONFIG), secret_store=FakeSecretStore()
    )

    assert updated.config.get("inventory_sync") is None
    assert updated.inventory_sync_last_attempted_at is None
    assert updated.inventory_sync_last_error is None
    assert updated.inventory_sync_failing_since is None


def test_an_unrelated_config_edit_keeps_the_inventory_sync_state(db_session: Any) -> None:
    """The clear is bound to the toggle going OFF, not to "config changed" — wiping
    the state on any edit would hide a live, still-failing sync."""
    from datetime import UTC, datetime

    conn = _create(db_session, FakeSecretStore(), config={**_SF_CONFIG, "inventory_sync": True})
    conn.inventory_sync_failing_since = datetime.now(UTC)
    conn.inventory_sync_last_error = "Inventory sync was rejected."
    db_session.flush()

    updated = svc.update_connection(
        db_session,
        conn.id,
        config={**_SF_CONFIG, "inventory_sync": True, "warehouse": "WH_OTHER"},
        secret_store=FakeSecretStore(),
    )

    assert updated.inventory_sync_failing_since is not None
    assert updated.inventory_sync_last_error is not None


def test_update_config_reresolves_bound_suite_assets(db_session: Any) -> None:
    """A config change that moves the OpenLineage identity re-points every targeted
    suite on the connection at the new asset (ADR 0034) — never a stale asset_id."""
    conn = _create(db_session, FakeSecretStore())  # _SF_CONFIG: database=ANALYTICS
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
        secret_store=FakeSecretStore(),
    )

    db_session.expire_all()
    refreshed = db_session.get(Suite, suite.id)
    assert refreshed.asset_id is not None
    assert db_session.get(Asset, refreshed.asset_id).name == "WAREHOUSE.SALES.ORDERS"


def test_update_rotates_secret(db_session: Any) -> None:
    store = FakeSecretStore()
    conn = _create(db_session, store)
    svc.update_connection(db_session, conn.id, secret="rotated", secret_store=store)
    assert conn.secret_ref is not None
    assert store.data[conn.secret_ref] == "rotated"


def test_update_invalid_config_raises(db_session: Any) -> None:
    conn = _create(db_session, FakeSecretStore())
    with pytest.raises(ConnectionConfigInvalidError):
        svc.update_connection(
            db_session, conn.id, config={"account": "only"}, secret_store=FakeSecretStore()
        )


def test_update_name_collision_raises_conflict(db_session: Any) -> None:
    store = FakeSecretStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="taken", env="dev")
    other = _create(db_session, store, user=user, name="free", env="dev")
    with pytest.raises(ConnectionConflictError):
        svc.update_connection(db_session, other.id, name="taken", secret_store=store)


# ───────────────────────── delete ──────────────────────────────────


def test_delete_removes_row_and_secret(db_session: Any) -> None:
    store = FakeSecretStore()
    conn = _create(db_session, store)
    ref = conn.secret_ref
    assert ref in store.data  # credential was written through on create
    svc.delete_connection(db_session, conn.id, secret_store=store, actor_id=conn.created_by)
    with pytest.raises(ConnectionNotFoundError):
        svc.get_connection(db_session, conn.id)
    assert ref not in store.data  # #372: orphaned credential removed on delete


def test_delete_unknown_raises_not_found(db_session: Any) -> None:
    with pytest.raises(ConnectionNotFoundError):
        svc.delete_connection(
            db_session, uuid.uuid4(), secret_store=FakeSecretStore(), actor_id=uuid.uuid4()
        )


def test_delete_with_dependent_suites_raises_409_not_500(db_session: Any) -> None:
    """#753: a connection still referenced by suites must 409 with the dependents
    named (bounded sample + true total), never surface the raw FK violation."""
    from backend.app.services import suite_service

    store = FakeSecretStore()
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
        svc.delete_connection(db_session, conn.id, secret_store=store, actor_id=owner.id)
    detail = exc.value.detail
    assert detail["total"] == 1
    assert detail["truncated"] is False
    assert detail["suites"] == [{"name": "depends-on-conn", "id": str(suite.id)}]
    # The connection survives, credential untouched.
    assert svc.get_connection(db_session, conn.id).id == conn.id
    assert conn.secret_ref in store.data

    # Removing the dependent unblocks the delete.
    suite_service.delete_suite(db_session, suite.id)
    svc.delete_connection(db_session, conn.id, secret_store=store, actor_id=owner.id)
    with pytest.raises(ConnectionNotFoundError):
        svc.get_connection(db_session, conn.id)


def test_delete_409_hides_suite_names_outside_the_actors_grants(db_session: Any) -> None:
    """#927 review: suite NAMES are grant-scoped (ADR 0027) — a caller with no
    grant on a dependent suite gets the count, never the name (the suite endpoint
    404-no-leaks it; this 409 must not defeat that one request over)."""
    store = FakeSecretStore()
    conn = _create(db_session, store)
    stranger_owner = _user(db_session)
    suite_service.create_suite(
        db_session,
        name="strangers-secret-suite",
        description=None,
        connection_id=conn.id,
        created_by=stranger_owner.id,
        target=None,
    )
    db_session.commit()

    outsider = _user(db_session)
    with pytest.raises(svc.ConnectionInUseError) as exc:
        svc.delete_connection(db_session, conn.id, secret_store=store, actor_id=outsider.id)
    detail = exc.value.detail
    assert detail["total"] == 1
    assert detail["restricted"] == 1
    assert detail["suites"] == []  # counted, never named
    assert "strangers-secret-suite" not in str(detail)

    # A workspace-admin actor sees the full sample.
    with pytest.raises(svc.ConnectionInUseError) as exc:
        svc.delete_connection(
            db_session, conn.id, secret_store=store, actor_id=outsider.id, actor_is_admin=True
        )
    detail = exc.value.detail
    assert detail["restricted"] == 0
    assert detail["suites"][0]["name"] == "strangers-secret-suite"


def test_delete_orchestration_connection_cascades_pipeline_runs(db_session: Any) -> None:
    """#927 review: pipeline_runs are observations polled THROUGH the connection —
    they cascade with it (migration a3b4c5d6e7f8) instead of 500ing the delete."""
    from backend.app.db.models import PipelineRun

    store = FakeSecretStore()
    conn = _create(
        db_session, store, name="af-dev", conn_type="airflow", config=dict(_AIRFLOW_CONFIG)
    )
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn.id,
            provider_run_id="run-1",
            pipeline_or_dag_id="dag_a",
            env="dev",
            status="succeeded",
        )
    )
    db_session.commit()

    svc.delete_connection(db_session, conn.id, secret_store=store, actor_id=conn.created_by)
    assert (
        db_session.scalar(
            select(func.count()).select_from(PipelineRun).where(PipelineRun.provider == "airflow")
        )
        == 0
    )


# ───────────────────────── test connectivity ───────────────────────


def test_test_connection_passes(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeSecretStore()
    conn = _create(db_session, store)
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    svc.test_connection(db_session, conn.id, secret_store=store)  # no raise


def test_test_connection_adapter_failure_raises(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    conn = _create(db_session, store)
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    with pytest.raises(ConnectionTestFailedError) as excinfo:
        svc.test_connection(db_session, conn.id, secret_store=store)
    # client message must NOT echo the adapter exception (DSN/secret leak guard);
    # the original is preserved only as __cause__ for server-side tracebacks.
    assert "warehouse unreachable" not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_test_connection_without_secret_raises(db_session: Any) -> None:
    conn = _create(db_session, FakeSecretStore(), secret=None)
    with pytest.raises(ConnectionTestFailedError, match="no stored credential"):
        svc.test_connection(db_session, conn.id, secret_store=FakeSecretStore())


def test_test_connection_missing_secret_in_store_raises(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _create(db_session, FakeSecretStore())  # secret written to a different store
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    with pytest.raises(ConnectionTestFailedError, match="could not be resolved"):
        svc.test_connection(db_session, conn.id, secret_store=FakeSecretStore())


# ──────── secret_optional — Iceberg/dbt credential-less configs (#351) ─────


def test_test_connection_secret_optional_no_secret_ref_succeeds(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved-path parity: a connection saved with NO credential (a legitimate
    credential-less catalog/artifacts path) must still test green when its
    adapter is `secret_optional`, not 502 'no stored credential to test with'.
    """
    store = FakeSecretStore()
    conn = _create(db_session, store, secret=None)
    assert conn.secret_ref is None
    adapter = _OptionalSecretAdapter()
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: adapter)
    svc.test_connection(db_session, conn.id, secret_store=store)  # no raise
    assert adapter.received_secret is None  # a real None, not "" or a placeholder


def test_test_connection_secret_required_adapter_unaffected(db_session: Any) -> None:
    """A `secret_optional`-unaware adapter (the default) keeps the old
    behavior — this is `test_test_connection_without_secret_raises` above,
    reasserted here as the explicit negative half of the #351 parity pair."""
    conn = _create(db_session, FakeSecretStore(), secret=None)
    with pytest.raises(ConnectionTestFailedError, match="no stored credential"):
        svc.test_connection(db_session, conn.id, secret_store=FakeSecretStore())


# ───────────────── draft connection test — unsaved probe (#351) ────────────


def test_draft_test_passes(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeSecretStore()
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    svc.test_draft_connection(
        "snowflake", env="dev", config=dict(_SF_CONFIG), secret="p@ss", secret_store=store
    )  # no raise
    # the whole point: nothing landed anywhere
    assert db_session.scalars(select(Connection)).all() == []
    assert store.data == {}


def test_draft_test_adapter_failure_raises(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    with pytest.raises(ConnectionTestFailedError) as excinfo:
        svc.test_draft_connection(
            "snowflake", env="dev", config=dict(_SF_CONFIG), secret="p@ss", secret_store=store
        )
    # same leak guard as the saved-connection path: never echo the adapter
    # exception (it can carry DSN/credential fragments) to the client message.
    assert "warehouse unreachable" not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert db_session.scalars(select(Connection)).all() == []
    assert store.data == {}


def test_draft_test_without_secret_raises(db_session: Any) -> None:
    with pytest.raises(ConnectionTestFailedError, match="credential is required"):
        svc.test_draft_connection(
            "snowflake",
            env="dev",
            config=dict(_SF_CONFIG),
            secret=None,
            secret_store=FakeSecretStore(),
        )


def test_draft_test_secret_optional_adapter_allows_missing_secret(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Iceberg/dbt (#351 `secret_optional`) — a legitimate credential-less
    draft must not 502 just because `secret` is absent, and the adapter must
    receive a real `None`, never a placeholder."""
    adapter = _OptionalSecretAdapter()
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: adapter)
    svc.test_draft_connection(
        "iceberg",
        env="dev",
        config={"catalog_type": "glue"},
        secret=None,
        secret_store=FakeSecretStore(),
    )  # no raise
    assert adapter.received_secret is None


def test_draft_test_secret_optional_adapter_normalizes_blank_string_to_none(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wire payload can hand in `secret=""` where the internal contract only
    ever sees `str | None` — both must mean "no credential" to the adapter."""
    adapter = _OptionalSecretAdapter()
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: adapter)
    svc.test_draft_connection(
        "dbt", env="dev", config={}, secret="", secret_store=FakeSecretStore()
    )  # no raise
    assert adapter.received_secret is None


def test_draft_test_unknown_type_raises_config_invalid(db_session: Any) -> None:
    with pytest.raises(ConnectionConfigInvalidError):
        svc.test_draft_connection(
            "mssql", env="dev", config={}, secret="p@ss", secret_store=FakeSecretStore()
        )


def test_draft_test_invalid_config_raises_config_invalid(db_session: Any) -> None:
    bad = {k: v for k, v in _SF_CONFIG.items() if k != "account"}
    with pytest.raises(ConnectionConfigInvalidError):
        svc.test_draft_connection(
            "snowflake", env="dev", config=bad, secret="p@ss", secret_store=FakeSecretStore()
        )


def test_draft_test_invalid_env_raises_config_invalid(db_session: Any) -> None:
    with pytest.raises(ConnectionConfigInvalidError):
        svc.test_draft_connection(
            "snowflake",
            env="staging",
            config=dict(_SF_CONFIG),
            secret="p@ss",
            secret_store=FakeSecretStore(),
        )


def test_draft_test_env_is_optional(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # env plays no role in the probe itself — omitting it must not block the test.
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    svc.test_draft_connection(
        "snowflake",
        env=None,
        config=dict(_SF_CONFIG),
        secret="p@ss",
        secret_store=FakeSecretStore(),
    )  # no raise


# ──────────────── orchestrator (type, env) singleton guard (#72) ────────────


def _create_adf(db_session: Any, store: FakeSecretStore, **overrides: Any) -> Connection:
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
    store = FakeSecretStore()
    user = _user(db_session)
    _create_adf(db_session, store, user=user, name="adf-a", env="dev")
    # different name, same (type, env) → the partial unique index must fire,
    # not the (name, env) constraint.
    with pytest.raises(ConnectionConflictError, match="orchestration connection of type 'adf'"):
        _create_adf(db_session, store, user=user, name="adf-b", env="dev")


def test_adf_in_different_env_is_allowed(db_session: Any) -> None:
    store = FakeSecretStore()
    user = _user(db_session)
    _create_adf(db_session, store, user=user, name="adf-dev", env="dev")
    other = _create_adf(db_session, store, user=user, name="adf-qa", env="qa")
    assert other.env == "qa"


def test_second_airflow_same_env_raises_conflict(db_session: Any) -> None:
    # The orchestrator singleton guard covers airflow too (partial index predicate
    # is `type IN ('adf','airflow')`), so the second provider type is guarded
    # without any new code.
    store = FakeSecretStore()
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
    store = FakeSecretStore()
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
    store = FakeSecretStore()
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
    store = FakeSecretStore()
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
    store = FakeSecretStore()
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
    store = FakeSecretStore()
    user = _user(db_session)
    _create(db_session, store, user=user, name="sf-one", env="dev")
    second = _create(db_session, store, user=user, name="sf-two", env="dev")
    assert second.type == "snowflake"


# ──────────── secret-store write failure → 502 (not 500) (#87) ───────────────


class _WriteFailStore(FakeSecretStore):
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
    conn = _create(db_session, FakeSecretStore())  # created fine with a working store
    with pytest.raises(ConnectionSecretWriteError) as excinfo:
        svc.update_connection(db_session, conn.id, secret="rotated", secret_store=_WriteFailStore())
    assert excinfo.value.status_code == 502
    assert isinstance(excinfo.value.__cause__, SecretWriteError)


# ────────── a SECOND credential — the Iceberg catalog secret (#1181) ─────────

_ICEBERG_SQL_CONFIG = {"catalog_type": "sql", "catalog_uri": "sqlite:///w"}


def test_create_iceberg_with_catalog_secret_stores_it_and_sets_config_field(
    db_session: Any,
) -> None:
    store = FakeSecretStore()
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret="storage-key",
        catalog_secret="catalog-db-pw",
        created_by=_user(db_session).id,
        secret_store=store,
    )
    ref = conn.config.get("catalog_secret_name")
    assert ref is not None
    assert conn.secret_ref is not None
    assert ref != conn.secret_ref  # a distinct ref from the storage credential
    assert store.data[ref] == "catalog-db-pw"
    assert store.data[conn.secret_ref] == "storage-key"


def test_create_iceberg_catalog_secret_alone_works_credential_less_storage(
    db_session: Any,
) -> None:
    """A credential-less catalog storage layer (Iceberg's `secret_optional`) must
    not block a catalog-only credential from being stored."""
    store = FakeSecretStore()
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret=None,
        catalog_secret="catalog-db-pw",
        created_by=_user(db_session).id,
        secret_store=store,
    )
    assert conn.secret_ref is None
    ref = conn.config["catalog_secret_name"]
    assert store.data[ref] == "catalog-db-pw"


def test_create_response_and_config_never_carry_the_catalog_secret_value(
    db_session: Any,
) -> None:
    """`config` holds only the vault KEY NAME, never the credential itself."""
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret=None,
        catalog_secret="super-secret-db-pw",
        created_by=_user(db_session).id,
        secret_store=FakeSecretStore(),
    )
    assert "super-secret-db-pw" not in str(conn.config)


def test_create_catalog_secret_unsupported_type_raises_config_invalid(
    db_session: Any,
) -> None:
    """Only a config model that declares `catalog_secret_name` (Iceberg today)
    can receive one — a Snowflake connection has nowhere to put it."""
    store = FakeSecretStore()
    with pytest.raises(ConnectionConfigInvalidError):
        _create(db_session, store, catalog_secret="should-not-write")
    # nothing persisted and nothing written — rejected before any DB/store I/O
    assert db_session.scalars(select(Connection)).all() == []
    assert store.data == {}


def test_update_rotates_catalog_secret_reusing_the_same_ref(db_session: Any) -> None:
    store = FakeSecretStore()
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret=None,
        catalog_secret="pw-v1",
        created_by=_user(db_session).id,
        secret_store=store,
    )
    ref_before = conn.config["catalog_secret_name"]

    svc.update_connection(db_session, conn.id, catalog_secret="pw-v2", secret_store=store)

    assert conn.config["catalog_secret_name"] == ref_before  # reused, not re-minted
    assert store.data[ref_before] == "pw-v2"


def test_update_mints_a_catalog_secret_that_did_not_exist_at_create(db_session: Any) -> None:
    store = FakeSecretStore()
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret=None,
        created_by=_user(db_session).id,
        secret_store=store,
    )
    assert "catalog_secret_name" not in conn.config

    svc.update_connection(db_session, conn.id, catalog_secret="pw-first-time", secret_store=store)

    ref = conn.config["catalog_secret_name"]
    assert store.data[ref] == "pw-first-time"


def test_update_catalog_secret_unsupported_type_raises_config_invalid(db_session: Any) -> None:
    conn = _create(db_session, FakeSecretStore())  # snowflake
    with pytest.raises(ConnectionConfigInvalidError):
        svc.update_connection(
            db_session, conn.id, catalog_secret="nope", secret_store=FakeSecretStore()
        )


def test_update_catalog_secret_write_failure_raises_502(db_session: Any) -> None:
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret=None,
        created_by=_user(db_session).id,
        secret_store=FakeSecretStore(),
    )
    with pytest.raises(ConnectionSecretWriteError) as excinfo:
        svc.update_connection(
            db_session, conn.id, catalog_secret="pw", secret_store=_WriteFailStore()
        )
    assert excinfo.value.status_code == 502
    assert isinstance(excinfo.value.__cause__, SecretWriteError)


def test_create_catalog_secret_write_failure_rolls_back(db_session: Any) -> None:
    """The main secret writes fine; the catalog secret fails — the whole create
    must roll back, not leave a half-written row + orphaned storage secret."""

    class _CatalogFailsStore(FakeSecretStore):
        def set(self, name: str, value: str) -> None:
            if "catalog" in name:
                raise SecretWriteError("key vault unreachable")
            super().set(name, value)

    store = _CatalogFailsStore()
    with pytest.raises(ConnectionSecretWriteError):
        svc.create_connection(
            db_session,
            name="harness-iceberg",
            conn_type="iceberg",
            env="dev",
            config=dict(_ICEBERG_SQL_CONFIG),
            secret="storage-key",
            catalog_secret="pw",
            created_by=_user(db_session).id,
            secret_store=store,
        )
    assert db_session.scalars(select(Connection)).all() == []


def test_update_writes_catalog_secret_before_the_primary_secret(db_session: Any) -> None:
    """On a two-secret PATCH, the catalog write must happen BEFORE the primary
    rotation: neither store write is part of the DB transaction, so if the
    CATALOG write fails after the primary already succeeded, the connection
    would be silently running on an unverified new primary credential the
    caller was told 502'd (no rollback can undo an already-live vault write).
    Ordering catalog-first means a catalog failure leaves the primary
    untouched — the worse corruption is structurally impossible."""
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret="storage-key-v1",
        created_by=_user(db_session).id,
        secret_store=FakeSecretStore(),
    )
    assert conn.secret_ref is not None

    class _CatalogFailsStore(FakeSecretStore):
        def set(self, name: str, value: str) -> None:
            if "catalog" in name:
                raise SecretWriteError("key vault unreachable")
            super().set(name, value)

    fail_store = _CatalogFailsStore()
    fail_store.data[conn.secret_ref] = "storage-key-v1"
    with pytest.raises(ConnectionSecretWriteError):
        svc.update_connection(
            db_session,
            conn.id,
            secret="storage-key-v2",
            catalog_secret="pw",
            secret_store=fail_store,
        )
    # The primary credential must be UNTOUCHED — still the original value, not
    # the submitted-but-unverified rotation.
    assert fail_store.data[conn.secret_ref] == "storage-key-v1"


# ────────── config-only PATCH must not orphan the catalog secret (#1181 review) ──


def test_config_only_update_preserves_catalog_secret_name(db_session: Any) -> None:
    """`update_connection`'s `config` param wholesale-REPLACES `conn.config` — the
    catalog secret's ref lives INSIDE config (no column of its own), so a
    config-only PATCH that doesn't re-send `catalog_secret_name` must not drop
    it: that key is server-owned bookkeeping, never something a caller is
    expected to round-trip, exactly like `secret_ref` (its own column) is never
    touched by a config-only PATCH."""
    store = FakeSecretStore()
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret=None,
        catalog_secret="catalog-pw",
        created_by=_user(db_session).id,
        secret_store=store,
    )
    ref = conn.config["catalog_secret_name"]

    # A config-only update that changes something unrelated and does NOT
    # resend catalog_secret_name — the realistic caller shape (the frontend
    # seeds the whole `connection.config`, but a direct API caller need not).
    svc.update_connection(
        db_session,
        conn.id,
        config={**_ICEBERG_SQL_CONFIG, "warehouse": "s3://bucket/warehouse"},
        secret_store=store,
    )

    assert conn.config["catalog_secret_name"] == ref
    assert conn.config["warehouse"] == "s3://bucket/warehouse"
    # …and the credential itself is still resolvable — the actual stake here.
    assert store.data[ref] == "catalog-pw"


def test_config_only_update_still_honors_an_explicitly_resent_catalog_secret_name(
    db_session: Any,
) -> None:
    """If a caller DOES resend `catalog_secret_name` (e.g. echoing back a prior
    GET), the explicit value wins — carry-over only fills a GAP, never overrides."""
    store = FakeSecretStore()
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret=None,
        catalog_secret="catalog-pw",
        created_by=_user(db_session).id,
        secret_store=store,
    )
    original_ref = conn.config["catalog_secret_name"]

    svc.update_connection(
        db_session,
        conn.id,
        config={**_ICEBERG_SQL_CONFIG, "catalog_secret_name": "some-other-ref"},
        secret_store=store,
    )
    assert conn.config["catalog_secret_name"] == "some-other-ref"
    assert conn.config["catalog_secret_name"] != original_ref


# ────────── delete removes the catalog secret too (#372/#1059 convention) ───────


def test_delete_removes_the_catalog_secret_alongside_the_primary(db_session: Any) -> None:
    store = FakeSecretStore()
    conn = svc.create_connection(
        db_session,
        name="harness-iceberg",
        conn_type="iceberg",
        env="dev",
        config=dict(_ICEBERG_SQL_CONFIG),
        secret="storage-key",
        catalog_secret="catalog-pw",
        created_by=_user(db_session).id,
        secret_store=store,
    )
    catalog_ref = conn.config["catalog_secret_name"]
    primary_ref = conn.secret_ref
    assert catalog_ref in store.data and primary_ref in store.data

    svc.delete_connection(db_session, conn.id, secret_store=store, actor_id=conn.created_by)

    assert primary_ref not in store.data
    assert catalog_ref not in store.data  # #1181: was previously left orphaned


def test_delete_without_a_catalog_secret_does_not_choke(db_session: Any) -> None:
    """A connection with no second credential (the common case) must delete
    exactly as it always has — no `catalog_secret_name` key to even look for."""
    store = FakeSecretStore()
    conn = _create(db_session, store)  # plain snowflake, no catalog_secret
    svc.delete_connection(db_session, conn.id, secret_store=store, actor_id=conn.created_by)
    assert db_session.scalars(select(Connection)).all() == []


# ────────── draft test 422s for an unsupported type too (#1116 path symmetry) ───


def test_draft_test_catalog_secret_unsupported_type_raises_config_invalid(
    db_session: Any,
) -> None:
    """`test_draft_connection` must reject a `catalog_secret` for a type with no
    `catalog_secret_name` field exactly like `create_connection` does — a draft
    is nothing MORE permissive than a real create just because nothing persists."""
    with pytest.raises(ConnectionConfigInvalidError):
        svc.test_draft_connection(
            "snowflake",
            env="dev",
            config=dict(_SF_CONFIG),
            secret="p@ss",
            catalog_secret="should-422",
            secret_store=FakeSecretStore(),
        )


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
    conn = _create(db_session, FakeSecretStore(), user=user)
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
    conn = _create(db_session, FakeSecretStore(), secret="super-secret")
    v1 = _versions(db_session, conn.id)[0]
    # the snapshot has no secret column at all; the live value never leaks into it
    assert "super-secret" not in str(v1.config)
    assert not hasattr(v1, "secret_ref")


def test_update_name_or_config_records_new_version(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _create(db_session, FakeSecretStore(), user=actor)
    svc.update_connection(
        db_session,
        conn.id,
        name="renamed",
        config={**_SF_CONFIG, "warehouse": "WH_BIG"},
        secret_store=FakeSecretStore(),
        actor_id=actor.id,
    )
    versions = _versions(db_session, conn.id)
    assert [v.version_no for v in versions] == [1, 2]
    assert versions[1].name == "renamed"
    assert versions[1].config["warehouse"] == "WH_BIG"
    assert versions[1].changed_by == actor.id


def test_secret_only_update_records_no_version(db_session: Any) -> None:
    """Credential rotation is not config history — no new snapshot (mirrors reauth)."""
    conn = _create(db_session, FakeSecretStore())
    store = FakeSecretStore()
    store.set(str(conn.secret_ref), "old")
    svc.update_connection(db_session, conn.id, secret="rotated", secret_store=store)
    assert [v.version_no for v in _versions(db_session, conn.id)] == [1]  # still just the create


def test_noop_update_records_no_version(db_session: Any) -> None:
    """A PATCH that re-sends the current name/config (no net change) must not mint
    a duplicate version — `is_modified` reports no change."""
    conn = _create(db_session, FakeSecretStore())
    svc.update_connection(
        db_session,
        conn.id,
        name=conn.name,  # unchanged
        config=dict(conn.config),  # equal value
        secret_store=FakeSecretStore(),
    )
    assert [v.version_no for v in _versions(db_session, conn.id)] == [1]


def test_create_without_secret_still_snapshots_v1(db_session: Any) -> None:
    """The credential-less create path still records v1 (conn.id is flushed before
    the snapshot regardless of whether a secret is written)."""
    conn = _create(db_session, FakeSecretStore(), secret=None)
    versions = _versions(db_session, conn.id)
    assert [v.version_no for v in versions] == [1]
    assert versions[0].connection_id == conn.id


def test_list_connection_versions_newest_first_with_author(db_session: Any) -> None:
    actor = _user(db_session)
    conn = _create(db_session, FakeSecretStore(), user=actor)
    svc.update_connection(
        db_session, conn.id, name="v2", secret_store=FakeSecretStore(), actor_id=actor.id
    )
    versions = svc.list_connection_versions(db_session, conn.id)
    assert [v.version_no for v in versions] == [2, 1]  # newest first
    assert versions[0].changed_by_name == actor.email  # eager-loaded author


def test_list_connection_versions_unknown_connection_404(db_session: Any) -> None:
    with pytest.raises(ConnectionNotFoundError):
        svc.list_connection_versions(db_session, uuid.uuid4())


def test_delete_connection_cascades_versions(db_session: Any) -> None:
    """Cascade delete is accepted policy — history is not retained past deletion."""
    conn = _create(db_session, FakeSecretStore())
    assert len(_versions(db_session, conn.id)) == 1
    svc.delete_connection(
        db_session, conn.id, secret_store=FakeSecretStore(), actor_id=conn.created_by
    )
    assert _versions(db_session, conn.id) == []


# ───────────────────── datasource health (#954) ─────────────────────


def _run_on(
    db_session: Any,
    conn: Connection,
    *,
    status: str,
    reason: str | None = None,
    minutes_ago: int = 0,
    suite: Suite | None = None,
) -> Run:
    """One run against a suite on `conn`, at an EXPLICIT time.

    `created_at` is set rather than defaulted because Postgres' `now()` is
    transaction-scoped: runs seeded in one test transaction otherwise share a
    timestamp, recency falls through to the `id` tie-break, and `id` is a random
    UUID — so "the newest run" would be arbitrary and this test would be a coin
    flip. (The same tied-timestamp trap #928 fixed for the pipeline-run feed;
    real runs are each their own transaction and do differ.)
    """
    suite = suite or db_session.scalars(select(Suite).where(Suite.connection_id == conn.id)).first()
    if suite is None:
        suite = Suite(
            name=f"s-{uuid.uuid4().hex[:6]}",
            connection_id=conn.id,
            created_by=conn.created_by,
        )
        db_session.add(suite)
        db_session.flush()
    run = Run(suite_id=suite.id, status=status, failure_reason=reason)
    db_session.add(run)
    db_session.flush()
    run.created_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    db_session.flush()
    return run


def test_datasource_health_reports_a_failure_streak(db_session: Any) -> None:
    """A dead credential fails every run — the connection must say so (#954)."""
    conn = _create(db_session, FakeSecretStore())
    for age in (2, 1, 0):
        _run_on(
            db_session,
            conn,
            status="failed",
            reason="The datasource rejected the credentials.",
            minutes_ago=age,
        )
    db_session.commit()

    health = svc.datasource_health(db_session, [conn.id])[conn.id]
    assert health.consecutive_failures == 3
    assert health.reason == "The datasource rejected the credentials."
    assert health.last_run_at is not None


def test_datasource_health_streak_resets_after_one_success(db_session: Any) -> None:
    """The streak counts LEADING failures, so one good run clears it.

    This is the case a naive `count(status='failed')` gets wrong: it would report
    2 for a connection that is working right now, and the badge would cry wolf
    forever after a single historical blip.
    """
    conn = _create(db_session, FakeSecretStore())
    _run_on(db_session, conn, status="failed", reason="old failure", minutes_ago=3)
    _run_on(db_session, conn, status="failed", reason="old failure", minutes_ago=2)
    _run_on(db_session, conn, status="succeeded", minutes_ago=1)  # most recent
    db_session.commit()

    health = svc.datasource_health(db_session, [conn.id])[conn.id]
    assert health.consecutive_failures == 0
    assert health.reason is None


def test_datasource_health_omits_a_connection_with_no_runs(db_session: Any) -> None:
    """No runs is UNKNOWN, not healthy — absent from the mapping so the UI cannot
    render it as a green tick (the rule the poll-health columns already carry)."""
    conn = _create(db_session, FakeSecretStore())
    db_session.commit()
    assert svc.datasource_health(db_session, [conn.id]) == {}


def test_datasource_health_is_one_query_for_many_connections(db_session: Any) -> None:
    """Batched, not per-connection — the N+1 shape #947 just removed elsewhere."""
    from sqlalchemy import event

    conns = [_create(db_session, FakeSecretStore(), name=f"c{i}") for i in range(4)]
    for conn in conns:
        _run_on(db_session, conn, status="failed", reason="boom")
    db_session.commit()
    # Materialise the ids BEFORE recording: reading `.id` off post-commit expired
    # objects issues refresh SELECTs that are the test's own doing, not the
    # function's.
    ids = [c.id for c in conns]

    statements: list[str] = []

    def _record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", _record)
    try:
        health = svc.datasource_health(db_session, ids)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _record)

    assert len(health) == 4
    # Count the HEALTH query specifically (its lateral alias is unmistakable), not
    # session bookkeeping like SAVEPOINTs — asserting on the total would be
    # brittle against unrelated session behaviour while proving less.
    #
    # Keyed on `recent_runs` since #999 replaced the window function with a
    # LATERAL top-N. A marker naming the implementation has to move when the
    # implementation does; the alternative — matching "SELECT ... FROM runs" —
    # would quietly match a future N+1 and pass.
    health_queries = [s for s in statements if "recent_runs" in s.lower()]
    assert len(health_queries) == 1, f"expected one batched query, issued {len(health_queries)}"


def test_datasource_health_a_cancelled_run_does_not_clear_the_streak(db_session: Any) -> None:
    """Only a SUCCEEDED run proves the connection works (#954 review finding).

    The first version broke on any non-failure, so a single cancelled or still-
    running run at the head of the list hid a real failure streak directly
    beneath it — the connection went quietly un-badged while every actual run
    was failing. `queued`/`running` have not answered yet and `cancelled` was
    stopped by a human; none of them is evidence the credential works.
    """
    conn = _create(db_session, FakeSecretStore())
    _run_on(db_session, conn, status="failed", reason="creds rejected", minutes_ago=3)
    _run_on(db_session, conn, status="failed", reason="creds rejected", minutes_ago=2)
    _run_on(db_session, conn, status="cancelled", minutes_ago=1)  # newest, not a success
    db_session.commit()

    health = svc.datasource_health(db_session, [conn.id])[conn.id]
    assert health.consecutive_failures == 2
    assert health.reason == "creds rejected"


def test_datasource_health_an_in_flight_run_does_not_clear_the_streak(db_session: Any) -> None:
    """Same rule for a run that simply hasn't finished yet."""
    conn = _create(db_session, FakeSecretStore())
    _run_on(db_session, conn, status="failed", reason="creds rejected", minutes_ago=2)
    _run_on(db_session, conn, status="running", minutes_ago=1)
    db_session.commit()

    assert svc.datasource_health(db_session, [conn.id])[conn.id].consecutive_failures == 1


# ── #998: per-suite streaks, rolled up ───────────────────────────────────────


def _suite_on(db_session: Any, conn: Connection, name: str) -> Suite:
    suite = Suite(name=name, connection_id=conn.id, created_by=conn.created_by)
    db_session.add(suite)
    db_session.flush()
    return suite


def test_one_broken_suite_does_not_badge_a_working_connection(db_session: Any) -> None:
    """The #998 false positive, pinned.

    A broken suite running often used to fill the shared window and badge a
    connection whose credential is fine — sending the operator to re-authenticate
    something that works. A suite still succeeding proves the datasource is
    reachable, so the CONNECTION-level signal must clear even while that other
    suite stays broken (a per-suite problem belongs on the suite).
    """
    conn = _create(db_session, FakeSecretStore())
    broken = _suite_on(db_session, conn, "broken-hourly")
    healthy = _suite_on(db_session, conn, "healthy-daily")
    # The broken suite runs often and fills the head of any shared window…
    for age in range(0, 10):
        _run_on(
            db_session, conn, status="failed", reason="bad check", minutes_ago=age, suite=broken
        )
    # …while the healthy one succeeded, less recently.
    _run_on(db_session, conn, status="succeeded", minutes_ago=600, suite=healthy)
    db_session.commit()

    health = svc.datasource_health(db_session, [conn.id])[conn.id]

    assert health.consecutive_failures == 0, "a reachable connection must not read as dead"
    assert health.reason is None
    assert health.last_run_at is not None  # still "known", just not degraded


def test_a_connection_is_degraded_only_when_every_suite_is_failing(db_session: Any) -> None:
    """The true positive: a dead credential fails every suite on the connection."""
    conn = _create(db_session, FakeSecretStore())
    a = _suite_on(db_session, conn, "suite-a")
    b = _suite_on(db_session, conn, "suite-b")
    for age in (2, 1, 0):
        _run_on(
            db_session, conn, status="failed", reason="creds rejected", minutes_ago=age, suite=a
        )
    for age in (5, 4):
        _run_on(
            db_session, conn, status="failed", reason="creds rejected", minutes_ago=age, suite=b
        )
    db_session.commit()

    health = svc.datasource_health(db_session, [conn.id])[conn.id]

    # The MINIMUM across suites — the strongest claim true of all of them
    # ("every suite has failed at least twice running"), not the loudest one.
    assert health.consecutive_failures == 2
    assert health.reason == "creds rejected"


def test_a_busy_suite_cannot_crowd_a_quiet_one_out_of_the_window(db_session: Any) -> None:
    """Each suite gets its OWN window (#998 AC 2).

    With a shared 20-run window, 25 failures on a chatty suite would evict the
    quiet suite's success entirely and the connection would read as dead. Per-suite
    windows make the quiet suite's verdict independent of the noisy one's volume.
    """
    conn = _create(db_session, FakeSecretStore())
    noisy = _suite_on(db_session, conn, "noisy")
    quiet = _suite_on(db_session, conn, "quiet")
    for age in range(0, 25):
        _run_on(db_session, conn, status="failed", reason="bad check", minutes_ago=age, suite=noisy)
    _run_on(db_session, conn, status="succeeded", minutes_ago=999, suite=quiet)
    db_session.commit()

    assert svc.datasource_health(db_session, [conn.id])[conn.id].consecutive_failures == 0


# ── #1024: NULL expiry must mean one thing ───────────────────────────────────


def test_checked_at_is_stamped_even_when_there_is_no_readable_expiry(db_session: Any) -> None:
    """The whole point. A Snowflake PAT states no expiry, so `credential_expires_at`
    stays NULL — but "we looked and there is none" must be distinguishable from
    "nobody has looked", or the absence of a warning reads as reassurance."""
    conn = _create(db_session, FakeSecretStore())  # a type with no readable expiry

    assert conn.credential_expires_at is None
    assert conn.credential_expiry_checked_at is not None


def test_a_connection_written_before_the_feature_reads_as_unchecked(db_session: Any) -> None:
    """Prod's actual state after the 2026-07-26 deploy: rows whose secret predates
    #838. They must NOT claim to have been checked — the migration deliberately
    does not backfill, because stamping "checked" for rows nobody read would
    assert exactly the thing this column exists to distinguish."""
    conn = _create(db_session, FakeSecretStore())
    conn.credential_expiry_checked_at = None  # simulate a pre-feature row
    db_session.commit()

    assert conn.credential_expires_at is None
    assert conn.credential_expiry_checked_at is None


def test_renaming_a_connection_never_moves_its_secret_ref(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE invariant of readable names, and the one the suite could not express.

    `secret_ref` is stored, never recomputed. Rename a connection and the generated
    name changes — but the credential still lives under the ORIGINAL key, so
    recomputing would point at a key that does not exist and the credential would
    read as missing: "#954 again, self-inflicted", as the module docstring puts it.

    Every existing rotate/reauth test creates a connection and never renames it, so
    the recomputed ref is byte-identical to the stored one and a recompute passes
    unnoticed — the "fixture encodes our model" shape. Removing the `conn.secret_ref
    or` guard from update AND reauth left all 2828 tests green; this is the test that
    fails.
    """
    store = FakeSecretStore()
    conn = _create(db_session, store)
    original_ref = conn.secret_ref
    assert original_ref is not None

    # Rename so the GENERATED name would now differ from the stored one.
    svc.update_connection(db_session, conn.id, name="Renamed Warehouse", secret_store=store)
    would_be = connection_secret_ref(
        connection_id=conn.id, env=conn.env, name="Renamed Warehouse", conn_type=conn.type
    )
    assert would_be != original_ref, "test is vacuous unless the generated name moved"
    assert conn.secret_ref == original_ref, "a rename must not move the stored ref"

    # Rotating afterwards must write to the ORIGINAL key, not the recomputed one.
    # reauth probes the datasource; stub it so this asserts the ref, not the network.
    class _PassAdapter:
        def validate_config(self, raw: dict[str, Any]) -> Any:
            return None

        def test(self, raw: dict[str, Any], secret: str) -> None:
            return None

    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    svc.reauth_connection(db_session, conn.id, secret="rotated", secret_store=store)
    db_session.refresh(conn)
    assert conn.secret_ref == original_ref
    assert store.data[original_ref] == "rotated"
    assert would_be not in store.data, "rotation wrote to a second, orphaned key"
