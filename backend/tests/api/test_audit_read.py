"""The workspace-admin audit read surface — ADR 0041 phase 1 (#1318).

`GET /api/v1/admin/audit-events`. Three things are worth testing here and the
first is the one that matters: the gate. This endpoint serves the record of every
privilege change and every credential rotation in the workspace, so it is exactly
the endpoint whose authz must not be assumed from the router's decorator.

Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.core.auth import get_current_user
from backend.app.db.models import AuditEvent, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, role: str) -> User:
    user = User(
        aad_object_id=uuid.uuid4().hex,
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _event(db_session: Any, **kw: Any) -> AuditEvent:
    event = AuditEvent(
        action_class=kw.pop("action_class", "config"),
        action=kw.pop("action", "check.update"),
        entity_type=kw.pop("entity_type", "check"),
        entity_id=kw.pop("entity_id", uuid.uuid4()),
        actor_kind="user",
        **kw,
    )
    db_session.add(event)
    db_session.commit()
    return event


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_non_admin_is_refused(client: TestClient, db_session: Any, role: str) -> None:
    """The gate, asserted rather than assumed from the router decorator.

    This endpoint serves every privilege change and credential rotation in the
    workspace. A `viewer` is tested alongside a `member` because "read-only" is
    the role most likely to be waved through by a reviewer reasoning that a GET is
    harmless — the whole point of an audit log is that reading it is not.
    """
    app.dependency_overrides[get_current_user] = lambda: _user(db_session, role)
    resp = client.get("/api/v1/admin/audit-events")
    assert resp.status_code == 403, resp.text


def test_an_admin_reads_the_log_newest_first(client: TestClient, db_session: Any) -> None:
    """The dev-bypass caller is a workspace admin (#741), so it clears the gate."""
    now = datetime.now(UTC)
    _event(db_session, action="check.create", occurred_at=now - timedelta(hours=2))
    _event(db_session, action="check.delete", occurred_at=now - timedelta(hours=1))

    resp = client.get("/api/v1/admin/audit-events?limit=200")
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()["events"]]
    assert actions.index("check.delete") < actions.index("check.create")


def test_filters_narrow_by_entity_and_actor(client: TestClient, db_session: Any) -> None:
    """The three filters exist to match the three indexes the migration created —
    a filter with no index behind it is a full scan of the biggest table in the
    system."""
    admin = _user(db_session, "admin")
    target = uuid.uuid4()
    _event(db_session, entity_id=target, actor_user_id=admin.id)
    _event(db_session, entity_id=uuid.uuid4())

    by_entity = client.get(f"/api/v1/admin/audit-events?entity_id={target}").json()
    assert by_entity["total"] == 1
    assert by_entity["events"][0]["entity_id"] == str(target)

    by_actor = client.get(f"/api/v1/admin/audit-events?actor_user_id={admin.id}").json()
    assert by_actor["total"] == 1


def test_a_full_page_says_so_rather_than_looking_complete(
    client: TestClient, db_session: Any
) -> None:
    """`truncated` exists because a page of `limit` rows is otherwise
    indistinguishable from "that is all there is" — and on an audit log, "there
    are no more events" is a conclusion someone may act on.

    Computed against the real total, not from `len(events) == limit`, which is
    wrong on the exact-boundary page — asserted below.
    """
    entity = uuid.uuid4()
    for _ in range(3):
        _event(db_session, entity_id=entity)

    page = client.get(f"/api/v1/admin/audit-events?entity_id={entity}&limit=2").json()
    assert len(page["events"]) == 2
    assert page["total"] == 3
    assert page["truncated"] is True

    exact = client.get(f"/api/v1/admin/audit-events?entity_id={entity}&limit=3").json()
    assert len(exact["events"]) == 3
    assert exact["truncated"] is False, "the exact-boundary page is NOT truncated"


def test_the_page_size_is_capped(client: TestClient, db_session: Any) -> None:
    """An uncapped page is a way to pull the whole table through the API one
    request at a time, and this is the table an attacker most wants wholesale.

    Seeds MORE than the cap, which the first version of this test did not — with
    a handful of rows, `limit=100000` returns a handful either way, so the
    assertion held with the cap removed. Mutation-checking is what caught it: a
    ceiling test that never reaches the ceiling tests nothing.
    """
    from backend.app.services.audit_read_service import MAX_PAGE_SIZE

    entity = uuid.uuid4()
    db_session.bulk_save_objects(
        [
            AuditEvent(
                action_class="config",
                action="check.update",
                entity_type="check",
                entity_id=entity,
                actor_kind="user",
            )
            for _ in range(MAX_PAGE_SIZE + 1)
        ]
    )
    db_session.commit()

    resp = client.get(f"/api/v1/admin/audit-events?entity_id={entity}&limit=100000")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == MAX_PAGE_SIZE
    assert body["total"] == MAX_PAGE_SIZE + 1
    # …and the honesty field must reflect the cap, not the request: an operator
    # who asked for everything and silently got 200 rows would read this page as
    # the whole log.
    assert body["truncated"] is True


