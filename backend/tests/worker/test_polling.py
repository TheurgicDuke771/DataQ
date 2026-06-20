"""poll_orchestration_runs task-core tests against a real Postgres (db_session).

`_poll_orchestration_runs` is the testable core: it loops orchestrator
connections, asks each provider's `list_recent_runs` for recent runs, and hands
them to `ingest_polled_runs`. The provider REST call is faked (the live ARM /
Airflow REST is the deferred smoke); the DB round-trip is real. Skips without
TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime
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
        self.since_arg: Any = None  # records the lookback boundary the poll asked for

    def list_recent_runs(self, config: Any, secret: str, since: Any) -> list[RunUpdate]:
        self.since_arg = since
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


# ───────────────────────── gap recovery (B2) ───────────────────────


def test_default_poll_uses_15min_lookback(db_session: Any, monkeypatch: Any) -> None:
    _adf_connection(db_session)  # a connection must exist for the poll to reach the provider
    provider = _FakeProvider(recent=[])
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: provider)
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

    tasks._poll_orchestration_runs(db_session, secret_store=_FakeStore(), now=now)

    assert provider.since_arg == now - tasks._POLL_LOOKBACK  # 11:45


def test_gap_recovery_widens_lookback_to_one_hour(db_session: Any, monkeypatch: Any) -> None:
    """B2: the gap-recovery sweep asks the provider for a full hour back, not the
    15-min poll window — so a run that completed during a ~40-min outage (older
    than the poll window, never recorded) is still in range to be re-ingested."""
    conn = _adf_connection(db_session)
    provider = _FakeProvider(recent=[_succeeded(conn.config["factory_name"])])
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: provider)
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)

    summary = tasks._poll_orchestration_runs(
        db_session,
        secret_store=_FakeStore(),
        now=now,
        lookback=tasks._GAP_RECOVERY_LOOKBACK,
    )

    assert provider.since_arg == now - tasks._GAP_RECOVERY_LOOKBACK  # 11:00, not 11:45
    assert summary["recorded"] == 1


def test_recover_orchestration_gaps_task_uses_gap_lookback(monkeypatch: Any) -> None:
    """The beat entry point delegates to the shared poll core with the wider
    lookback and closes its session."""
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: _FakeStore())

    def _capture(_session: Any, *, secret_store: Any, lookback: Any = None) -> dict[str, int]:
        captured["session"] = _session
        captured["lookback"] = lookback
        return {"connections": 0}

    monkeypatch.setattr(tasks, "_poll_orchestration_runs", _capture)

    tasks.recover_orchestration_gaps()

    assert captured["lookback"] == tasks._GAP_RECOVERY_LOOKBACK
    assert captured["session"] is session
    assert session.closed is True


def test_beat_start_signal_dispatches_gap_recovery(monkeypatch: Any) -> None:
    """beat_init → one-off gap recovery enqueued by task name (once per beat)."""
    from backend.app.worker import celery_app as celery_mod

    sent: list[str] = []
    monkeypatch.setattr(celery_mod.celery_app, "send_task", sent.append)

    celery_mod._recover_gaps_on_beat_start()

    assert sent == ["recover_orchestration_gaps"]


def test_beat_start_signal_swallows_broker_failure(monkeypatch: Any) -> None:
    """A broker outage at beat startup must not crash the scheduler."""
    from backend.app.worker import celery_app as celery_mod

    def _boom(_name: str) -> None:
        raise RuntimeError("broker unreachable at boot")

    monkeypatch.setattr(celery_mod.celery_app, "send_task", _boom)

    celery_mod._recover_gaps_on_beat_start()  # must not raise
