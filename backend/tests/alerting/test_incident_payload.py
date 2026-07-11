"""The publisher payload carries the incident reference + evidence card (ADR 0034
#761): after the engine reconciles a run's incidents, ``build_run_report`` attaches
an ``IncidentCard`` per breaching check.

Skips without TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from typing import Any

from backend.app.alerting import builder
from backend.app.db.models import Check, Connection, Result, Run, User
from backend.app.services import incident_service, suite_service

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}


def _seed(db: Any, *, status: str = "fail") -> Run:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=_SF_CONFIG,
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    suite = suite_service.create_suite(
        db,
        name="Orders QA",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )
    check = Check(
        suite_id=suite.id,
        name="not-null id",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.flush()
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual", asset_id=suite.asset_id)
    db.add(run)
    db.flush()
    db.add(Result(run_id=run.id, check_id=check.id, status=status, metric_value=0.4))
    db.commit()
    return run


def test_report_carries_incident_card(db_session: Any) -> None:
    run = _seed(db_session, status="fail")
    # Reconcile incidents first (worker order), then build the report.
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    report = builder.build_run_report(db_session, run)

    assert len(report.incidents) == 1
    card = report.incidents[0]
    assert card.check_name == "not-null id"
    assert card.status == "fail"
    assert card.occurrence_count == 1
    assert card.is_new is True  # this run opened it
    assert card.evidence is not None
    assert card.incident_id is not None


def test_report_card_marks_recurring_occurrence(db_session: Any) -> None:
    run = _seed(db_session, status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    # A second failing run on the same suite → attaches an occurrence.
    suite_id = run.suite_id
    from backend.app.db.models import Suite

    suite = db_session.get(Suite, suite_id)
    check = suite.checks[0]
    run2 = Run(
        suite_id=suite_id, status="succeeded", triggered_by="manual", asset_id=suite.asset_id
    )
    db_session.add(run2)
    db_session.flush()
    db_session.add(Result(run_id=run2.id, check_id=check.id, status="fail"))
    db_session.commit()
    incident_service.sync_incidents_for_run(db_session, run_id=run2.id)

    report = builder.build_run_report(db_session, run2)
    assert len(report.incidents) == 1
    card = report.incidents[0]
    assert card.occurrence_count == 2
    assert card.is_new is False  # a recurrence, not a fresh open


def test_clean_run_has_no_incident_cards(db_session: Any) -> None:
    run = _seed(db_session, status="pass")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    report = builder.build_run_report(db_session, run)
    assert report.incidents == []
