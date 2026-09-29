"""A connection (or LLM provider) config that fails its own test is not saved (#1927).

Every door that writes a connection is driven through the real route with the registered
adapter's live probe replaced — never the save-time gate itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.errors import SafeMonitorError
from backend.app.core.secrets import get_secret_store
from backend.app.datasources import registry
from backend.app.db.models import AuditEvent, Connection, LlmInvocation
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import credential_health, llm_service
from backend.tests.support.fake_secret_store import FakeSecretStore

_SF_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}
_LEAKY_DRIVER_TEXT = "login failed for dsn snowflake://svc:hunter2@ab12345"


class _Probe:
    """Stands in for the snowflake adapter's live `test`: records what it was handed, and
    raises `error` while one is set."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str | None, dict[str, Any]]] = []
        self.error: BaseException | None = None

    def __call__(self, raw: dict[str, Any], secret: str | None, **extra: Any) -> None:
        self.calls.append((raw, secret, extra))
        if self.error is not None:
            raise self.error


@pytest.fixture
def store() -> FakeSecretStore:
    return FakeSecretStore()


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _Probe:
    fake = _Probe()
    monkeypatch.setattr(registry._ADAPTERS["snowflake"], "test", fake)
    return fake


@pytest.fixture
def api(db_session: Any, store: FakeSecretStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create(api: TestClient, **overrides: Any) -> Any:
    body: dict[str, Any] = {
        "name": f"sf-{uuid.uuid4().hex[:8]}",
        "type": "snowflake",
        "env": "dev",
        "config": dict(_SF_CONFIG),
        "secret": "p@ss",
    }
    body.update(overrides)
    return api.post("/api/v1/connections", json=body)


def _saved(api: TestClient, probe: _Probe) -> str:
    resp = _create(api)
    assert resp.status_code == 201, resp.text
    probe.calls.clear()
    return str(resp.json()["id"])


def _events(db_session: Any, action: str, entity_id: str | None = None) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == uuid.UUID(entity_id))
    return list(db_session.scalars(stmt))


def _row(db_session: Any, cid: str) -> Connection:
    conn: Connection | None = db_session.get(Connection, uuid.UUID(cid))
    assert conn is not None
    db_session.refresh(conn)
    return conn


# ───────────────────────── create ─────────────────────────


def test_a_failing_create_writes_no_row_no_secret_and_no_audit_event(
    api: TestClient, probe: _Probe, store: FakeSecretStore, db_session: Any
) -> None:
    probe.error = RuntimeError(_LEAKY_DRIVER_TEXT)
    resp = _create(api, name="refused")

    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "connection_test_failed_on_save"
    assert body["message"] == "connection test failed"
    assert "hunter2" not in resp.text
    assert probe.calls[0][1] == "p@ss"
    assert db_session.scalar(select(Connection).where(Connection.name == "refused")) is None
    assert store.data == {}
    assert _events(db_session, "connection.create") == []


def test_a_safe_driver_limitation_reaches_the_client(api: TestClient, probe: _Probe) -> None:
    probe.error = SafeMonitorError("the role has no default warehouse")
    resp = _create(api)
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == (
        "connection test failed: the role has no default warehouse"
    )


def test_a_passing_create_is_saved_audited_as_passed_and_reads_healthy(
    api: TestClient, probe: _Probe, db_session: Any
) -> None:
    resp = _create(api)

    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]
    (event,) = _events(db_session, "connection.create", cid)
    assert event.after is not None and event.after["connection_test"] == "passed"
    assert credential_health.credential_status(_row(db_session, cid)) == "healthy"


def test_skip_test_saves_untested_and_the_audit_event_says_so(
    api: TestClient, probe: _Probe, store: FakeSecretStore, db_session: Any
) -> None:
    probe.error = RuntimeError("unreachable from the API")
    resp = _create(api, skip_test=True)

    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]
    assert probe.calls == []
    conn = _row(db_session, cid)
    assert conn.secret_ref is not None and store.data[conn.secret_ref] == "p@ss"
    assert credential_health.credential_status(conn) == "unknown"
    (event,) = _events(db_session, "connection.create", cid)
    assert event.after is not None and event.after["connection_test"] == "skipped"


def test_a_member_cannot_reach_skip_test(
    api: TestClient, probe: _Probe, as_role: Callable[..., tuple[Any, dict[str, str]]]
) -> None:
    _, headers = as_role("member")
    body = {
        "name": "m",
        "type": "snowflake",
        "env": "dev",
        "config": dict(_SF_CONFIG),
        "secret": "x",
        "skip_test": True,
    }
    resp = api.post("/api/v1/connections", json=body, headers=headers)
    assert resp.status_code == 403
    assert probe.calls == []


