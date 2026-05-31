"""orchestration_service.record_pipeline_event tests against a real Postgres.

Covers connection resolution by factory name, the idempotent upsert (replay
lands on the same row and refreshes mutable fields), and the unattributable
event (no matching connection → None). Skips without TEST_DATABASE_URL.
"""

import uuid
from typing import Any

from sqlalchemy import select

from backend.app.db.models import Connection, PipelineRun, User
from backend.app.orchestration.base import RunUpdate
from backend.app.services.orchestration_service import record_pipeline_event

_ADF_CONFIG = {
    "subscription_id": "00000000-0000-0000-0000-000000000001",
    "resource_group": "rg-data",
    "factory_name": "lll-adf-nonprod",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "client_id": "00000000-0000-0000-0000-0000000000bb",
}


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def _adf_connection(
    db_session: Any, *, env: str = "dev", factory: str = "lll-adf-nonprod"
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
        "resource_name": "lll-adf-nonprod",
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
