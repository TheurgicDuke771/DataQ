"""The offboarding pass — preview honesty, the guards, and what survives (#1699)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.models import (
    ApiKey,
    AuditEvent,
    Check,
    CheckVersion,
    Connection,
    Result,
    Run,
    Share,
    Suite,
    User,
    UserSession,
    WorkspaceMember,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import api_key_service


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, role: str = "member", *, email: str | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=email or f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _suite(db_session: Any, owner: User) -> Suite:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name=f"suite-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    return suite


def _member_row(db_session: Any, user: User) -> WorkspaceMember:
    row = WorkspaceMember(
        id=uuid.uuid4(),
        email=user.email.lower(),
        initial_role=user.role,
        source="admin",
        invited_by=None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _session_row(db_session: Any, user: User, *, hours: int = 4) -> UserSession:
    row = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _preview(client: TestClient, user: User) -> dict[str, Any]:
    resp = client.get(f"/api/v1/admin/offboarding/{user.id}/preview")
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def _offboard(client: TestClient, user: User, **body: Any) -> Any:
    payload: dict[str, Any] = {"confirm_email": user.email, **body}
    return client.post(f"/api/v1/admin/offboarding/{user.id}", json=payload)


# ── Preview honesty ──────────────────────────────────────────────────────────


def test_the_preview_counts_what_the_pass_would_touch(client: TestClient, db_session: Any) -> None:
    leaver = _user(db_session, "member")
    suite = _suite(db_session, leaver)
    db_session.add(
        Check(
            suite_id=suite.id,
            name="rows",
            expectation_type="expect_table_row_count_to_be_between",
            config={"min_value": 1},
        )
    )
    _member_row(db_session, leaver)
    _session_row(db_session, leaver)
    db_session.commit()
    api_key_service.create_key(db_session, leaver, name="laptop")

    view = _preview(client, leaver)

    assert view["email"] == leaver.email
    assert [s["id"] for s in view["owned_suites"]] == [str(suite.id)]
    assert view["owned_suites"][0]["check_count"] == 1
    assert view["open_api_key_count"] == 1
    assert view["live_session_count"] == 1
    assert view["membership_state"] == "member"
    assert view["is_last_admin"] is False
    assert view["is_self"] is False


def test_an_expired_credential_is_not_reported_as_live(client: TestClient, db_session: Any) -> None:
    """`open_api_key_count` answers "what will this pass revoke", so a lapsed PAT
    and a revoked session must not inflate it into a threat that isn't there.
    """
    leaver = _user(db_session, "member")
    db_session.commit()
    key, _ = api_key_service.create_key(db_session, leaver, name="stale")
    key.expires_at = datetime.now(UTC) - timedelta(days=1)
    revoked = _session_row(db_session, leaver)
    revoked.revoked_at = datetime.now(UTC)
    db_session.commit()

    view = _preview(client, leaver)

    assert view["open_api_key_count"] == 0
    assert view["live_session_count"] == 0


def test_a_user_with_no_membership_row_says_so_rather_than_implying_a_removal(
    client: TestClient, db_session: Any
) -> None:
    leaver = _user(db_session, "member")
    db_session.commit()

    view = _preview(client, leaver)

    assert view["membership_state"] == "not_a_member"
    assert view["membership_id"] is None
    assert "nothing to withdraw" in (view["membership_note"] or "")


def test_an_env_listed_address_names_the_variable_that_still_admits_them(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is not the whole rule — membership is the union of it and the env
    allowlist, so deleting it here would leave a working sign-in.
    """
    leaver = _user(db_session, "member", email=f"env-{uuid.uuid4().hex[:6]}@example.com")
    _member_row(db_session, leaver)
    db_session.commit()
    monkeypatch.setenv("OIDC_ALLOWED_EMAILS", leaver.email)
    get_settings.cache_clear()

    view = _preview(client, leaver)

    assert view["membership_state"] == "env_listed"
    assert "OIDC_ALLOWED_EMAILS" in (view["membership_note"] or "")


