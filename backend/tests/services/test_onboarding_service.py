"""First-run status (#1668), on real Postgres."""

from __future__ import annotations

import uuid
from typing import Any

from backend.app.db.models import Check, Connection, Run, Suite, User
from backend.app.services.onboarding_service import onboarding_status


def _connection(db: Any, type_: str) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io")
    db.add(owner)
    db.flush()
    connection = Connection(
        name=f"c-{uuid.uuid4().hex[:6]}",
        type=type_,
        env="dev",
        config={},
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(connection)
    db.flush()
    return connection


def test_an_empty_workspace_has_done_nothing(db_session: Any) -> None:
    status = onboarding_status(db_session)

    assert (status.has_datasource, status.has_suite, status.has_check, status.has_run) == (
        False,
        False,
        False,
        False,
    )
    assert status.complete is False


def test_an_orchestration_connection_is_not_a_data_source(db_session: Any) -> None:
    _connection(db_session, "airflow")
    assert onboarding_status(db_session).has_datasource is False

    _connection(db_session, "postgres")
    assert onboarding_status(db_session).has_datasource is True


def test_each_step_is_reported_on_its_own_and_complete_needs_all_four(db_session: Any) -> None:
    conn = _connection(db_session, "postgres")
    suite = Suite(name="orders", connection_id=conn.id, created_by=conn.created_by)
    db_session.add(suite)
    db_session.flush()
    after_suite = onboarding_status(db_session)

    db_session.add(Check(suite_id=suite.id, name="c", expectation_type="e", config={}))
    db_session.flush()
    after_check = onboarding_status(db_session)

    db_session.add(Run(suite_id=suite.id, status="queued", triggered_by="manual"))
    db_session.flush()
    after_run = onboarding_status(db_session)

    assert (after_suite.has_suite, after_suite.has_check, after_suite.complete) == (
        True,
        False,
        False,
    )
    assert (after_check.has_check, after_check.has_run, after_check.complete) == (
        True,
        False,
        False,
    )
    assert after_run.complete is True
