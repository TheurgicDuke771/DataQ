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


# ── the reverse linking direction (ADR 0032 decision 6, found in review of #1134) ──
#
# `otp_service.resolve_or_create_user` resolves an OTP sign-in onto an existing AAD
# row. These cover the direction that was MISSING: an AAD sign-in for an address
# that already has an OTP-provisioned row (`aad_object_id IS NULL`). Unfixed, that
# collided with `uq_users_email_lower` and produced a PERMANENT 409 on every future
# Azure sign-in for that person — with a message blaming "another account", which
# was in fact their own.


def test_an_aad_signin_ADOPTS_an_existing_otp_row(db_session: Any) -> None:
    """One human, two authenticators, one row — in both directions.

    Without the claim, this is the 409 that locks an AAD identity out of the
    account it should be linking to.
    """
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
    assert resolved.last_seen_at is not None
    assert db_session.query(User).filter(User.email.ilike(email)).count() == 1


def test_the_adoption_is_case_insensitive_like_the_index(db_session: Any) -> None:
    """The collision is raised by `lower(email)`, so the claim must match on the
    same normalization — otherwise it never finds the row it is meant to adopt and
    the 409 comes straight back.

    This is the REALISTIC shape, and the one that makes the fix necessary at all:
    OTP stores a normalized address (`otp_service.normalize_email`) while AAD
    stores the claim verbatim (`_extract_claims`), so the two rows for one human
    routinely differ in case and *only* in case. Whitespace is deliberately not
    exercised: the index cannot express the strip half, and — as migration
    `7d25617cfaf0` records — no writer produces a padded address.
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
    accumulated must still be there after the AAD sign-in."""
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
    """The narrowness of the claim is the security property.

    Adoption applies ONLY to a NULL `aad_object_id`. A row already carrying another
    directory identity is the genuine conflict #1131 exists for — two humans, or one
    human with two tenant identities, on one mailbox — and must still need an
    operator. A claim that dropped the `IS NULL` predicate would let any AAD
    identity take over any account by presenting its email address.
    """
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
    409."""
    local = uuid.uuid4().hex[:10]
    email = f"person.{local}@example.com"
    db_session.add(User(id=uuid.uuid4(), aad_object_id=None, email=email))
    db_session.commit()

    first = _upsert_user(db_session, aad_object_id=f"oid-{local}", email=email, display_name="A")
    second = _upsert_user(db_session, aad_object_id=f"oid-{local}", email=email, display_name="B")

    assert first.id == second.id
    assert second.display_name == "B"
    assert db_session.query(User).filter(User.email.ilike(email)).count() == 1


def test_the_session_is_usable_after_an_adoption(db_session: Any) -> None:
    """The claim runs after a rollback of the failed INSERT. If it left the session
    in a failed transaction, the request would die on its next statement — the same
    trap `test_the_session_is_usable_after_the_conflict` guards for the 409 path."""
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
    """Two AAD sign-ins for the same new identity racing for one unlinked row.

    The `IS NULL` predicate makes the claim atomic, so exactly one wins. The loser
    must then find the row through the ordinary ON CONFLICT path — NOT fall through
    to a 409, which would be a spurious lockout produced purely by concurrency.

    Simulated by claiming the row from underneath the call, between its failed
    INSERT and its claim attempt.
    """
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
