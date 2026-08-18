"""Does reading regulated data actually record it? — G1 / #431.

`test_access_coverage.py` proves a *decision* was taken for every surface that can
return failing rows. This is the other half: read through the real API and assert
the row.

The distinguishing assertion in this file is **`exposed`**. The HIPAA question is
"who accessed PHI", not "who opened a page" — so a read whose sample came back
fully redacted must be recorded as exposing nothing, and one that surfaced real
failing rows must be recorded as exposing something. A log that cannot tell those
apart makes an investigator read every page-view as an access.

Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import AuditEvent, Check, Connection, Result, Run, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(db_session: Any, *, sample: dict[str, Any] | None, column: str) -> tuple[User, Run]:
    """A suite with one failed check and one result carrying `sample`.

    `column` is the tested column, which is what drives the redactor: a non-PII
    name lets the failing values through, a PII one masks them. That is the lever
    the `exposed` assertions below turn.
    """
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="orders", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="values",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": column},
    )
    db_session.add(check)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status="fail", sample_failures=sample))
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: owner
    return owner, run


def _events(db_session: Any, action: str, entity_id: uuid.UUID) -> list[AuditEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == action,
                AuditEvent.entity_id == entity_id,
                AuditEvent.action_class == "access",
            )
        )
    )


def test_reading_a_run_records_an_access_event(client: TestClient, db_session: Any) -> None:
    """Who read which run's results, and when."""
    owner, run = _seed(
        db_session,
        sample={"partial_unexpected_list": [1, 2, 3], "unexpected_count": 3},
        column="line_total",
    )
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200, resp.text

    events = _events(db_session, "run_results.read", run.id)
    assert len(events) == 1
    event = events[0]
    assert event.action_class == "access", "a read is not a config event"
    assert event.actor_user_id == owner.id
    assert event.after is not None
    assert event.after["result_count"] == 1


def test_an_exposing_read_is_distinguishable_from_a_redacted_one(
    client: TestClient, db_session: Any
) -> None:
    """`exposed` is what makes the log answer the question it exists for.

    A non-PII tested column surfaces its failing values; a PII one is masked. The
    two reads are identical in every other respect — same route, same caller, same
    shape of stored row — so if the event did not distinguish them, an
    investigator would have to treat every page-view as an access to PHI, and the
    handful of events that matter would be buried among the many that do not.
    """
    _owner, safe_run = _seed(
        db_session,
        sample={"partial_unexpected_list": [1, 2, 3], "unexpected_count": 3},
        column="line_total",
    )
    client.get(f"/api/v1/runs/{safe_run.id}")
    safe = _events(db_session, "run_results.read", safe_run.id)[0]

    _owner2, pii_run = _seed(
        db_session,
        sample={"partial_unexpected_list": ["a@b.com"], "unexpected_count": 1},
        column="email",
    )
    client.get(f"/api/v1/runs/{pii_run.id}")
    pii = _events(db_session, "run_results.read", pii_run.id)[0]

    assert safe.after is not None and pii.after is not None
    assert safe.after["exposed"] is True, "a non-PII failing value WAS surfaced"
    assert safe.after["exposed_result_ids"], "and the event must say which result"
    assert pii.after["exposed"] is False, "a fully-masked sample surfaced nothing"
    assert pii.after["exposed_result_ids"] == []


def test_the_event_records_which_result_never_what_it_contained(
    client: TestClient, db_session: Any
) -> None:
    """ADR 0041 §2.6.3. Copying a sample into an append-only table with a LONGER
    retention would silently defeat both the #1253 purge and the #432 erasure
    path — an audit log that quietly becomes a second, unpurged copy of the
    personal data it is auditing is worse than no audit log.
    """
    secret = "alice@example.com"
    _owner, run = _seed(
        db_session,
        sample={"partial_unexpected_list": [secret], "unexpected_count": 1},
        column="line_total",
    )
    client.get(f"/api/v1/runs/{run.id}")

    import json

    event = _events(db_session, "run_results.read", run.id)[0]
    serialized = json.dumps({"before": event.before, "after": event.after})
    assert secret not in serialized
    assert "partial_unexpected_list" not in serialized


def test_a_failed_audit_write_does_not_fail_the_read(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    """The phase-2 contract, and the opposite of phase 1's.

    A config event is fail-closed: if it cannot be written, the change must not
    happen. A read event must NOT take that contract — failing a legitimate read
    because the audit insert failed trades a real outage for a bookkeeping
    problem, and #431's own AC forbids the regression.

    The failure must still be LOUD, which is asserted too: a compliance control
    that silently stops recording is the worst of both worlds.
    """
    from structlog.testing import capture_logs

    from backend.app.services import audit_service

    _owner, run = _seed(
        db_session,
        sample={"partial_unexpected_list": [1], "unexpected_count": 1},
        column="line_total",
    )

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(audit_service, "record", _boom)
    with capture_logs() as logs:
        resp = client.get(f"/api/v1/runs/{run.id}")
    monkeypatch.undo()

    assert resp.status_code == 200, "the read must survive an audit failure"
    assert resp.json()["results"], "and must still return its data"
    assert any(
        entry.get("event") == "audit_access_write_failed" for entry in logs
    ), "a compliance control that stops recording must be visible in telemetry"