def test_paging_is_stable_across_same_transaction_events(
    client: TestClient, db_session: Any
) -> None:
    """Postgres freezes `now()` per transaction, so events written together share
    `occurred_at` EXACTLY — a suite create and its checks, for instance.

    Ordering on the timestamp alone leaves those rows in an arbitrary order that
    can differ between the two queries backing two pages, so a row can repeat on
    one page and vanish from the next. The `id` tie-break is what makes paging
    deterministic; without it this test is flaky rather than failing, which is why
    it compares full pagination against a single read.
    """
    entity = uuid.uuid4()
    stamp = datetime.now(UTC)
    for i in range(6):
        _event(db_session, entity_id=entity, action=f"check.update{i}", occurred_at=stamp)

    one_shot = [
        e["id"]
        for e in client.get(f"/api/v1/admin/audit-events?entity_id={entity}&limit=6").json()[
            "events"
        ]
    ]
    paged: list[str] = []
    for offset in (0, 2, 4):
        paged += [
            e["id"]
            for e in client.get(
                f"/api/v1/admin/audit-events?entity_id={entity}&limit=2&offset={offset}"
            ).json()["events"]
        ]
    assert paged == one_shot
    assert len(set(paged)) == 6, "no row may repeat or vanish across pages"


def test_an_actor_deleted_since_the_event_is_still_attributable(
    client: TestClient, db_session: Any
) -> None:
    """`actor_user_id` is `ON DELETE SET NULL`, so the denormalized `actor_label`
    is the only thing keeping the event legible afterwards — which is the entire
    reason the column exists.

    Both fields are served: they agree almost always, and differ exactly when the
    actor was renamed or removed. That difference is information an auditor wants
    ("done by someone who no longer exists"), not a discrepancy to hide by serving
    only one.
    """
    actor = _user(db_session, "admin")
    entity = uuid.uuid4()
    _event(db_session, entity_id=entity, actor_user_id=actor.id, actor_label="Olivia Green")

    db_session.delete(actor)
    db_session.commit()

    event = client.get(f"/api/v1/admin/audit-events?entity_id={entity}").json()["events"][0]
    assert event["actor_user_id"] is None
    assert event["actor_label"] == "Olivia Green"
    assert event["actor_display"] == "Olivia Green"


def test_an_unknown_entity_type_is_refused_not_answered_with_an_empty_page(
    client: TestClient, db_session: Any
) -> None:
    """A typo must not be able to say "nothing happened".

    An unvalidated filter that matches nothing returns `total: 0`, and on THIS
    table an empty page is a statement about the workspace, not about the query —
    the #828 class, in the place it is least affordable. The write path already
    refuses an undeclared entity type, so the read path reuses that vocabulary
    rather than inventing a second one.
    """
    resp = client.get("/api/v1/admin/audit-events?entity_type=cheque")
    assert resp.status_code == 422, resp.text
    body = resp.json()["error"]
    assert body["code"] == "audit_filter_invalid"
    assert "check" in body["detail"]["known"], "the error must say what IS valid"

    ok = client.get("/api/v1/admin/audit-events?entity_type=check")
    assert ok.status_code == 200


def test_a_naive_since_is_read_as_utc(client: TestClient, db_session: Any) -> None:
    """A naive datetime compared against a `timestamptz` column is interpreted in
    the DATABASE session's `TimeZone`, so the same request would cover a different
    period depending on server configuration.

    An audit query that quietly covers a different window than the one asked for
    is worse than one that refuses, and it is invisible: the response looks
    perfectly well-formed.

    **The session timezone is moved off UTC deliberately, and the test is
    worthless without it.** The first version ran against the default UTC session,
    where a naive and an aware timestamp mean the same instant — so it passed with
    the coercion deleted. Mutation-checking caught that. `America/New_York` is
    chosen because it is four hours off UTC in August, comfortably larger than the
    one-hour window under test, so a mis-read boundary changes the answer rather
    than merely nudging it.
    """
    db_session.execute(text("SET TIME ZONE 'America/New_York'"))
    entity = uuid.uuid4()
    now = datetime.now(UTC)
    _event(db_session, entity_id=entity, occurred_at=now - timedelta(hours=3))
    _event(db_session, entity_id=entity, occurred_at=now - timedelta(minutes=30))

    naive = (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    aware = (now - timedelta(hours=1)).isoformat()

    # `params=` rather than an f-string URL: an aware ISO timestamp ends in
    # `+00:00`, and a bare `+` in a query string decodes to a SPACE, so the
    # interpolated version 422s on a malformed datetime and the test would be
    # comparing two failures rather than two windows.
    def _total(since_value: str) -> int:
        resp = client.get(
            "/api/v1/admin/audit-events",
            params={"entity_id": str(entity), "since": since_value},
        )
        assert resp.status_code == 200, resp.text
        return int(resp.json()["total"])

    assert _total(naive) == _total(aware) == 1
    db_session.execute(text("SET TIME ZONE 'UTC'"))


def test_the_page_states_the_retention_window(client: TestClient, db_session: Any) -> None:
    """Pagination honesty is not the only honesty this page needs.

    A query for a window older than `AUDIT_RETENTION_DAYS` returns `total: 0`,
    which is indistinguishable from "nothing happened then" — the single most
    misleading answer an audit log can give. `retained_since` lets a reader tell
    "no events" from "no longer retained".
    """
    from backend.app.core.config import get_settings

    body = client.get("/api/v1/admin/audit-events?limit=1").json()
    assert body["retention_days"] == get_settings().audit_retention_days
    assert body["retained_since"] is not None
    assert datetime.fromisoformat(body["retained_since"]) < datetime.now(UTC)


def test_a_disabled_sweep_reports_no_retention_horizon(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    """`null`, not a date. "Nothing has been swept" is a different statement from
    "swept back to the beginning of time", and collapsing them would be the same
    conflation `retained_since` exists to prevent."""
    from backend.app.services import audit_read_service

    page = audit_read_service.list_events(db_session, limit=1, retention_days=0)
    assert page.retention_days == 0
    assert page.retained_since is None
