"""A case-colliding email must 409 at sign-in, never 500 (#735, CONTRIBUTING rule 32)."""

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
from backend.app.services import user_service


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
    """The message rides the HTTP error envelope, which the log redactor does not touch."""
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
    """The rollback must actually happen — otherwise the request dies later anyway."""
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
    """End-to-end: `GET /me` under the dev-bypass authenticator."""
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
    """Guards the obvious over-correction: the happy path must be untouched."""
    first = client.get("/api/v1/me")
    assert first.status_code == 200, first.text
    second = client.get("/api/v1/me")
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["aad_object_id"] == DEV_BYPASS_AAD_OID


# ── the reverse linking direction (ADR 0032 decision 6, found in review of #1134) ──
# `otp_service.resolve_or_create_user` resolves an OTP sign-in onto an existing AAD row.


def test_an_aad_signin_ADOPTS_an_existing_otp_row(db_session: Any) -> None:
    """One human, two authenticators, one row — in both directions."""
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    otp_row = User(id=uuid.uuid4(), aad_object_id=None, email=email)
    db_session.add(otp_row)
    db_session.commit()

    resolved = _upsert_user(
        db_session, aad_object_id=f"oid-{local}", email=email, display_name="Person"
    )

    assert resolved.id == otp_row.id, "the AAD sign-in forked a second row for one human"
    assert resolved.aad_object_id == f"oid-{local}"
    assert resolved.display_name == "Person"
    assert resolved.display_name_override is False
    assert resolved.last_seen_at is not None
    assert db_session.query(User).filter(User.email.ilike(email)).count() == 1


def test_an_aad_signin_does_not_overwrite_a_pre_set_otp_display_name(db_session: Any) -> None:
    """The other direction of the same invariant, at the `_claim_unlinked_user` link itself: a
    human who signed in with an emailed code first and already set their own name via `PATCH
    /me` — BEFORE ever touching Azure AD — must not have it silently replaced by whatever the
    AAD claim says the moment the two identities link.
    """
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    otp_row = User(id=uuid.uuid4(), aad_object_id=None, email=email)
    db_session.add(otp_row)
    db_session.commit()
    user_service.update_display_name(db_session, otp_row, "Set Before AAD Ever Ran")

    resolved = _upsert_user(
        db_session, aad_object_id=f"oid-{local}", email=email, display_name="AAD Claim Name"
    )

    assert resolved.id == otp_row.id
    assert resolved.display_name == "Set Before AAD Ever Ran"
    assert resolved.display_name_override is True


def test_the_adoption_is_case_insensitive_like_the_index(db_session: Any) -> None:
    """The collision is raised by `lower(email)`, so the claim must match on the
    same normalization — otherwise it never finds the row it is meant to adopt and
    the 409 comes straight back.
    """
    local = uuid.uuid4().hex[:10]
    otp_row = User(id=uuid.uuid4(), aad_object_id=None, email=f"person.{local}@example.com")
    db_session.add(otp_row)
    db_session.commit()

    resolved = _upsert_user(
        db_session,
        aad_object_id=f"oid-{local}",
        email=f"Person.{local}@Example.COM",
        display_name=None,
    )
    assert resolved.id == otp_row.id
    assert (
        db_session.query(User).filter(User.email.ilike(f"person.{local}@example.com")).count() == 1
    )


def test_grants_and_pats_survive_the_adoption(db_session: Any) -> None:
    """The whole point of linking rather than forking: what the OTP identity
    accumulated must still be there after the AAD sign-in.
    """
    from backend.app.services import api_key_service

    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    otp_row = User(id=uuid.uuid4(), aad_object_id=None, email=email)
    db_session.add(otp_row)
    db_session.commit()
    _, pat = api_key_service.create_key(db_session, otp_row, name="minted-as-otp-user")

    resolved = _upsert_user(
        db_session, aad_object_id=f"oid-{local}", email=email, display_name=None
    )

    assert api_key_service.resolve_token(db_session, pat).id == resolved.id


