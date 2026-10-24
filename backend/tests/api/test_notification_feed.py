"""The caller's own notification feed over the API (#1687): own rows only, paging honesty,
read-acks, 404-no-leak, Viewers allowed."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import (
    Asset,
    Check,
    Connection,
    Incident,
    Notification,
    Run,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _user(db: Any, *, role: str = "member") -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@example.com", role=role)
    db.add(u)
    db.commit()
    return u


def _suite(db: Any, owner: User) -> Suite:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.commit()
    return suite


def _run(db: Any, suite: Suite) -> Run:
    run = Run(suite_id=suite.id, status="succeeded")
    db.add(run)
    db.commit()
    return run


def _entry(
    db: Any,
    user: User,
    suite: Suite,
    *,
    run: Run | None,
    read: bool = False,
    created_at: datetime | None = None,
    incident_id: uuid.UUID | None = None,
    severity: str = "fail",
) -> Notification:
    row = Notification(
        user_id=user.id,
        suite_id=suite.id,
        run_id=run.id if run else None,
        incident_id=incident_id,
        severity=severity,
        title="s: 1 of 2 checks breached (worst: fail)",
        body="T: c (fail)",
        deep_link=f"/results/{run.id if run else uuid.uuid4()}",
        read_at=datetime.now(UTC) if read else None,
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.commit()
    return row


def test_list_is_own_feed_only_newest_first_with_total(client: TestClient, db_session: Any) -> None:
    me, other = _user(db_session), _user(db_session)
    suite = _suite(db_session, me)
    base = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    older = _entry(db_session, me, suite, run=_run(db_session, suite), created_at=base)
    newer = _entry(
        db_session, me, suite, run=_run(db_session, suite), created_at=base + timedelta(minutes=1)
    )
    _entry(db_session, other, suite, run=_run(db_session, suite))
    _as(me)

    res = client.get("/api/v1/notifications")
    assert res.status_code == 200
    assert [row["id"] for row in res.json()] == [str(newer.id), str(older.id)]
    assert res.headers["X-Total-Count"] == "2"
    row = res.json()[0]
    assert row["deep_link"] == f"/results/{newer.run_id}"
    assert row["incident_link"] is None
    assert row["read_at"] is None
    assert row["severity"] == "fail"


def test_list_pages_honestly_and_filters_unread(client: TestClient, db_session: Any) -> None:
    me = _user(db_session)
    suite = _suite(db_session, me)
    for i in range(3):
        _entry(db_session, me, suite, run=_run(db_session, suite), read=(i == 0))
    _as(me)

    page = client.get("/api/v1/notifications", params={"limit": 2})
    assert len(page.json()) == 2
    assert page.headers["X-Total-Count"] == "3"
    unread = client.get("/api/v1/notifications", params={"unread_only": "true", "limit": 1})
    assert len(unread.json()) == 1
    assert unread.headers["X-Total-Count"] == "2"
    too_small = client.get("/api/v1/notifications", params={"limit": 0})
    too_large = client.get("/api/v1/notifications", params={"limit": 201})
    assert (too_small.status_code, too_large.status_code) == (422, 422)


def test_unread_count_is_mine_only(client: TestClient, db_session: Any) -> None:
    me, other = _user(db_session), _user(db_session)
    suite = _suite(db_session, me)
    _entry(db_session, me, suite, run=_run(db_session, suite))
    _entry(db_session, me, suite, run=_run(db_session, suite), read=True)
    _entry(db_session, other, suite, run=_run(db_session, suite))
    _as(me)
    count = client.get("/api/v1/notifications/unread_count")
    assert count.json() == {"unread": 1}


def test_read_one_is_idempotent(client: TestClient, db_session: Any) -> None:
    me = _user(db_session)
    suite = _suite(db_session, me)
    row = _entry(db_session, me, suite, run=_run(db_session, suite))
    _as(me)

    first = client.post(f"/api/v1/notifications/{row.id}/read")
    assert first.status_code == 200
    stamped = first.json()["read_at"]
    assert stamped is not None
    second = client.post(f"/api/v1/notifications/{row.id}/read")
    assert second.json()["read_at"] == stamped
    count = client.get("/api/v1/notifications/unread_count")
    assert count.json() == {"unread": 0}


def test_another_users_entry_is_404_not_403(client: TestClient, db_session: Any) -> None:
    me, other = _user(db_session), _user(db_session)
    suite = _suite(db_session, other)
    theirs = _entry(db_session, other, suite, run=_run(db_session, suite))
    _as(me)

    res = client.post(f"/api/v1/notifications/{theirs.id}/read")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "notification_not_found"
    missing = client.post(f"/api/v1/notifications/{uuid.uuid4()}/read")
    assert missing.status_code == 404
    db_session.refresh(theirs)
    assert theirs.read_at is None


def test_read_all_marks_only_mine_and_reports_the_count(
    client: TestClient, db_session: Any
) -> None:
    me, other = _user(db_session), _user(db_session)
    suite = _suite(db_session, me)
    _entry(db_session, me, suite, run=_run(db_session, suite))
    _entry(db_session, me, suite, run=_run(db_session, suite))
    _entry(db_session, me, suite, run=_run(db_session, suite), read=True)
    theirs = _entry(db_session, other, suite, run=_run(db_session, suite))
    _as(me)

    first = client.post("/api/v1/notifications/read_all")
    assert first.json() == {"marked": 2}
    again = client.post("/api/v1/notifications/read_all")
    assert again.json() == {"marked": 0}
    db_session.refresh(theirs)
    assert theirs.read_at is None


def test_a_viewer_reads_and_acks_their_own_feed(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session)
    viewer = _user(db_session, role="viewer")
    suite = _suite(db_session, owner)
    row = _entry(db_session, viewer, suite, run=_run(db_session, suite))
    _as(viewer)

    listed = client.get("/api/v1/notifications")
    acked = client.post(f"/api/v1/notifications/{row.id}/read")
    all_acked = client.post("/api/v1/notifications/read_all")
    assert (listed.status_code, acked.status_code, all_acked.status_code) == (200, 200, 200)


def test_deep_link_is_withheld_once_the_run_is_gone(client: TestClient, db_session: Any) -> None:
    me = _user(db_session)
    suite = _suite(db_session, me)
    row = _entry(db_session, me, suite, run=None)
    _as(me)
    body = client.get("/api/v1/notifications").json()
    assert [r["id"] for r in body] == [str(row.id)]
    assert body[0]["run_id"] is None
    assert body[0]["deep_link"] is None


def test_incident_link_points_at_the_incidents_asset(client: TestClient, db_session: Any) -> None:
    me = _user(db_session)
    suite = _suite(db_session, me)
    asset = Asset(namespace="snowflake://a", name="RETAIL.ORDERS", env="dev")
    db_session.add(asset)
    db_session.flush()
    check = Check(suite_id=suite.id, name="c", expectation_type="e", config={})
    db_session.add(check)
    db_session.flush()
    incident = Incident(asset_id=asset.id, check_id=check.id, suite_id=suite.id)
    db_session.add(incident)
    db_session.commit()
    run = _run(db_session, suite)
    row = _entry(db_session, me, suite, run=run, incident_id=incident.id)
    _as(me)

    body = client.get("/api/v1/notifications").json()
    assert body[0]["incident_id"] == str(incident.id)
    assert body[0]["incident_link"] == f"/assets/{asset.id}?incident={incident.id}"
    acked = client.post(f"/api/v1/notifications/{row.id}/read").json()
    assert acked["incident_link"] == body[0]["incident_link"]
