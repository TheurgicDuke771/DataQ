"""A case-colliding email must 409 at sign-in, never 500 (#735, CONTRIBUTING rule 32).

`_upsert_user` keys its `ON CONFLICT` on `aad_object_id`, so the `uq_users_email_lower`
index #735 added introduces a conflict target the upsert does **not** handle: a
*different* AAD object id arriving with an email that case-collides with an existing
row (a new tenant identity for someone who already has a row, or an email-claim change
onto an address another row holds). Unhandled, that is a raw `IntegrityError` — a 500
on **every single login** for that user, with no path out except a DBA.

These tests go through the real seam against real Postgres: the index is the thing
under test and only Postgres has it.

Skips without TEST_DATABASE_URL (see conftest's resolution order).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import (
    DEV_BYPASS_AAD_OID,
    DEV_BYPASS_EMAIL,
    IdentityConflictError,
    _upsert_user,
)
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_upsert_raises_identity_conflict_for_a_case_colliding_email(db_session: Any) -> None:
    """Two distinct object ids, one mailbox differing only in case → 409, not 500."""
    local = uuid.uuid4().hex[:10]
    first = _upsert_user(
        db_session,
        aad_object_id=f"oid-a-{local}",
        email=f"Person.{local}@Example.COM",
        display_name="First",
    )
    assert first.id is not None

    with pytest.raises(IdentityConflictError) as caught:
        _upsert_user(
            db_session,
            aad_object_id=f"oid-b-{local}",
            email=f"person.{local}@example.com",
            display_name="Second",
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "identity_conflict"


def test_the_conflict_message_never_names_the_colliding_address(db_session: Any) -> None:
    """The message rides the HTTP error envelope, which the log redactor does not touch.

    Naming the address would tell any caller — including one who just guessed an
    email — who else holds an account in the workspace.
    """
    local = uuid.uuid4().hex[:10]
    existing = f"Victim.{local}@Example.COM"
    _upsert_user(db_session, aad_object_id=f"oid-a-{local}", email=existing, display_name=None)

    with pytest.raises(IdentityConflictError) as caught:
        _upsert_user(
            db_session,
            aad_object_id=f"oid-b-{local}",
            email=existing.lower(),
            display_name=None,
        )
    message = caught.value.message
    assert local not in message
    assert existing.lower() not in message.lower()
    assert caught.value.detail == {}


def test_the_session_is_usable_after_the_conflict(db_session: Any) -> None:
    """The rollback must actually happen — otherwise the request dies later anyway.

    Without `db.rollback()` the session stays in a failed transaction and the very
    next statement raises `PendingRollbackError`, so a "handled" 409 would still
    take the request down.
    """
    local = uuid.uuid4().hex[:10]
    _upsert_user(
        db_session, aad_object_id=f"oid-a-{local}", email=f"S.{local}@Ex.com", display_name=None
    )
    with pytest.raises(IdentityConflictError):
        _upsert_user(
            db_session, aad_object_id=f"oid-b-{local}", email=f"s.{local}@ex.com", display_name=None
        )

    # A perfectly ordinary write must still work on this session.
    survivor = _upsert_user(
        db_session,
        aad_object_id=f"oid-c-{local}",
        email=f"unrelated.{local}@example.com",
        display_name=None,
    )
    assert survivor.id is not None


def test_login_returns_409_not_500_through_the_real_seam(
    client: TestClient, db_session: Any
) -> None:
    """End-to-end: `GET /me` under the dev-bypass authenticator.

    The bypass upserts a FIXED (oid, email) pair, so seeding a different oid holding
    the case-variant of that email reproduces the production shape exactly — the
    authenticator resolves a user on every request, and this is the request where it
    cannot. A 500 here would be an unhandled `IntegrityError` reaching the client.
    """
    db_session.add(
        User(aad_object_id=f"squatter-{uuid.uuid4().hex[:8]}", email=DEV_BYPASS_EMAIL.upper())
    )
    db_session.commit()

    resp = client.get("/api/v1/me")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"]["code"] == "identity_conflict"
    assert DEV_BYPASS_EMAIL not in body["error"]["message"].lower()
    # …and specifically NOT the unhandled-exception shape.
    assert resp.status_code != 500


def test_the_same_object_id_signing_in_again_still_just_updates(client: TestClient) -> None:
    """Guards the obvious over-correction: the happy path must be untouched.

    `on_conflict_do_update` on `aad_object_id` is what makes a repeat sign-in an
    UPDATE; a fix that turned every second login into a 409 would also make these
    tests' first assertion pass.
    """
    first = client.get("/api/v1/me")
    assert first.status_code == 200, first.text
    second = client.get("/api/v1/me")
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["aad_object_id"] == DEV_BYPASS_AAD_OID