# ───────────────────────── update ─────────────────────────


def test_a_failing_config_change_leaves_the_stored_config_alone(
    api: TestClient, probe: _Probe, db_session: Any
) -> None:
    cid = _saved(api, probe)
    probe.error = RuntimeError(_LEAKY_DRIVER_TEXT)

    resp = api.patch(
        f"/api/v1/connections/{cid}", json={"config": {**_SF_CONFIG, "warehouse": "WH_NOPE"}}
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_test_failed_on_save"
    assert "hunter2" not in resp.text
    # Tested with the STORED credential, against the NEW config.
    assert probe.calls[0][0]["warehouse"] == "WH_NOPE"
    assert probe.calls[0][1] == "p@ss"
    assert _row(db_session, cid).config["warehouse"] == "WH_DQ"
    assert _events(db_session, "connection.update", cid) == []


def test_a_failing_credential_rotation_by_patch_keeps_the_stored_credential(
    api: TestClient, probe: _Probe, store: FakeSecretStore, db_session: Any
) -> None:
    cid = _saved(api, probe)
    probe.error = RuntimeError("bad password")

    resp = api.patch(f"/api/v1/connections/{cid}", json={"secret": "wrong"})

    assert resp.status_code == 422
    assert probe.calls[0][1] == "wrong"
    ref = _row(db_session, cid).secret_ref
    assert ref is not None and store.data[ref] == "p@ss"


def test_a_rename_or_an_inventory_toggle_alone_is_not_tested(
    api: TestClient, probe: _Probe, db_session: Any
) -> None:
    cid = _saved(api, probe)
    probe.error = RuntimeError("store is down")

    renamed = api.patch(f"/api/v1/connections/{cid}", json={"name": "renamed"})
    toggled = api.patch(
        f"/api/v1/connections/{cid}", json={"config": {**_SF_CONFIG, "inventory_sync": False}}
    )

    assert (renamed.status_code, toggled.status_code) == (200, 200)
    assert probe.calls == []
    dispositions = [
        (e.after or {}).get("connection_test")
        for e in _events(db_session, "connection.update", cid)
    ]
    assert dispositions == ["not_required", "not_required"]


def test_skip_test_on_a_patch_saves_the_change_and_records_it(
    api: TestClient, probe: _Probe, db_session: Any
) -> None:
    cid = _saved(api, probe)
    probe.error = RuntimeError("store is down")

    resp = api.patch(
        f"/api/v1/connections/{cid}",
        json={"config": {**_SF_CONFIG, "warehouse": "WH_NEW"}, "skip_test": True},
    )

    assert resp.status_code == 200, resp.text
    assert probe.calls == []
    assert _row(db_session, cid).config["warehouse"] == "WH_NEW"
    (event,) = _events(db_session, "connection.update", cid)
    assert event.after is not None and event.after["connection_test"] == "skipped"


def test_a_patch_whose_stored_credential_is_gone_is_refused_not_crashed(
    api: TestClient, probe: _Probe, store: FakeSecretStore, db_session: Any
) -> None:
    cid = _saved(api, probe)
    store.data.clear()

    resp = api.patch(
        f"/api/v1/connections/{cid}", json={"config": {**_SF_CONFIG, "warehouse": "WH_NEW"}}
    )

    assert resp.status_code == 422
    assert "stored credential could not be resolved" in resp.json()["error"]["message"]
    assert probe.calls == []


# ───────────────────────── reauth ─────────────────────────


def test_a_failing_reauth_keeps_the_working_credential_and_its_health(
    api: TestClient, probe: _Probe, store: FakeSecretStore, db_session: Any
) -> None:
    cid = _saved(api, probe)
    probe.error = RuntimeError(_LEAKY_DRIVER_TEXT)

    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "wrong"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_test_failed_on_save"
    assert "hunter2" not in resp.text
    conn = _row(db_session, cid)
    assert conn.secret_ref is not None and store.data[conn.secret_ref] == "p@ss"
    assert credential_health.credential_status(conn) == "healthy"
    assert _events(db_session, "connection.reauth", cid) == []


def test_a_passing_reauth_rotates_and_clears_a_failing_signal(
    api: TestClient, probe: _Probe, store: FakeSecretStore, db_session: Any
) -> None:
    cid = _saved(api, probe)
    conn = _row(db_session, cid)
    conn.consecutive_auth_failures = 3
    db_session.commit()

    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "rotated"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "tested": True}
    conn = _row(db_session, cid)
    assert conn.secret_ref is not None and store.data[conn.secret_ref] == "rotated"
    assert credential_health.credential_status(conn) == "healthy"
    (event,) = _events(db_session, "connection.reauth", cid)
    assert event.after is not None and event.after["connection_test"] == "passed"


