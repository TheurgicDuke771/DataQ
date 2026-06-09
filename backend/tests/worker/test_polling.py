"""poll_orchestration_runs task-core tests against a real Postgres (db_session).

`_poll_orchestration_runs` is the testable core: it loops orchestrator
connections, asks each provider's `list_recent_runs` for recent runs, and hands
them to `ingest_polled_runs`. The provider REST call is faked (the live ARM /
Airflow REST is the deferred smoke); the DB round-trip is real. Skips without
TEST_DATABASE_URL.
"""

import uuid
from typing import Any

from sqlalchemy import select

from backend.app.db.models import Connection, PipelineRun, User
from backend.app.orchestration.base import RunUpdate
from backend.app.worker import tasks


class _FakeStore:
    def get(self, name: str) -> str:
        return "sp-secret"

    def set(self, name: str, value: str) -> None:  # pragma: no cover - protocol completeness
        raise NotImplementedError


class _FakeProvider:
    provider = "adf"
    resource_config_key = "factory_name"

    def __init__(
        self, recent: list[RunUpdate] | None = None, raises: Exception | None = None
    ) -> None:
        self._recent = recent or []
        self._raises = raises

    def list_recent_runs(self, config: Any, secret: str, since: Any) -> list[RunUpdate]:
        if self._raises is not None:
            raise self._raises
        return self._recent


def _adf_connection(db_session: Any, *, factory: str = "poll-factory") -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name="adf-dev",
        type="adf",
        env="dev",
        config={
            "subscription_id": "s",
            "resource_group": "rg",
            "factory_name": factory,
            "tenant_id": "t",
            "client_id": "c",
        },
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    conn.secret_ref = f"conn-{conn.id}"
    db_session.commit()
    return conn


def _succeeded(factory: str, run_id: str = "run-1") -> RunUpdate:
    return RunUpdate(
        provider_run_id=run_id,
        pipeline_or_dag_id="load_finance",
        resource_name=factory,
        status="succeeded",
    )


def test_poll_records_succeeded_runs(db_session: Any, monkeypatch: Any) -> None:
    conn = _adf_connection(db_session)
    provider = _FakeProvider(recent=[_succeeded(conn.config["factory_name"])])
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: provider)

    summary = tasks._poll_orchestration_runs(db_session, secret_store=_FakeStore())

    assert summary["connections"] == 1
    assert summary["recorded"] == 1
    assert summary["errors"] == 0
    assert db_session.scalar(select(PipelineRun.status)) == "succeeded"


def test_poll_is_fail_soft_per_connection(db_session: Any, monkeypatch: Any) -> None:
    _adf_connection(db_session)
    provider = _FakeProvider(raises=RuntimeError("ARM unreachable"))
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: provider)

    # one bad connection must not raise — it's logged and counted
    summary = tasks._poll_orchestration_runs(db_session, secret_store=_FakeStore())

    assert summary["errors"] == 1
    assert summary["recorded"] == 0
    assert db_session.scalar(select(PipelineRun.id)) is None


def test_poll_ignores_connections_without_secret(db_session: Any, monkeypatch: Any) -> None:
    # a connection with no secret_ref is filtered out (can't authenticate a poll)
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Connection(
            name="adf-nosecret",
            type="adf",
            env="qa",
            config={
                "subscription_id": "s",
                "resource_group": "rg",
                "factory_name": "no-secret",
                "tenant_id": "t",
                "client_id": "c",
            },
            created_by=user.id,
        )
    )
    db_session.commit()

    def _boom(_t: str) -> Any:
        raise AssertionError("provider must not be resolved for a secret-less connection")

    monkeypatch.setattr(tasks, "get_orchestration_provider", _boom)
    summary = tasks._poll_orchestration_runs(db_session, secret_store=_FakeStore())
    assert summary["connections"] == 0