def test_a_domain_allowlist_counts_as_env_listed_too(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaver = _user(db_session, "member", email=f"dom-{uuid.uuid4().hex[:6]}@leaver.example")
    _member_row(db_session, leaver)
    db_session.commit()
    monkeypatch.setenv("OIDC_ALLOWED_DOMAINS", "leaver.example")
    get_settings.cache_clear()

    view = _preview(client, leaver)

    assert view["membership_state"] == "env_listed"
    assert "OIDC_ALLOWED_DOMAINS" in (view["membership_note"] or "")


def test_previewing_an_unknown_user_is_404(client: TestClient) -> None:
    resp = client.get(f"/api/v1/admin/offboarding/{uuid.uuid4()}/preview")
    assert resp.status_code == 404


# ── The gate ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_non_admin_cannot_preview_or_offboard(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    """A REAL user id: a fabricated one 404s before the gate is reached, and the
    sweep would then pass with the gate deleted.
    """
    _, headers = as_role(role)
    leaver = _user(db_session, "member")
    db_session.commit()

    preview_resp = client.get(f"/api/v1/admin/offboarding/{leaver.id}/preview", headers=headers)
    offboard_resp = client.post(
        f"/api/v1/admin/offboarding/{leaver.id}",
        json={"confirm_email": leaver.email},
        headers=headers,
    )

    assert preview_resp.status_code == 403
    assert offboard_resp.status_code == 403


def test_an_admin_gets_through(client: TestClient, db_session: Any) -> None:
    """The dev-bypass caller is a workspace admin (#741)."""
    leaver = _user(db_session, "member")
    db_session.commit()

    resp = _offboard(client, leaver)
    assert resp.status_code == 200


# ── The guards ───────────────────────────────────────────────────────────────


def test_a_mistyped_confirmation_changes_nothing(client: TestClient, db_session: Any) -> None:
    leaver = _user(db_session, "member")
    _member_row(db_session, leaver)
    db_session.commit()
    api_key_service.create_key(db_session, leaver, name="laptop")

    resp = _offboard(client, leaver, confirm_email="someone.else@example.com")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "offboard_rejected"
    assert _preview(client, leaver)["open_api_key_count"] == 1


def test_the_confirmation_is_case_insensitive_like_every_other_address_check(
    client: TestClient, db_session: Any
) -> None:
    leaver = _user(db_session, "member", email=f"Mixed-{uuid.uuid4().hex[:6]}@Example.com")
    db_session.commit()

    resp = _offboard(client, leaver, confirm_email=leaver.email.upper())
    assert resp.status_code == 200


def test_owning_suites_with_nobody_named_to_inherit_them_is_refused(
    client: TestClient, db_session: Any
) -> None:
    leaver = _user(db_session, "member")
    _suite(db_session, leaver)
    db_session.commit()

    resp = _offboard(client, leaver)

    assert resp.status_code == 422
    assert "inherits" in resp.json()["error"]["message"]


def test_a_viewer_cannot_inherit_the_suites(client: TestClient, db_session: Any) -> None:
    """ADR 0033: viewers are read-only, so they cannot own. The whole pass is
    refused rather than half-run.
    """
    leaver = _user(db_session, "member")
    viewer = _user(db_session, "viewer")
    suite = _suite(db_session, leaver)
    db_session.commit()
    api_key_service.create_key(db_session, leaver, name="laptop")

    resp = _offboard(client, leaver, new_owner_user_id=str(viewer.id))

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_transfer_rejected"
    db_session.expire_all()
    assert db_session.get(Suite, suite.id).created_by == leaver.id
    assert _preview(client, leaver)["open_api_key_count"] == 1


def test_the_departing_user_cannot_inherit_their_own_suites(
    client: TestClient, db_session: Any
) -> None:
    leaver = _user(db_session, "member")
    _suite(db_session, leaver)
    db_session.commit()

    resp = _offboard(client, leaver, new_owner_user_id=str(leaver.id))

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "offboard_rejected"


# ── The pass ─────────────────────────────────────────────────────────────────


def test_the_whole_pass_runs_and_reports_what_it_did(client: TestClient, db_session: Any) -> None:
    leaver = _user(db_session, "member")
    heir = _user(db_session, "member")
    suite = _suite(db_session, leaver)
    member = _member_row(db_session, leaver)
    _session_row(db_session, leaver)
    db_session.commit()
    key, _ = api_key_service.create_key(db_session, leaver, name="laptop")
    suite_id, member_id, key_id = suite.id, member.id, key.id
    leaver_id, heir_id = leaver.id, heir.id

    resp = _offboard(client, leaver, new_owner_user_id=str(heir.id))

    assert resp.status_code == 200, resp.text
    receipt = resp.json()
    assert receipt["transferred_suite_ids"] == [str(suite_id)]
    assert receipt["api_keys_revoked"] == 1
    assert receipt["sessions_revoked"] == 1
    assert receipt["membership_removed"] is True
    assert receipt["skipped"] == []

    # Detach rather than expire: the membership row is gone, and refreshing a
    # deleted instance raises instead of reading as absent.
    db_session.expunge_all()
    assert db_session.get(Suite, suite_id).created_by == heir_id
    assert db_session.get(ApiKey, key_id).revoked_at is not None
    assert db_session.get(WorkspaceMember, member_id) is None
    live = db_session.scalars(
        select(UserSession).where(
            UserSession.user_id == leaver_id, UserSession.revoked_at.is_(None)
        )
    ).all()
    assert live == []


def test_the_departing_user_keeps_no_access_to_the_suites_they_handed_over(
    client: TestClient, db_session: Any
) -> None:
    """The opposite default from the standalone transfer endpoint: here they are
    leaving, so an `edit` grant back to them would undo the point of the pass.
    """
    leaver = _user(db_session, "member")
    heir = _user(db_session, "member")
    suite = _suite(db_session, leaver)
    db_session.commit()

    _offboard(client, leaver, new_owner_user_id=str(heir.id))

    shares = db_session.scalars(select(Share).where(Share.suite_id == suite.id)).all()
    assert [s.user_id for s in shares] == []


def test_a_deliberate_handover_can_keep_the_previous_owner_as_an_editor(
    client: TestClient, db_session: Any
) -> None:
    leaver = _user(db_session, "member")
    heir = _user(db_session, "member")
    suite = _suite(db_session, leaver)
    db_session.commit()

    _offboard(client, leaver, new_owner_user_id=str(heir.id), keep_previous_owner_access=True)

    share = db_session.scalars(
        select(Share).where(Share.suite_id == suite.id, Share.user_id == leaver.id)
    ).one()
    assert share.permission == "edit"


def test_a_step_that_cannot_run_is_reported_with_its_reason(
    client: TestClient, db_session: Any
) -> None:
    """Silence would read as "membership withdrawn" on the one question the
    receipt exists to answer.
    """
    leaver = _user(db_session, "member")
    db_session.commit()

    receipt = _offboard(client, leaver).json()

    steps = {entry["step"]: entry["reason"] for entry in receipt["skipped"]}
    assert steps["transfer_suites"] == "this user owns no suites"
    assert "nothing to withdraw" in steps["remove_membership"]
    assert receipt["membership_removed"] is False


def test_an_env_listed_address_is_skipped_rather_than_silently_left_behind(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaver = _user(db_session, "member", email=f"env-{uuid.uuid4().hex[:6]}@example.com")
    member = _member_row(db_session, leaver)
    db_session.commit()
    monkeypatch.setenv("OIDC_ALLOWED_EMAILS", leaver.email)
    get_settings.cache_clear()

    receipt = _offboard(client, leaver).json()

    assert receipt["membership_removed"] is False
    reason = next(
        entry["reason"] for entry in receipt["skipped"] if entry["step"] == "remove_membership"
    )
    assert "OIDC_ALLOWED_EMAILS" in reason
    # The row is deliberately left: removing it would report a withdrawal that did
    # not happen, since the allowlist admits on its own.
    assert db_session.get(WorkspaceMember, member.id) is not None


def test_authored_history_survives_the_offboarding(client: TestClient, db_session: Any) -> None:
    """Offboarding is not erasure. Provenance columns are the record of who did
    what, so the connection they authored, the check version they last edited and
    the runs and results under their suite must all still be there afterwards.
    """
    leaver = _user(db_session, "member")
    heir = _user(db_session, "member")
    suite = _suite(db_session, leaver)
    connection_id = suite.connection_id
    check = Check(
        suite_id=suite.id,
        name="rows",
        expectation_type="expect_table_row_count_to_be_between",
        config={"min_value": 1},
    )
    db_session.add(check)
    db_session.flush()
    version = CheckVersion(
        check_id=check.id,
        version_no=1,
        name=check.name,
        kind="expectation",
        expectation_type=check.expectation_type,
        config=check.config,
        changed_by=leaver.id,
    )
    db_session.add(version)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    db_session.add(run)
    db_session.flush()
    result = Result(run_id=run.id, check_id=check.id, status="pass")
    db_session.add(result)
    db_session.commit()

    resp = _offboard(client, leaver, new_owner_user_id=str(heir.id))
    assert resp.status_code == 200

    db_session.expire_all()
    # The user row itself survives — erasure is a different, deliberate act.
    assert db_session.get(User, leaver.id) is not None
    assert db_session.get(Connection, connection_id).created_by == leaver.id
    assert db_session.get(CheckVersion, version.id).changed_by == leaver.id
    assert db_session.get(Check, check.id) is not None
    assert db_session.get(Run, run.id) is not None
    assert db_session.get(Result, result.id) is not None


# ── The trail ────────────────────────────────────────────────────────────────


def test_the_pass_records_a_receipt_beside_each_step_s_own_event(
    client: TestClient, db_session: Any
) -> None:
    leaver = _user(db_session, "member")
    heir = _user(db_session, "member")
    suite = _suite(db_session, leaver)
    _member_row(db_session, leaver)
    db_session.commit()
    api_key_service.create_key(db_session, leaver, name="laptop")

    _offboard(client, leaver, new_owner_user_id=str(heir.id))

    actions = [
        row.action
        for row in db_session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at))
    ]
    for step in ("suite.transfer", "api_key.revoke", "workspace_member.remove", "user.offboard"):
        assert step in actions, actions

    event = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "user.offboard")).one()
    assert event.after["transferred_suite_ids"] == [str(suite.id)]
    assert event.after["api_keys_revoked"] == 1
    assert event.after["membership_removed"] is True


def test_the_revoke_is_attributed_to_the_admin_not_to_the_departing_user(
    client: TestClient, db_session: Any
) -> None:
    """A trail saying the leaver revoked their own key on the way out is worse
    than no trail — it names the wrong actor for a privileged act.
    """
    leaver = _user(db_session, "member")
    db_session.commit()
    api_key_service.create_key(db_session, leaver, name="laptop")

    _offboard(client, leaver)

    revokes = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "api_key.revoke")
    ).all()
    assert len(revokes) == 1
    assert revokes[0].actor_user_id != leaver.id
