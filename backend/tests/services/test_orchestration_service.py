"""orchestration_service.record_pipeline_event tests against a real Postgres.

Covers connection resolution by factory name, the idempotent upsert (replay
lands on the same row and refreshes mutable fields), and the unattributable
event (no matching connection → None). Skips without TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.core.secrets import SecretNotFoundError
from backend.app.db.models import Connection, PipelineRun, Run, Suite, TriggerBinding, User
from backend.app.orchestration.base import RunUpdate
from backend.app.services.orchestration_service import (
    ingest_event,
    ingest_polled_runs,
    record_pipeline_event,
)

_ADF_CONFIG = {
    "subscription_id": "00000000-0000-0000-0000-000000000001",
    "resource_group": "rg-data",
    "factory_name": "example-adf-preprod",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "client_id": "00000000-0000-0000-0000-0000000000bb",
}


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def _adf_connection(
    db_session: Any, *, env: str = "dev", factory: str = "example-adf-preprod"
) -> Connection:
    conn = Connection(
        name=f"adf-{env}",
        type="adf",
        env=env,
        config={**_ADF_CONFIG, "factory_name": factory},
        created_by=_user(db_session).id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _update(**overrides: Any) -> RunUpdate:
    base: dict[str, Any] = {
        "provider_run_id": "run-1",
        "pipeline_or_dag_id": "load_finance",
        "resource_name": "example-adf-preprod",
        "status": "failed",
        "failure_reason": "boom",
    }
    base.update(overrides)
    return RunUpdate(**base)


def test_records_pipeline_run_for_matching_factory(db_session: Any) -> None:
    conn = _adf_connection(db_session)
    run = record_pipeline_event(db_session, provider="adf", update=_update())

    assert run is not None
    assert run.connection_id == conn.id
    assert run.env == "dev"  # taken from the resolved connection
    assert run.provider == "adf"
    assert run.status == "failed"
    assert run.failure_reason == "boom"
    assert run.last_updated_at is not None


def test_unattributable_event_returns_none(db_session: Any) -> None:
    _adf_connection(db_session, factory="some-other-factory")
    run = record_pipeline_event(db_session, provider="adf", update=_update())
    assert run is None
    assert db_session.scalars(select(PipelineRun)).all() == []


def test_replay_is_idempotent_and_refreshes_status(db_session: Any) -> None:
    _adf_connection(db_session)
    first = record_pipeline_event(db_session, provider="adf", update=_update(status="running"))
    assert first is not None
    first_id = first.id

    # Same run id, later delivery with a terminal status → same row, updated.
    second = record_pipeline_event(
        db_session, provider="adf", update=_update(status="failed", failure_reason="late")
    )
    assert second is not None
    assert second.id == first_id  # no duplicate row
    assert len(db_session.scalars(select(PipelineRun)).all()) == 1
    assert second.status == "failed"
    assert second.failure_reason == "late"


def test_resolves_correct_connection_across_envs(db_session: Any) -> None:
    _adf_connection(db_session, env="dev", factory="factory-dev")
    _adf_connection(db_session, env="qa", factory="factory-qa")
    run = record_pipeline_event(
        db_session, provider="adf", update=_update(resource_name="factory-qa")
    )
    assert run is not None
    assert run.env == "qa"


def test_ambiguous_factory_picks_first_match(db_session: Any) -> None:
    # Two connections sharing a factory across envs is a misconfiguration (factory
    # names are globally unique in Azure); the service warns and resolves to one.
    _adf_connection(db_session, env="dev", factory="dup-factory")
    _adf_connection(db_session, env="qa", factory="dup-factory")
    run = record_pipeline_event(
        db_session, provider="adf", update=_update(resource_name="dup-factory")
    )
    assert run is not None
    assert run.env in ("dev", "qa")


# ───────────────────── ingest_event: enrichment + trigger (PR 8) ─────────────


class _FakeStore:
    def __init__(self, **data: str) -> None:
        self.data = dict(data)

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value


class _FakeProvider:
    """Stand-in OrchestrationProvider: parse_event isn't used here; fetch_run_detail
    is driven by the test (returns a canned RunUpdate or raises)."""

    provider = "adf"
    resource_config_key = "factory_name"

    def __init__(
        self,
        detail: RunUpdate | None = None,
        raises: Exception | None = None,
        recent: list[RunUpdate] | None = None,
    ) -> None:
        self._detail = detail
        self._raises = raises
        self._recent = recent or []
        self.calls: list[str] = []

    def parse_event(self, payload: bytes, headers: Any) -> RunUpdate:  # pragma: no cover
        raise NotImplementedError

    def fetch_run_detail(self, config: Any, secret: str, provider_run_id: str) -> RunUpdate:
        self.calls.append(provider_run_id)
        if self._raises is not None:
            raise self._raises
        assert self._detail is not None
        return self._detail

    def list_recent_runs(self, config: Any, secret: str, since: Any) -> list[RunUpdate]:
        if self._raises is not None:
            raise self._raises
        return self._recent


def _adf_connection_with_secret(
    db_session: Any, *, factory: str = "example-adf-preprod"
) -> Connection:
    conn = _adf_connection(db_session, factory=factory)
    conn.secret_ref = f"conn-{conn.id}"
    db_session.commit()
    return conn


def _suite(db_session: Any, connection: Connection) -> Suite:
    suite = Suite(name="s1", connection_id=connection.id, created_by=connection.created_by)
    db_session.add(suite)
    db_session.commit()
    return suite


def _binding(
    db_session: Any, *, suite: Suite, pipeline: str, env: str, enabled: bool = True
) -> None:
    db_session.add(
        TriggerBinding(
            provider="adf",
            pipeline_or_dag_id=pipeline,
            env=env,
            suite_id=suite.id,
            enabled=enabled,
        )
    )
    db_session.commit()


# ── enrichment ──


def test_ingest_enriches_when_connection_has_credential(db_session: Any) -> None:
    _adf_connection_with_secret(db_session)
    enriched = _update(status="succeeded", failure_reason=None, provider_run_id="run-1")
    provider = _FakeProvider(detail=enriched)
    store = _FakeStore(**{f"conn-{db_session.scalars(select(Connection)).first().id}": "sp"})

    result = ingest_event(
        db_session, provider_impl=provider, update=_update(status="running"), secret_store=store
    )
    assert provider.calls == ["run-1"]  # fetch_run_detail was used
    assert result.pipeline_run is not None
    assert result.pipeline_run.status == "succeeded"  # authoritative detail won


def test_ingest_fails_soft_when_enrichment_raises(db_session: Any) -> None:
    _adf_connection_with_secret(db_session)
    provider = _FakeProvider(raises=RuntimeError("ARM unreachable"))
    cid = db_session.scalars(select(Connection)).first().id
    store = _FakeStore(**{f"conn-{cid}": "sp"})

    result = ingest_event(
        db_session, provider_impl=provider, update=_update(status="failed"), secret_store=store
    )
    # falls back to the parsed event rather than dropping it
    assert result.pipeline_run is not None
    assert result.pipeline_run.status == "failed"


def test_ingest_skips_enrichment_without_credential(db_session: Any) -> None:
    _adf_connection(db_session)  # no secret_ref
    provider = _FakeProvider(raises=AssertionError("must not be called"))
    result = ingest_event(
        db_session,
        provider_impl=provider,
        update=_update(status="failed"),
        secret_store=_FakeStore(),
    )
    assert provider.calls == []
    assert result.pipeline_run is not None


def test_ingest_unattributable_returns_empty_result(db_session: Any) -> None:
    provider = _FakeProvider()
    result = ingest_event(
        db_session,
        provider_impl=provider,
        update=_update(resource_name="unknown-factory"),
        secret_store=_FakeStore(),
    )
    assert result.pipeline_run is None
    assert result.triggered_runs == []


# ── trigger-on-success ──


def test_succeeded_run_triggers_bound_suite(db_session: Any) -> None:
    conn = _adf_connection(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env="dev")
    provider = _FakeProvider()

    result = ingest_event(
        db_session,
        provider_impl=provider,
        update=_update(status="succeeded", provider_run_id="run-9"),
        secret_store=_FakeStore(),
    )
    assert len(result.triggered_runs) == 1
    run = db_session.scalars(select(Run)).one()
    assert run.suite_id == suite.id
    assert run.status == "queued"
    assert run.triggered_by == "adf:load_finance:run-9"


def test_trigger_is_idempotent_on_replay(db_session: Any) -> None:
    conn = _adf_connection(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env="dev")
    provider = _FakeProvider()
    upd = _update(status="succeeded", provider_run_id="run-9")

    first = ingest_event(db_session, provider_impl=provider, update=upd, secret_store=_FakeStore())
    second = ingest_event(db_session, provider_impl=provider, update=upd, secret_store=_FakeStore())
    assert len(first.triggered_runs) == 1
    assert second.triggered_runs == []  # replay creates no second run
    assert len(db_session.scalars(select(Run)).all()) == 1


def test_duplicate_orchestration_marker_rejected_by_index(db_session: Any) -> None:
    """The partial unique index is the atomic guard behind ON CONFLICT (#308).

    The in-app check stops the *sequential* replay; the index stops the
    *concurrent* race (two ingestions both passing the check before either
    commits). Simulate the race outcome directly: a second insert of the same
    (suite_id, orchestration marker) must fail at the DB, not double-trigger.
    """
    conn = _adf_connection(db_session)
    suite = _suite(db_session, conn)
    marker = "adf:load_finance:run-9"
    db_session.add(Run(suite_id=suite.id, status="queued", triggered_by=marker))
    db_session.commit()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(Run(suite_id=suite.id, status="queued", triggered_by=marker))
            db_session.flush()


def test_repeatable_markers_are_exempt_from_dedup_index(db_session: Any) -> None:
    """manual/probe/schedule markers legitimately repeat for the same suite.

    The index is partial (orchestration markers only), so re-running a suite
    manually or on a schedule tick must not collide.
    """
    conn = _adf_connection(db_session)
    suite = _suite(db_session, conn)
    for marker in ("manual:user-1", "manual:user-1", "schedule:sch-1", "schedule:sch-1"):
        db_session.add(Run(suite_id=suite.id, status="queued", triggered_by=marker))
    db_session.commit()
    assert len(db_session.scalars(select(Run)).all()) == 4


def test_failed_run_does_not_trigger(db_session: Any) -> None:
    conn = _adf_connection(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env="dev")
    result = ingest_event(
        db_session,
        provider_impl=_FakeProvider(),
        update=_update(status="failed"),
        secret_store=_FakeStore(),
    )
    assert result.triggered_runs == []
    assert db_session.scalars(select(Run)).all() == []


def test_disabled_binding_does_not_trigger(db_session: Any) -> None:
    conn = _adf_connection(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env="dev", enabled=False)
    result = ingest_event(
        db_session,
        provider_impl=_FakeProvider(),
        update=_update(status="succeeded"),
        secret_store=_FakeStore(),
    )
    assert result.triggered_runs == []


def test_binding_for_other_pipeline_does_not_trigger(db_session: Any) -> None:
    conn = _adf_connection(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="some_other_pipeline", env="dev")
    result = ingest_event(
        db_session,
        provider_impl=_FakeProvider(),
        update=_update(status="succeeded", pipeline_or_dag_id="load_finance"),
        secret_store=_FakeStore(),
    )
    assert result.triggered_runs == []


# ── cross-provider: Airflow resolves by base_url; enrichment is skipped ──


def test_ingest_airflow_resolves_by_base_url_and_skips_enrichment(db_session: Any) -> None:
    # AirflowProvider.fetch_run_detail raises NotImplementedError (its callback is
    # authoritative), so _maybe_enrich must skip silently even though the
    # connection has a stored credential — and resolution matches on base_url.
    from backend.app.orchestration.airflow import AirflowProvider

    user = _user(db_session)
    conn = Connection(
        name="airflow-dev",
        type="airflow",
        env="dev",
        config={"base_url": "https://airflow.example.com", "auth_type": "token"},
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    conn.secret_ref = f"conn-{conn.id}"
    db_session.commit()

    update = RunUpdate(
        provider_run_id="dag-run-1",
        pipeline_or_dag_id="load_finance",
        resource_name="https://airflow.example.com",
        status="succeeded",
    )
    result = ingest_event(
        db_session,
        provider_impl=AirflowProvider(),
        update=update,
        secret_store=_FakeStore(**{f"conn-{conn.id}": "token"}),
    )
    assert result.pipeline_run is not None
    assert result.pipeline_run.provider == "airflow"
    assert result.pipeline_run.env == "dev"  # resolved via base_url
    assert result.pipeline_run.status == "succeeded"


# ── polling ingestion (ingest_polled_runs) ──


def test_polled_succeeded_run_records_and_triggers(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    conn = _adf_connection_with_secret(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env=conn.env)
    since = datetime.now(UTC) - timedelta(minutes=15)

    result = ingest_polled_runs(
        db_session,
        provider_impl=_FakeProvider(),
        connection=conn,
        updates=[_update(status="succeeded", provider_run_id="run-poll-1")],
        skip_updated_since=since,
    )
    assert len(result.pipeline_runs) == 1
    assert result.pipeline_runs[0].status == "succeeded"
    assert len(result.triggered_runs) == 1  # binding fired
    assert result.skipped == 0
    # The ungate (#215): each triggered run is handed to Celery.
    assert stub_run_dispatch == [str(result.triggered_runs[0].id)]


def test_dispatch_broker_failure_marks_run_failed_with_finished_at(
    db_session: Any, monkeypatch: Any
) -> None:
    """If dispatch raises (broker down), the triggered run must not be left stuck
    'queued' — it's marked failed with a finished_at, mirroring the worker's
    terminal-failed shape so run-history views stay consistent (#215)."""
    from backend.app.services import run_dispatch

    def _boom(_run_id: Any) -> None:
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _boom)

    conn = _adf_connection_with_secret(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env=conn.env)
    since = datetime.now(UTC) - timedelta(minutes=15)

    result = ingest_polled_runs(
        db_session,
        provider_impl=_FakeProvider(),
        connection=conn,
        updates=[_update(status="succeeded", provider_run_id="run-broker-fail")],
        skip_updated_since=since,
    )
    assert len(result.triggered_runs) == 1
    run = db_session.get(Run, result.triggered_runs[0].id)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.started_at is None  # never started — only dispatch failed


def test_polled_non_succeeded_run_is_recorded_not_triggered(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    # All-status monitor poll (#490): a non-succeeded polled run is now *recorded*
    # in pipeline_runs, but must NOT trigger a suite (trigger-on-success only).
    conn = _adf_connection_with_secret(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env=conn.env)
    result = ingest_polled_runs(
        db_session,
        provider_impl=_FakeProvider(),
        connection=conn,
        updates=[_update(status="failed", provider_run_id="run-poll-2")],
        skip_updated_since=datetime.now(UTC) - timedelta(minutes=15),
    )
    assert len(result.pipeline_runs) == 1
    assert result.pipeline_runs[0].status == "failed"
    assert result.triggered_runs == []  # failure recorded, never triggers
    assert stub_run_dispatch == []
    assert db_session.scalar(select(PipelineRun.id)) is not None


def test_polled_run_skipped_when_recently_updated(db_session: Any) -> None:
    conn = _adf_connection_with_secret(db_session)
    suite = _suite(db_session, conn)
    _binding(db_session, suite=suite, pipeline="load_finance", env=conn.env)
    update = _update(status="succeeded", provider_run_id="run-poll-3")

    # first poll lands the row (sets last_updated_at = now)
    first = ingest_polled_runs(
        db_session,
        provider_impl=_FakeProvider(),
        connection=conn,
        updates=[update],
        skip_updated_since=datetime.now(UTC) - timedelta(minutes=15),
    )
    assert len(first.pipeline_runs) == 1

    # a later poll whose window opened *before* that write skips the run
    second = ingest_polled_runs(
        db_session,
        provider_impl=_FakeProvider(),
        connection=conn,
        updates=[update],
        skip_updated_since=datetime.now(UTC) - timedelta(minutes=15),
    )
    assert second.pipeline_runs == []
    assert second.skipped == 1
    # only one run was ever triggered (no double-fire)
    assert len(list(db_session.scalars(select(Run)))) == 1
