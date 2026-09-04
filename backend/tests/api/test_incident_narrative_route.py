"""`GET /incidents/{id}/narrative` — the UI read for the latest RCA narrative (#1845)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import (
    Check,
    Connection,
    Incident,
    LlmInvocation,
    Result,
    Run,
    Share,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import incident_service, llm_rca, suite_service

_SF_CONFIG = {
    "account": "acme-xy12345",
    "user": "READER",
    "database": "DB",
    "schema": "PUBLIC",
    "warehouse": "WH",
    "role": "R",
}


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _user(db: Any, email: str, *, role: str = "member") -> User:
    user = User(aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email=email, display_name=email)
    user.role = role
    db.add(user)
    db.commit()
    return user


def _suite(db: Any, owner: User) -> Suite:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=_SF_CONFIG,
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    return suite_service.create_suite(
        db,
        name=f"s-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )


def _breach(db: Any, suite: Suite) -> Incident:
    check = Check(
        suite_id=suite.id,
        name="orders_not_null",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.flush()
    run = Run(suite_id=suite.id, status="succeeded", asset_id=suite.asset_id)
    db.add(run)
    db.flush()
    db.add(Result(run_id=run.id, check_id=check.id, status="fail", metric_value=0.4))
    db.commit()
    incident_service.sync_incidents_for_run(db, run_id=run.id)
    incident: Incident | None = db.scalars(
        select(Incident).where(Incident.suite_id == suite.id).order_by(Incident.created_at.desc())
    ).first()
    assert incident is not None
    return incident


def _narrative(
    db: Any, incident: Incident, requester: User, summary: str, *, age_minutes: int = 0
) -> LlmInvocation:
    # Explicit timestamps: rows added in one transaction share `created_at`, and the
    # service's tie-break on `id` is documented as arbitrary.
    when = datetime.now(UTC) - timedelta(minutes=age_minutes)
    row = LlmInvocation(
        kind=llm_rca.RCA_KIND,
        status="succeeded",
        requested_by_user_id=requester.id,
        suite_id=incident.suite_id,
        request={"incident_id": str(incident.id)},
        response={"summary": summary, "ranked_hypotheses": [], "blind_spots": []},
        created_at=when,
        finished_at=when,
    )
    db.add(row)
    db.commit()
    return row


def _get(client: TestClient, incident: Incident) -> Any:
    return client.get(f"/api/v1/incidents/{incident.id}/narrative")


def test_none_generated_reads_as_absent_not_withheld(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@x.io")
    incident = _breach(db_session, _suite(db_session, owner))
    _as(owner)
    r = _get(client, incident)
    assert r.status_code == 200
    assert r.json() == {
        "narrative": None,
        "invocation_id": None,
        "generated_at": None,
        "withheld_reason": None,
    }


def test_requester_reads_their_own_latest(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@x.io")
    incident = _breach(db_session, _suite(db_session, owner))
    _narrative(db_session, incident, owner, "older", age_minutes=5)
    latest = _narrative(db_session, incident, owner, "newer")
    _as(owner)
    body = _get(client, incident).json()
    assert body["narrative"]["summary"] == "newer"
    assert body["invocation_id"] == str(latest.id)
    assert body["generated_at"] is not None
    assert body["withheld_reason"] is None


def test_viewer_sees_it_when_no_cross_suite_sibling(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@x.io")
    viewer = _user(db_session, "v@x.io")
    suite = _suite(db_session, owner)
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()
    incident = _breach(db_session, suite)
    _narrative(db_session, incident, owner, "shared view")
    _as(viewer)
    body = _get(client, incident).json()
    assert body["narrative"]["summary"] == "shared view"
    assert body["withheld_reason"] is None


def test_withheld_from_other_viewer_when_other_suites_check_the_asset(
    client: TestClient, db_session: Any
) -> None:
    owner = _user(db_session, "o@x.io")
    viewer = _user(db_session, "v@x.io")
    suite = _suite(db_session, owner)
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()
    incident = _breach(db_session, suite)
    # Stamp the workspace-true evidence the alert gate keys on.
    incident.evidence = {
        **(incident.evidence or {}),
        "same_asset_siblings": [{"suite_id": str(uuid.uuid4()), "check_name": "secret check"}],
    }
    db_session.commit()
    row = _narrative(db_session, incident, owner, "mentions secret check")
    _as(viewer)
    body = _get(client, incident).json()
    assert body["narrative"] is None
    assert body["invocation_id"] == str(row.id)
    assert body["withheld_reason"] == llm_rca.NARRATIVE_WITHHELD_CROSS_SUITE
    # The requester and a workspace admin still read it.
    _as(owner)
    assert _get(client, incident).json()["narrative"]["summary"] == "mentions secret check"
    admin = _user(db_session, "a@x.io", role="admin")
    _as(admin)
    assert _get(client, incident).json()["narrative"]["summary"] == "mentions secret check"


def test_ungranted_user_gets_404_no_leak(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@x.io")
    outsider = _user(db_session, "x@x.io")
    incident = _breach(db_session, _suite(db_session, owner))
    _narrative(db_session, incident, owner, "private")
    _as(outsider)
    assert _get(client, incident).status_code == 404