def test_skip_test_on_reauth_rotates_untested_and_resets_health_to_unknown(
    api: TestClient, probe: _Probe, store: FakeSecretStore, db_session: Any
) -> None:
    cid = _saved(api, probe)
    conn = _row(db_session, cid)
    conn.consecutive_auth_failures = 2
    db_session.commit()
    probe.error = RuntimeError("store is down")

    resp = api.post(
        f"/api/v1/connections/{cid}/reauth", json={"secret": "rotated", "skip_test": True}
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "tested": False}
    assert probe.calls == []
    conn = _row(db_session, cid)
    assert conn.secret_ref is not None and store.data[conn.secret_ref] == "rotated"
    assert credential_health.credential_status(conn) == "unknown"
    (event,) = _events(db_session, "connection.reauth", cid)
    assert event.after is not None and event.after["connection_test"] == "skipped"


# ───────────────────────── orchestration: a brand-new dbt project ─────────────────────────


def _dbt_body(artifacts: Path, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": f"dbt-{uuid.uuid4().hex[:6]}",
        "type": "dbt",
        "env": "dev",
        "config": {
            "project_name": "analytics",
            "artifacts_uri": artifacts.as_uri(),
            "jobs": ["nightly"],
        },
    }
    body.update(overrides)
    return body


def test_a_dbt_project_that_has_never_built_is_refused_naming_the_path(
    api: TestClient, tmp_path: Path, db_session: Any
) -> None:
    """The real dbt adapter, against an empty artifacts directory (#2215)."""
    resp = api.post("/api/v1/connections", json=_dbt_body(tmp_path))

    assert resp.status_code == 422
    message = resp.json()["error"]["message"]
    assert "no run_results.json at" in message and "nightly/latest/run_results.json" in message
    assert db_session.scalar(select(Connection).where(Connection.type == "dbt")) is None


def test_a_dbt_project_that_has_never_built_can_be_saved_with_skip_test(
    api: TestClient, tmp_path: Path
) -> None:
    resp = api.post("/api/v1/connections", json=_dbt_body(tmp_path, skip_test=True))
    assert resp.status_code == 201, resp.text


# ───────────────────────── LLM provider ─────────────────────────

_LLM_BODY = {
    "provider": "openai_compatible",
    "model": "qwen2.5:3b",
    "base_url": "http://ollama.local/v1",
    "api_key": "sk-1",
    "structured_output": "prompt_json",
    "enabled": True,
}


class _Provider:
    model = "fake"

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    def complete(self, *_a: Any, **_kw: Any) -> Any:
        from backend.app.llm.base import LLMResult

        self.calls += 1
        if self.error is not None:
            raise self.error
        return LLMResult(text="ok", input_tokens=1, output_tokens=1)


def test_an_enabled_llm_provider_that_fails_its_test_is_not_saved(
    api: TestClient, db_session: Any, store: FakeSecretStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.llm.base import LLMUnavailableError

    provider = _Provider(LLMUnavailableError("the endpoint refused the connection"))
    monkeypatch.setattr(llm_service, "_provider_from", lambda **_kw: provider)

    resp = api.put("/api/v1/admin/llm", json=_LLM_BODY)

    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "llm_test_failed_on_save"
    assert body["detail"]["error_code"] == LLMUnavailableError.code
    assert provider.calls == 1
    assert llm_service.get_settings_row(db_session) is None
    assert store.data == {}
    assert _events(db_session, "llm_setting.update") == []
    # The probe is a real outbound call, so it is still on the invocation record.
    assert db_session.query(LlmInvocation).filter_by(kind="ping").count() == 1


def test_an_enabled_llm_provider_that_passes_is_saved(
    api: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_service, "_provider_from", lambda **_kw: _Provider())
    resp = api.put("/api/v1/admin/llm", json=_LLM_BODY)
    assert resp.status_code == 200, resp.text
    row = llm_service.get_settings_row(db_session)
    assert row is not None and row.enabled is True


def test_a_disabled_llm_provider_is_saved_without_a_test(
    api: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.llm.base import LLMUnavailableError

    provider = _Provider(LLMUnavailableError("down"))
    monkeypatch.setattr(llm_service, "_provider_from", lambda **_kw: provider)

    resp = api.put("/api/v1/admin/llm", json={**_LLM_BODY, "enabled": False})

    assert resp.status_code == 200, resp.text
    assert provider.calls == 0
    row = llm_service.get_settings_row(db_session)
    assert row is not None and row.enabled is False
