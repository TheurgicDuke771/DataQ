"""In-app role management — ADR 0033 slice #742."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from backend.app.core.auth import DEV_BYPASS_AAD_OID, DEV_BYPASS_EMAIL
from backend.app.core.config import get_settings
from backend.app.db.models import ADMIN_ROLE, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import admin_service


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    db_session.commit()
    return user


# ── the endpoint ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("new_role", ["admin", "member", "viewer"])
def test_an_admin_can_set_any_role(client: TestClient, db_session: Any, new_role: str) -> None:
    """The dev-bypass caller is a workspace admin (#741), so it clears the gate."""
    target = _user(db_session, "member")
    # Keep a second stored admin around so the guard is never the thing under
    # test here — one test asserting two behaviours proves neither cleanly.
    _user(db_session, "admin")

    resp = client.patch(f"/api/v1/admin/users/{target.id}/role", json={"role": new_role})

    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == new_role
    db_session.refresh(target)
    assert target.role == new_role


def test_a_non_admin_gets_403(client: TestClient, db_session: Any, as_role: Any) -> None:
    """Role management is Admin-only — asserted with a genuine member principal,
    not the ambient dev-bypass identity (which is itself an admin).
    """
    _, headers = as_role("member")
    target = _user(db_session, "member")

    resp = client.patch(
        f"/api/v1/admin/users/{target.id}/role", json={"role": "admin"}, headers=headers
    )

    assert resp.status_code == 403
    db_session.refresh(target)
    assert target.role == "member", "a 403 must not have written anything"


def test_an_unknown_role_is_422(client: TestClient, db_session: Any) -> None:
    """The closed vocabulary is in the schema, so the framework rejects it before
    the service is reached — and the OpenAPI doc tells a client what's allowed.
    """
    target = _user(db_session, "member")
    resp = client.patch(f"/api/v1/admin/users/{target.id}/role", json={"role": "owner"})
    assert resp.status_code == 422


def test_the_service_revalidates_the_role_itself(db_session: Any) -> None:
    """The service is callable directly (and is, by the endpoint's own tests), so
    it must not depend on a router having filtered its input.
    """
    actor = _user(db_session, "admin")
    target = _user(db_session, "member")
    with pytest.raises(admin_service.RoleChangeRejectedError):
        admin_service.set_user_role(db_session, target.id, new_role="owner", actor=actor)


def test_an_unknown_user_is_404(client: TestClient) -> None:
    resp = client.patch(f"/api/v1/admin/users/{uuid.uuid4()}/role", json={"role": "member"})
    assert resp.status_code == 404


def test_setting_the_same_role_is_idempotent(client: TestClient, db_session: Any) -> None:
    """A UI that re-submits the current value must not surface a failure."""
    target = _user(db_session, "viewer")
    resp = client.patch(f"/api/v1/admin/users/{target.id}/role", json={"role": "viewer"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "viewer"


def test_the_response_carries_the_same_shape_as_the_list(
    client: TestClient, db_session: Any
) -> None:
    """A response shaped differently from the list it updates is how a table ends
    up with one row rendering unlike its neighbours.
    """
    target = _user(db_session, "member")
    _user(db_session, "admin")

    patched = client.patch(f"/api/v1/admin/users/{target.id}/role", json={"role": "viewer"})
    listed = client.get("/api/v1/admin/users")
    row = next(u for u in listed.json() if u["id"] == str(target.id))

    assert patched.json() == row


# ── the last-admin guard ─────────────────────────────────────────────────────


@pytest.mark.parametrize("new_role", ["member", "viewer"])
def test_the_last_stored_admin_cannot_be_demoted(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, new_role: str
) -> None:
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    only_admin = _user(db_session, "admin")

    with pytest.raises(admin_service.RoleChangeRejectedError) as exc:
        admin_service.set_user_role(db_session, only_admin.id, new_role=new_role, actor=only_admin)

    assert "last workspace admin" in str(exc.value)
    db_session.refresh(only_admin)
    assert only_admin.role == "admin"


def test_an_admin_can_be_demoted_when_another_stored_admin_exists(db_session: Any) -> None:
    first = _user(db_session, "admin")
    second = _user(db_session, "admin")

    admin_service.set_user_role(db_session, first.id, new_role="member", actor=second)

    db_session.refresh(first)
    assert first.role == "member"


def test_self_demotion_is_allowed_when_another_admin_exists(db_session: Any) -> None:
    """An admin stepping down is legitimate; the guard already covers the case
    that makes it unsafe, so there is no separate self-demotion prohibition.
    """
    stepping_down = _user(db_session, "admin")
    _user(db_session, "admin")

    admin_service.set_user_role(
        db_session, stepping_down.id, new_role="member", actor=stepping_down
    )

    db_session.refresh(stepping_down)
    assert stepping_down.role == "member"


def test_an_allowlist_admin_does_not_satisfy_the_guard(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0033 decision 7's counting rule, and the reason it exists."""
    only_stored_admin = _user(db_session, "admin")
    effective_only = _user(db_session, "member")
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", effective_only.email)
    get_settings.cache_clear()
    # Precondition: they really are an admin by the normal resolver.
    from backend.app.core.roles import is_workspace_admin

    assert is_workspace_admin(effective_only) is True

    with pytest.raises(admin_service.RoleChangeRejectedError):
        admin_service.set_user_role(
            db_session, only_stored_admin.id, new_role="member", actor=only_stored_admin
        )


def test_promoting_someone_first_then_demoting_works(db_session: Any) -> None:
    """The documented escape from the guard — and proof it is actually escapable.
    A guard nobody can satisfy is a lockout, not a safeguard.
    """
    only_admin = _user(db_session, "admin")
    successor = _user(db_session, "member")

    admin_service.set_user_role(db_session, successor.id, new_role="admin", actor=only_admin)
    admin_service.set_user_role(db_session, only_admin.id, new_role="member", actor=successor)

    db_session.refresh(only_admin)
    assert only_admin.role == "member"


def test_concurrent_demotions_cannot_both_succeed(_db_engine: Any) -> None:
    """The race the `FOR UPDATE` exists for — with two REAL, COMMITTED sessions."""
    from sqlalchemy.orm import Session as SASession

    engine = _db_engine
    a_id, b_id = uuid.uuid4(), uuid.uuid4()
    setup = SASession(bind=engine)
    try:
        setup.add_all(
            [
                User(
                    id=a_id,
                    aad_object_id=None,
                    email=f"race-a-{a_id.hex[:8]}@example.com",
                    role="admin",
                ),
                User(
                    id=b_id,
                    aad_object_id=None,
                    email=f"race-b-{b_id.hex[:8]}@example.com",
                    role="admin",
                ),
            ]
        )
        setup.commit()
        a = setup.get(User, a_id)
        b = setup.get(User, b_id)
        assert a is not None and b is not None

        s1, s2 = SASession(bind=engine), SASession(bind=engine)
        try:
            # s1 demotes A and commits, taking and releasing the lock.
            admin_service.set_user_role(s1, a_id, new_role="member", actor=b)
            # s2 must then observe the POST-commit state (A is a member) rather
            # than a stale count, and refuse — leaving B as the last admin.
            with pytest.raises(admin_service.RoleChangeRejectedError):
                admin_service.set_user_role(s2, b_id, new_role="member", actor=a)
        finally:
            s1.close()
            s2.close()

        setup.expire_all()
        final_a, final_b = setup.get(User, a_id), setup.get(User, b_id)
        assert final_a is not None and final_b is not None
        assert {final_a.role, final_b.role} == {
            "member",
            "admin",
        }, "exactly one admin must survive, never zero"
    finally:
        setup.rollback()
        for uid in (a_id, b_id):
            row = setup.get(User, uid)
            if row is not None:
                setup.delete(row)
        setup.commit()
        setup.close()


def test_a_stale_in_session_read_cannot_bypass_the_guard(_db_engine: Any) -> None:
    """The interleaving that reached zero stored admins (found in review)."""
    from sqlalchemy.orm import Session as SASession

    x_id, t_id, actor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = (x_id, t_id, actor_id)
    setup = SASession(bind=_db_engine)
    try:
        setup.add_all(
            [
                User(id=x_id, aad_object_id=None, email=f"x-{x_id.hex[:8]}@e.io", role="admin"),
                User(id=t_id, aad_object_id=None, email=f"t-{t_id.hex[:8]}@e.io", role="member"),
                # NOT an admin: the service gates on the actor's role nowhere (the endpoint does),
                # and making them one would leave the workspace a spare admin.
                User(
                    id=actor_id,
                    aad_object_id=None,
                    email=f"a-{actor_id.hex[:8]}@e.io",
                    role="member",
                ),
            ]
        )
        setup.commit()
        actor = setup.get(User, actor_id)
        assert actor is not None

        s_a, s_other = SASession(bind=_db_engine), SASession(bind=_db_engine)
        try:
            # A loads T while it is still a member — this is the stale read.
            stale = s_a.get(User, t_id)
            assert stale is not None and stale.role == "member"

            admin_service.set_user_role(s_other, t_id, new_role=ADMIN_ROLE, actor=actor)
            admin_service.set_user_role(s_other, x_id, new_role="member", actor=actor)

            # A proceeds on its stale picture. T is now the LAST stored admin.
            with pytest.raises(admin_service.RoleChangeRejectedError):
                admin_service.set_user_role(s_a, t_id, new_role="viewer", actor=actor)
        finally:
            s_a.close()
            s_other.close()

        setup.expire_all()
        survivors = [setup.get(User, i) for i in ids]
        assert any(
            u is not None and u.role == ADMIN_ROLE for u in survivors
        ), "the workspace must never be left with zero stored admins"
    finally:
        setup.rollback()
        for uid in ids:
            row = setup.get(User, uid)
            if row is not None:
                setup.delete(row)
        setup.commit()
        setup.close()


def test_the_audit_line_reports_the_locked_previous_role(db_session: Any) -> None:
    """`previous_role` comes from the locked read, so it can never report a value
    that was already stale when the change was decided — the second half of the
    same defect (an audit line that misreports the old value is worse than none).
    """
    actor = _user(db_session, "admin")
    _user(db_session, "admin")
    target = _user(db_session, "member")
    # Change it out from under a caller that might have read it earlier.
    target.role = "viewer"
    db_session.commit()

    with capture_logs() as logs:
        admin_service.set_user_role(db_session, target.id, new_role="member", actor=actor)

    line = next(e for e in logs if e["event"] == "workspace_role_changed")
    assert line["previous_role"] == "viewer"


def test_setting_the_dev_bypass_identity_to_its_current_role_is_a_no_op(
    client: TestClient, db_session: Any
) -> None:
    """The refusal must not contradict the idempotency rule: re-submitting the
    role it already has is a no-op, not a 409.
    """
    client.get("/api/v1/me")
    bypass = db_session.query(User).filter(User.aad_object_id == DEV_BYPASS_AAD_OID).one()

    resp = client.patch(f"/api/v1/admin/users/{bypass.id}/role", json={"role": "admin"})

    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


# ── the dev-bypass identity is not manageable ────────────────────────────────


def test_the_dev_bypass_identity_cannot_be_demoted(client: TestClient, db_session: Any) -> None:
    """It is force-written to `admin` on every request (#741), so accepting a
    demotion would 200 and silently revert on the next one — and *succeeding*
    would lock the only operator out of a stack that has no other door. Refused
    with a reason instead.
    """
    client.get("/api/v1/me")  # materialize the bypass row
    bypass = db_session.query(User).filter(User.aad_object_id == DEV_BYPASS_AAD_OID).one()
    _user(db_session, "admin")  # so the last-admin guard is not what refuses

    resp = client.patch(f"/api/v1/admin/users/{bypass.id}/role", json={"role": "viewer"})

    assert resp.status_code == 409
    assert "dev-bypass" in resp.json()["error"]["message"]
    db_session.refresh(bypass)
    assert bypass.role == "admin"


def test_the_dev_bypass_identity_is_refused_by_email_too(db_session: Any) -> None:
    """Matched on EITHER marker. A row carrying the bypass email but no object id
    is what an OTP-mode stack leaves behind (ADR 0032 linking), so keying only on
    `aad_object_id` would miss it.
    """
    actor = _user(db_session, "admin")
    lookalike = User(id=uuid.uuid4(), aad_object_id=None, email=DEV_BYPASS_EMAIL, role="admin")
    db_session.add(lookalike)
    db_session.commit()

    with pytest.raises(admin_service.RoleChangeRejectedError):
        admin_service.set_user_role(db_session, lookalike.id, new_role="member", actor=actor)


# ── the audit line ───────────────────────────────────────────────────────────


def test_a_role_change_emits_an_audit_line(db_session: Any) -> None:
    """Until ADR 0020's durable change log (#310) exists, this line IS the
    guarantee that a role change is never silent — so its CONTENTS are asserted
    field by field, not merely its presence. An audit line missing the actor is
    not an audit line.
    """
    actor = _user(db_session, "admin")
    target = _user(db_session, "viewer")

    with capture_logs() as logs:
        admin_service.set_user_role(db_session, target.id, new_role="member", actor=actor)

    line = next(entry for entry in logs if entry["event"] == "workspace_role_changed")
    assert line["actor_id"] == str(actor.id)
    assert line["target_user_id"] == str(target.id)
    assert line["previous_role"] == "viewer"
    assert line["new_role"] == "member"
    # No email anywhere in the line — ids correlate, and the logger's PII
    # redaction is a backstop rather than a reason to hand one over.
    assert target.email not in str(line)
    assert actor.email not in str(line)


def test_no_audit_line_when_nothing_changed(db_session: Any) -> None:
    """A log of non-events dilutes the ones that matter."""
    actor = _user(db_session, "admin")
    target = _user(db_session, "viewer")

    with capture_logs() as logs:
        admin_service.set_user_role(db_session, target.id, new_role="viewer", actor=actor)

    assert not [e for e in logs if e["event"] == "workspace_role_changed"]


def test_no_audit_line_when_the_guard_refuses(
    db_session: Any, caplog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The line is emitted after the commit precisely so it can never claim a
    change that did not happen.
    """
    import logging

    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    only_admin = _user(db_session, "admin")

    with caplog.at_level(logging.INFO), pytest.raises(admin_service.RoleChangeRejectedError):
        admin_service.set_user_role(db_session, only_admin.id, new_role="member", actor=only_admin)

    assert not [r for r in caplog.records if "workspace_role_changed" in r.getMessage()]


# ── the admin users list carries role + allowlist_admin ──────────────────────


def test_the_users_list_exposes_stored_role_and_allowlist_flag(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list must show the STORED role (what the editor writes) plus whether the allowlist
    independently makes them an admin. Showing the *effective* role alone would render a break-
    glass admin as `admin` and then appear to fail when demoted — misrepresenting exactly the
    row an admin is most likely to act on.
    """
    break_glass = _user(db_session, "member")
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", break_glass.email)
    get_settings.cache_clear()

    rows = client.get("/api/v1/admin/users").json()
    row = next(u for u in rows if u["id"] == str(break_glass.id))

    assert row["role"] == "member", "the STORED role, not the resolved one"
    assert row["allowlist_admin"] is True


def test_an_ordinary_user_is_not_flagged_as_an_allowlist_admin(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    ordinary = _user(db_session, "member")

    rows = client.get("/api/v1/admin/users").json()
    row = next(u for u in rows if u["id"] == str(ordinary.id))

    assert row["role"] == "member"
    assert row["allowlist_admin"] is False
