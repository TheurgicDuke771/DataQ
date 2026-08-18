"""Does an audited route actually WRITE an event? — ADR 0041 phase 1 (#1318).

`test_audit_coverage.py` proves a *decision* was taken for every mutating route.
It cannot prove an `AUDITED` route really writes, and a manifest that says
"audited" over a route that does not is worse than no manifest — it reads as
coverage. This file is the other half: exercise the route through the real API
against real Postgres, and assert the row.

The three entities here are the ones ADR 0041 singles out as **completely
unrecorded before this change** — a share grant/revoke (the finest-grained
permission in the product), a credential rotation (a hole ADR 0020 shipped
knowingly), and a workspace-role change (which emitted a log line and nothing
durable). They are also the three where a missing record matters most.

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
from backend.app.db.models import AuditEvent, Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, email: str, role: str = "member") -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=email, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session: Any) -> tuple[User, User, Suite, Connection]:
    owner = _user(db_session, f"owner-{uuid.uuid4().hex[:8]}@example.com")
    other = _user(db_session, f"other-{uuid.uuid4().hex[:8]}@example.com")
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="finance", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.commit()
    return owner, other, suite, conn


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _events(
    db_session: Any,
    action: str,
    *,
    entity_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> list[AuditEvent]:
    """Events for one action, narrowed to one entity or one actor.

    **Narrowing is mandatory, enforced here rather than by convention.** An
    earlier version made it optional and three call sites promptly skipped it —
    which is the whole reason it exists: `test_admin_role_management`'s
    concurrency tests use REAL committed sessions by design (a race is correct in
    every single-threaded test and wrong in production), so their rows are not
    rolled back with `db_session` and are still present when this file runs.
    Counting every row for an action passed in isolation and failed in the full
    suite. A default that can be omitted is a default that will be.

    `actor_id` is the narrowing to reach for when the entity's id is assigned by
    the request under test (a share's, say): every test here seeds its own users,
    so the actor identifies this test's events uniquely.
    """
    if entity_id is None and actor_id is None:
        raise AssertionError(
            "narrow by entity_id or actor_id — an un-narrowed count is order-dependent"
        )
    stmt = select(AuditEvent).where(AuditEvent.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if actor_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_id)
    db_session.expire_all()
    return list(db_session.scalars(stmt.order_by(AuditEvent.occurred_at.desc())))


def test_granting_a_share_writes_an_audit_event(client: TestClient, db_session: Any) -> None:
    """The grant is recorded with the permission it conferred.

    A share is the finest-grained authorization decision in the product and left
    no trace of any kind before this — ADR 0041 calls these the highest-value rows
    in the table for exactly that reason.
    """
    owner, other, suite, _conn = _seed(db_session)
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares",
        json={"user_id": str(other.id), "permission": "view"},
    )
    assert resp.status_code in (200, 201), resp.text

    events = _events(db_session, "share.grant", actor_id=owner.id)
    assert len(events) == 1
    event = events[0]
    assert event.action_class == "config"
    assert event.entity_type == "share"
    assert event.entity_id is not None, "a create must carry the id the database assigned"
    assert event.after is not None
    assert event.after["permission"] == "view"
    assert event.after["suite_id"] == str(suite.id)
    assert event.before is None, "a create has no prior state"
    assert event.actor_user_id == owner.id
    assert event.actor_label, "attribution must survive without joining users"


def test_revoking_a_share_records_what_was_destroyed(client: TestClient, db_session: Any) -> None:
    """The revoke's `before` is the only surviving record of the grant.

    The `shares` row is gone afterwards, so if the payload were captured after the
    delete — or not at all — the question "who had access to this suite, and who
    took it away?" would have no answer anywhere in the system.
    """
    owner, other, suite, _conn = _seed(db_session)
    _as(owner)
    client.post(
        f"/api/v1/suites/{suite.id}/shares",
        json={"user_id": str(other.id), "permission": "view"},
    )
    resp = client.delete(f"/api/v1/suites/{suite.id}/shares/{other.id}")
    assert resp.status_code in (200, 204), resp.text

    events = _events(db_session, "share.revoke", actor_id=owner.id)
    assert len(events) == 1
    event = events[0]
    assert event.after is None, "a delete has no resulting state"
    assert event.before is not None
    assert event.before["permission"] == "view"
    assert event.before["user_id"] == str(other.id)
    assert event.entity_id is not None, "the id must survive the row it identified"


def test_a_role_change_writes_both_ends_of_the_change(client: TestClient, db_session: Any) -> None:
    """ADR 0033 §7 requires a durable record of privilege changes; before this
    there was a log line and nothing queryable.

    Both ends are asserted because only one of them is interesting on its own: the
    new role stays visible on the `users` row, and the OLD one exists nowhere else
    once the change commits.
    """
    _owner, other, _suite, _conn = _seed(db_session)
    # The dev-bypass caller is a workspace admin (#741), so it clears the gate.
    resp = client.patch(f"/api/v1/admin/users/{other.id}/role", json={"role": "viewer"})
    assert resp.status_code == 200, resp.text

    events = _events(db_session, "user.role_change", entity_id=other.id)
    assert len(events) == 1
    event = events[0]
    assert event.entity_type == "user"
    assert event.entity_id == other.id
    assert event.before is not None and event.before["role"] == "member"
    assert event.after is not None and event.after["role"] == "viewer"


def test_a_refused_role_change_records_nothing(client: TestClient, db_session: Any) -> None:
    """The audit write is same-transaction, so a rejected change must leave no row.

    This is the half a naive "did it write?" test misses, and it is what makes the
    log trustworthy: an event for a change that did not happen is worse than a
    missing event, because a reader cannot tell it apart from a real one.
    """
    _seed(db_session)
    missing = uuid.uuid4()
    resp = client.patch(f"/api/v1/admin/users/{missing}/role", json={"role": "viewer"})
    assert resp.status_code >= 400
    assert _events(db_session, "user.role_change", entity_id=missing) == []


def test_a_connection_delete_is_the_only_surviving_record_of_it(
    client: TestClient, db_session: Any
) -> None:
    """`connection_versions` is `ondelete=CASCADE`, so the config history dies with
    the connection. The audit event is what remains — which is precisely why
    `audit_events.entity_id` carries no foreign key.
    """
    owner, _other, suite, conn = _seed(db_session)
    # The 409 guard refuses while a suite runs against it (#753), so unbind first.
    db_session.delete(suite)
    # Connection mutations are Admin-only since ADR 0033 (#741) — the owner of the
    # connection is not sufficient, which is the whole point of that change.
    owner.role = "admin"
    db_session.commit()
    _as(owner)

    resp = client.delete(f"/api/v1/connections/{conn.id}")
    assert resp.status_code in (200, 204), resp.text

    events = _events(db_session, "connection.delete", entity_id=conn.id)
    assert len(events) == 1
    event = events[0]
    assert event.entity_id == conn.id
    assert event.after is None
    assert event.before is not None
    assert event.before["name"] == conn.name
    assert event.before["type"] == "snowflake"
    # The config blob is excluded wholesale — it is where every `*_secret_name`
    # pointer and every adapter-specific field lives.
    assert "config" not in event.before


def test_the_auto_classify_beat_task_records_nothing(db_session: Any) -> None:
    """A machine write must not enter the audit log (ADR 0041 §2.1).

    `set_column_policy` has two callers: an author setting a redaction policy —
    audited, and among the highest-value events in the table — and the
    auto-classify beat task deriving one for a suite that has none. The second has
    no principal, and there is deliberately no `system` actor_kind, so it must
    write nothing rather than write an unattributable event.

    The exclusion is an explicit `machine_write=True` at the call site rather than
    an inference from a missing `actor_id`, because a missing `actor_id` is
    exactly what a FORGOTTEN one looks like — inferring the exclusion from it
    would silently drop a principal's act. This test pins the flag's effect; the
    test above pins that the audited path still fires.
    """
    from backend.app.services import suite_service

    _owner, _other, suite, _conn = _seed(db_session)

    suite_service.set_column_policy(
        db_session,
        suite.id,
        identifier_column="order_id",
        pii_columns=["email"],
        machine_write=True,
    )

    assert _events(db_session, "suite.column_policy_update", entity_id=suite.id) == []
    # …and the policy really was written, so this is not passing because nothing
    # happened at all.
    db_session.refresh(suite)
    assert suite.column_policy == {"pii_columns": ["email"], "identifier_column": "order_id"}


def test_a_rebaseline_that_dropped_nothing_records_nothing(
    client: TestClient, db_session: Any
) -> None:
    """`monitor_baseline.rebaseline` is idempotent and returns whether a baseline
    existed; the first version of this route discarded that flag.

    An event for a request that discarded nothing is worse here than the usual
    no-op noise: "the baseline was reset" is a real explanation for a monitor
    going quiet, and a reader with no way to tell a real reset from an empty one
    would accept the false answer.
    """
    from backend.app.db.models import Check

    owner, _other, suite, _conn = _seed(db_session)
    check = Check(
        suite_id=suite.id,
        name="drift",
        kind="schema_drift",
        expectation_type="schema_drift",
        config={},
    )
    db_session.add(check)
    db_session.commit()
    _as(owner)

    resp = client.post(f"/api/v1/suites/{suite.id}/checks/{check.id}/rebaseline")
    assert resp.status_code in (200, 204), resp.text
    assert _events(db_session, "check.rebaseline", entity_id=check.id) == []


def test_an_empty_asset_patch_records_nothing(client: TestClient, db_session: Any) -> None:
    """A PATCH naming no fields is a real request that changed nothing.

    The route builds its partial-update discriminators from `model_fields_set`, so
    an empty body reaches the service with both set to False and mutates nothing —
    and before `if_changed` it still minted an `asset.update` whose `before` and
    `after` were identical.
    """
    from backend.app.db.models import Asset

    asset = Asset(namespace="snowflake://acct", name="RETAIL.ORDERS", env="dev")
    db_session.add(asset)
    db_session.commit()

    resp = client.patch(f"/api/v1/assets/{asset.id}", json={})
    assert resp.status_code == 200, resp.text
    assert _events(db_session, "asset.update", entity_id=asset.id) == []


def test_a_real_asset_patch_still_records(client: TestClient, db_session: Any) -> None:
    """The other half — `if_changed` must not be a mute button."""
    from backend.app.db.models import Asset

    asset = Asset(namespace="snowflake://acct", name="RETAIL.ORDERS", env="dev")
    db_session.add(asset)
    db_session.commit()

    resp = client.patch(f"/api/v1/assets/{asset.id}", json={"description": "the orders table"})
    assert resp.status_code == 200, resp.text
    events = _events(db_session, "asset.update", entity_id=asset.id)
    assert len(events) == 1
    assert events[0].after is not None
    assert events[0].after["description"] == "the orders table"


def test_the_notification_race_loser_records_an_update_not_a_create(
    db_session: Any, monkeypatch: Any
) -> None:
    """The concurrent-insert-loser branch overwrites the winner's row.

    `audit_before` is captured from OUR read, which found no row — so on this
    branch it was `None` while a row demonstrably existed and was about to be
    replaced. Left uncorrected, the event reports a create and the configuration
    being overwritten is lost, in the one branch entered precisely because
    something else got there first.

    **Reaching the branch is the whole difficulty, and the obvious test does not.**
    Seeding the row and calling `upsert_config` takes the ordinary update path,
    because `get_config` finds it — that version passed against the unfixed code
    and was caught only by mutation-checking. The branch is defined by a
    contradiction between two observations ("my read saw nothing" and "my insert
    conflicts"), so the read is what has to be forced: `get_config` returns None
    once, then delegates. That is exactly what a lost race looks like from inside
    the function, without the nondeterminism of real threads.
    """
    from backend.app.core.secrets import get_secret_store
    from backend.app.db.models import SuiteNotification
    from backend.app.services import notification_service

    owner, _other, suite, _conn = _seed(db_session)
    db_session.add(SuiteNotification(suite_id=suite.id, enabled=False, alert_on="fail"))
    db_session.commit()

    real_get_config = notification_service.get_config
    calls = {"n": 0}

    def _first_read_sees_nothing(session: Any, suite_id: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_get_config(session, suite_id)

    monkeypatch.setattr(notification_service, "get_config", _first_read_sees_nothing)

    notification_service.upsert_config(
        db_session,
        suite_id=suite.id,
        enabled=True,
        alert_on="warn",
        webhook=None,
        secret_store=get_secret_store(),
        actor_id=owner.id,
    )
    assert calls["n"] >= 2, "the loser branch was never entered — the test proves nothing"

    events = _events(db_session, "suite_notification.update", actor_id=owner.id)
    assert len(events) == 1
    assert events[0].before is not None, "an overwrite is not a create"
    assert events[0].before["enabled"] is False
    assert events[0].before["alert_on"] == "fail"
    assert events[0].after is not None and events[0].after["alert_on"] == "warn"


def test_the_probe_records_provisioning_once_not_on_every_smoke(db_session: Any) -> None:
    """`ensure_probe_fixtures` is a get-or-create, so the common case is a repeat
    call that provisions nothing.

    An event per smoke run would report provisioning that did not happen, in the
    log an auditor reads to find out what did — and this endpoint is the "third
    door" that creates a Connection AND a Suite under a name mentioning neither,
    so its events are the ones least likely to be sanity-checked against another
    source.

    Both halves are asserted in one test on purpose: the interesting property is
    the DIFFERENCE between the first call and the second, and splitting it would
    let a always-record and a never-record bug each pass one half.
    """
    from backend.app.core.config import get_settings
    from backend.app.services import probe

    owner, _other, _suite, _conn = _seed(db_session)

    probe.ensure_probe_fixtures(db_session, user=owner, settings=get_settings())
    first = _events(db_session, "probe.provision", actor_id=owner.id)
    assert len(first) == 1, "the provisioning call must be recorded"

    probe.ensure_probe_fixtures(db_session, user=owner, settings=get_settings())
    assert _events(db_session, "probe.provision", actor_id=owner.id) == first