def test_a_row_with_a_DIFFERENT_object_id_is_STILL_a_409(db_session: Any) -> None:
    """The narrowness of the claim is the security property."""
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    db_session.add(User(id=uuid.uuid4(), aad_object_id=f"oid-first-{local}", email=email))
    db_session.commit()

    with pytest.raises(IdentityConflictError) as caught:
        _upsert_user(
            db_session, aad_object_id=f"oid-second-{local}", email=email, display_name=None
        )
    assert caught.value.status_code == 409
    assert local not in caught.value.message


def test_a_second_aad_signin_after_adoption_is_an_ordinary_update(db_session: Any) -> None:
    """Guards the obvious over-correction: once claimed, the row must behave like
    any other AAD row — an UPDATE through the ON CONFLICT target, not a re-claim
    (which would silently succeed against a row that is no longer NULL) and not a
    409.
    """
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    db_session.add(User(id=uuid.uuid4(), aad_object_id=None, email=email))
    db_session.commit()

    first = _upsert_user(db_session, aad_object_id=f"oid-{local}", email=email, display_name="A")
    second = _upsert_user(db_session, aad_object_id=f"oid-{local}", email=email, display_name="B")

    assert first.id == second.id
    # The property this test exists to guard — ordinary update, not a re-claim, not a 409, exactly
    # one row — holds regardless of the display_name direction.
    assert second.display_name == "B"
    assert second.display_name_override is False
    assert db_session.query(User).filter(User.email.ilike(email)).count() == 1


def test_a_second_aad_signin_after_adoption_respects_an_override(db_session: Any) -> None:
    """Same ordinary-update path as above, opposite display_name direction:
    once `PATCH /me` (#1139) has set an override on the row, a second AAD
    sign-in's claim must NOT overwrite it.
    """
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    db_session.add(User(id=uuid.uuid4(), aad_object_id=None, email=email))
    db_session.commit()

    first = _upsert_user(db_session, aad_object_id=f"oid-{local}", email=email, display_name="A")
    user_service.update_display_name(db_session, first, "Self-Service Override")

    second = _upsert_user(db_session, aad_object_id=f"oid-{local}", email=email, display_name="B")

    assert first.id == second.id
    assert second.display_name == "Self-Service Override"
    assert second.display_name_override is True


def test_the_session_is_usable_after_an_adoption(db_session: Any) -> None:
    """The claim runs after a rollback of the failed INSERT. If it left the session
    in a failed transaction, the request would die on its next statement — the same
    trap `test_the_session_is_usable_after_the_conflict` guards for the 409 path.
    """
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    db_session.add(User(id=uuid.uuid4(), aad_object_id=None, email=email))
    db_session.commit()

    _upsert_user(db_session, aad_object_id=f"oid-{local}", email=email, display_name=None)

    survivor = _upsert_user(
        db_session,
        aad_object_id=f"oid-unrelated-{local}",
        email=f"unrelated.{local}@example.com",
        display_name=None,
    )
    assert survivor.id is not None


def test_losing_the_race_to_claim_an_unlinked_row_still_resolves(db_session: Any) -> None:
    """Two AAD sign-ins for the same new identity racing for one unlinked row."""
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    otp_row = User(id=uuid.uuid4(), aad_object_id=None, email=email)
    db_session.add(otp_row)
    db_session.commit()

    real_rollback = db_session.rollback
    fired = {"done": False}

    def _rollback_then_let_the_winner_in() -> None:
        real_rollback()
        if not fired["done"]:
            fired["done"] = True
            # The concurrent sign-in claims the row first.
            db_session.query(User).filter(User.id == otp_row.id).update(
                {"aad_object_id": f"oid-{local}"}
            )
            db_session.commit()

    db_session.rollback = _rollback_then_let_the_winner_in
    try:
        resolved = _upsert_user(
            db_session, aad_object_id=f"oid-{local}", email=email, display_name="Late"
        )
    finally:
        db_session.rollback = real_rollback

    assert resolved.id == otp_row.id
    assert db_session.query(User).filter(User.email.ilike(email)).count() == 1
