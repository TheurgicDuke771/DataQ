"""Workspace-admin suite writes — revoke any grant, transfer ownership, delete
any suite (#1698).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import AuditEvent, Check, Connection, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import admin_suite_service


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


def _share(db_session: Any, suite: Suite, user: User, permission: str = "view") -> Share:
    share = Share(suite_id=suite.id, user_id=user.id, permission=permission)
    db_session.add(share)
    db_session.commit()
    return share


def _events(db_session: Any, action: str) -> list[AuditEvent]:
    return list(db_session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


# ── revoke any grant ─────────────────────────────────────────────────────────


def test_an_admin_revokes_a_grant_on_a_suite_it_does_not_own(
    client: TestClient, db_session: Any
) -> None:
    """The dev-bypass caller is a workspace admin (#741) and owns none of this."""
    owner = _user(db_session, "member")
    grantee = _user(db_session, "member")
    suite = _suite(db_session, owner)
    share = _share(db_session, suite, grantee, "edit")

    resp = client.delete(f"/api/v1/admin/suites/{suite.id}/access/{share.id}")

    assert resp.status_code == 204, resp.text
    assert db_session.get(Share, share.id) is None


def test_the_revoke_records_the_admin_override(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    share = _share(db_session, suite, _user(db_session, "member"))

    client.delete(f"/api/v1/admin/suites/{suite.id}/access/{share.id}")

    events = _events(db_session, "suite_access.revoke")
    assert len(events) == 1
    assert events[0].after == {"revoked": True, "admin_override": True}
    # The revoked grant survives only in the payload — the row is gone.
    assert events[0].before is not None
    assert events[0].before["permission"] == "view"


def test_a_grant_on_another_suite_is_not_revocable_through_this_suite(
    client: TestClient, db_session: Any
) -> None:
    """The path pairs the two ids, so a mismatched pair must 404 rather than
    revoking whatever grant the id happens to name.
    """
    owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    other = _suite(db_session, owner)
    share = _share(db_session, other, _user(db_session, "member"))

    resp = client.delete(f"/api/v1/admin/suites/{suite.id}/access/{share.id}")

    assert resp.status_code == 404
    assert db_session.get(Share, share.id) is not None


def test_an_unknown_suite_is_404(client: TestClient) -> None:
    resp = client.delete(f"/api/v1/admin/suites/{uuid.uuid4()}/access/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_non_admin_cannot_revoke_a_real_grant(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    """A REAL suite and a REAL grant: a fabricated id 404s before the gate is
    reached, and the sweep would then pass with the gate deleted.
    """
    actor, headers = as_role(role)
    suite = _suite(db_session, actor)  # the caller even OWNS it
    share = _share(db_session, suite, _user(db_session, "member"))

    resp = client.delete(f"/api/v1/admin/suites/{suite.id}/access/{share.id}", headers=headers)

    assert resp.status_code == 403
    assert db_session.get(Share, share.id) is not None, "a 403 must not have written anything"


# ── transfer ownership ───────────────────────────────────────────────────────


def test_a_transfer_moves_ownership_and_leaves_the_previous_owner_an_edit_grant(
    client: TestClient, db_session: Any
) -> None:
    owner = _user(db_session, "member")
    new_owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    db_session.commit()

    resp = client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(new_owner.id)},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["previous_owner_id"] == str(owner.id)
    assert body["new_owner_id"] == str(new_owner.id)
    assert body["previous_owner_permission"] == "edit"
    db_session.refresh(suite)
    assert suite.created_by == new_owner.id
    kept = db_session.scalars(
        select(Share).where(Share.suite_id == suite.id, Share.user_id == owner.id)
    ).one()
    assert kept.permission == "edit"


def test_keep_previous_owner_access_false_leaves_no_grant(
    client: TestClient, db_session: Any
) -> None:
    owner = _user(db_session, "member")
    new_owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    db_session.commit()

    resp = client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(new_owner.id), "keep_previous_owner_access": False},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["previous_owner_permission"] is None
    assert (
        db_session.scalars(
            select(Share).where(Share.suite_id == suite.id, Share.user_id == owner.id)
        ).first()
        is None
    )


def test_the_previous_owners_existing_share_is_removed_when_access_is_not_kept(
    client: TestClient, db_session: Any
) -> None:
    """ "Don't keep their access" must mean it, even when they already held a share
    for some other reason — otherwise offboarding leaves the grant it was run to remove.
    """
    owner = _user(db_session, "member")
    new_owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    stale = _share(db_session, suite, owner, "view")

    client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(new_owner.id), "keep_previous_owner_access": False},
    )

    assert db_session.get(Share, stale.id) is None


def test_the_new_owners_own_share_row_is_dropped(client: TestClient, db_session: Any) -> None:
    """An owner outranks every grant, and `grant_share` refuses to share a suite
    with its owner — a leftover row would render as a second, weaker grant.
    """
    owner = _user(db_session, "member")
    new_owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    share_id = _share(db_session, suite, new_owner, "view").id

    client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(new_owner.id)},
    )

    # The service deletes it in SQL, so the identity map still holds the old object.
    db_session.expire_all()
    assert db_session.scalars(select(Share).where(Share.id == share_id)).first() is None


def test_a_viewer_cannot_be_made_an_owner(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "member")
    viewer = _user(db_session, "viewer")
    suite = _suite(db_session, owner)
    db_session.commit()

    resp = client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(viewer.id)},
    )

    assert resp.status_code == 422
    assert "viewer" in resp.json()["error"]["message"]
    db_session.refresh(suite)
    assert suite.created_by == owner.id


def test_a_previous_owner_who_is_a_viewer_keeps_view_not_edit(
    client: TestClient, db_session: Any
) -> None:
    """A Viewer cannot hold `edit` (ADR 0033) — writing one would be a grant the
    ladder then silently clamps.
    """
    owner = _user(db_session, "viewer")
    new_owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    db_session.commit()

    resp = client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(new_owner.id)},
    )

    assert resp.json()["previous_owner_permission"] == "view"


def test_a_transfer_to_the_current_owner_is_409(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    db_session.commit()

    resp = client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(owner.id)},
    )

    assert resp.status_code == 409


def test_an_unknown_target_user_is_404(client: TestClient, db_session: Any) -> None:
    suite = _suite(db_session, _user(db_session, "member"))
    db_session.commit()
    resp = client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


def test_the_transfer_audit_carries_both_owners(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "member")
    new_owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    db_session.commit()

    client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(new_owner.id)},
    )

    events = _events(db_session, "suite.transfer")
    assert len(events) == 1
    assert events[0].before == {"id": str(suite.id), "owner_id": str(owner.id)}
    assert events[0].after == {
        "id": str(suite.id),
        "owner_id": str(new_owner.id),
        "previous_owner_permission": "edit",
        "admin_override": True,
    }


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_non_admin_cannot_transfer_a_real_suite(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    actor, headers = as_role(role)
    suite = _suite(db_session, actor)
    target = _user(db_session, "member")
    db_session.commit()

    resp = client.post(
        f"/api/v1/admin/suites/{suite.id}/transfer",
        json={"new_owner_user_id": str(target.id)},
        headers=headers,
    )

    assert resp.status_code == 403
    db_session.refresh(suite)
    assert suite.created_by == actor.id, "a 403 must not have written anything"


# ── admin delete ─────────────────────────────────────────────────────────────


def test_an_admin_deletes_a_suite_it_does_not_own(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    db_session.add(
        Check(
            suite_id=suite.id,
            name="rows",
            expectation_type="expect_table_row_count_to_be_between",
            config={"min_value": 1},
        )
    )
    db_session.commit()
    suite_id = suite.id

    resp = client.delete(f"/api/v1/admin/suites/{suite_id}")

    assert resp.status_code == 204, resp.text
    assert db_session.get(Suite, suite_id) is None


def test_the_delete_audit_carries_the_blast_radius(client: TestClient, db_session: Any) -> None:
    """The counts are the only surviving record of what the cascade destroyed."""
    owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    db_session.add(
        Check(
            suite_id=suite.id,
            name="rows",
            expectation_type="expect_table_row_count_to_be_between",
            config={"min_value": 1},
        )
    )
    db_session.commit()

    client.delete(f"/api/v1/admin/suites/{suite.id}")

    events = _events(db_session, "suite.delete")
    assert len(events) == 1
    after = events[0].after
    assert after is not None
    assert after["admin_override"] is True
    assert after["impact"]["checks"] == 1
    assert after["impact"]["runs"] == 0


def test_an_unknown_suite_delete_is_404(client: TestClient) -> None:
    assert client.delete(f"/api/v1/admin/suites/{uuid.uuid4()}").status_code == 404


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_non_admin_cannot_admin_delete_a_real_suite(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    actor, headers = as_role(role)
    suite = _suite(db_session, actor)
    db_session.commit()

    resp = client.delete(f"/api/v1/admin/suites/{suite.id}", headers=headers)

    assert resp.status_code == 403
    assert db_session.get(Suite, suite.id) is not None


# ── the access overview exposes the id the revoke needs ──────────────────────


def test_the_access_overview_carries_the_grant_id_only_for_share_rows(
    client: TestClient, db_session: Any
) -> None:
    """An implicit owner row is not a grant and cannot be revoked — a `grant_id`
    on it would render a Revoke action that always 404s.
    """
    owner = _user(db_session, "member")
    suite = _suite(db_session, owner)
    share = _share(db_session, suite, _user(db_session, "member"))

    rows = client.get("/api/v1/admin/access").json()
    by_permission = {r["permission"]: r for r in rows if r["suite_id"] == str(suite.id)}

    assert by_permission["owner"]["grant_id"] is None
    assert by_permission["view"]["grant_id"] == str(share.id)


# ── the service is callable directly, and revalidates ────────────────────────


def test_the_service_rejects_a_viewer_owner_without_a_router(db_session: Any) -> None:
    actor = _user(db_session, "admin")
    suite = _suite(db_session, _user(db_session, "member"))
    viewer = _user(db_session, "viewer")
    db_session.commit()

    with pytest.raises(admin_suite_service.SuiteTransferRejectedError):
        admin_suite_service.transfer_ownership(
            db_session, suite.id, new_owner_user_id=viewer.id, actor=actor
        )
