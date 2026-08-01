"""`users` identity semantics after #735 step 1 (ADR 0032 decision 6).

The schema half of email-OTP sign-in: `aad_object_id` may be NULL, and email is
unique **case-insensitively**. These are database guarantees — every assertion
here goes through real Postgres via `db_session`; a mock could not distinguish
"the index exists" from "the index is on plain `email`".

Skips without TEST_DATABASE_URL (see conftest's resolution order).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.app.core.config import get_settings
from backend.app.db.models import User


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@example.com"


def test_a_user_persists_without_an_aad_object_id(db_session: Any) -> None:
    """An OTP-provisioned identity has no Azure AD object id."""
    email = _email("otp")
    db_session.add(User(aad_object_id=None, email=email))
    db_session.flush()

    stored = db_session.scalars(select(User).where(User.email == email)).one()
    assert stored.aad_object_id is None
    assert stored.id is not None


def test_many_users_may_share_a_null_aad_object_id(db_session: Any) -> None:
    """The unique constraint on `aad_object_id` is KEPT, and that is safe.

    Postgres treats NULLs as distinct in a unique constraint, so an unlimited
    number of OTP users coexist while AAD users stay one-row-per-object-id. If
    this ever failed, `otp` mode would support exactly one user.
    """
    db_session.add_all(
        [
            User(aad_object_id=None, email=_email("otp-a")),
            User(aad_object_id=None, email=_email("otp-b")),
            User(aad_object_id=None, email=_email("otp-c")),
        ]
    )
    db_session.flush()

    assert (
        db_session.scalar(
            select(func.count()).select_from(User).where(User.aad_object_id.is_(None))
        )
        == 3
    )


def test_aad_object_id_is_still_unique_when_present(db_session: Any) -> None:
    """Relaxing NOT NULL must not have relaxed uniqueness for real AAD users."""
    oid = f"oid-{uuid.uuid4().hex[:12]}"
    db_session.add_all(
        [
            User(aad_object_id=oid, email=_email("aad-a")),
            User(aad_object_id=oid, email=_email("aad-b")),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_email_uniqueness_is_case_insensitive(db_session: Any) -> None:
    """`Foo@X.com` and `foo@x.com` are ONE human — the whole point of #735.

    Emails are stored verbatim from the JWT claims (`core/auth.py`
    `_extract_claims` does no normalization), so without `uq_users_email_lower`
    these two would be separate rows and the OTP↔AAD linking rule in ADR 0032
    decision 6 would fragment grants, shares and PATs across them.
    """
    local = uuid.uuid4().hex[:10]
    db_session.add(User(aad_object_id=f"oid-{local}", email=f"Foo.{local}@Example.COM"))
    db_session.flush()

    db_session.add(User(aad_object_id=None, email=f"foo.{local}@example.com"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_email_uniqueness_still_separates_genuinely_different_addresses(db_session: Any) -> None:
    """Guards the opposite mistake: an index so broad it merges distinct people."""
    local = uuid.uuid4().hex[:10]
    db_session.add_all(
        [
            User(aad_object_id=None, email=f"a.{local}@example.com"),
            User(aad_object_id=None, email=f"b.{local}@example.com"),
        ]
    )
    db_session.flush()  # must not raise


def test_index_normalization_matches_the_admin_allowlist_rule(
    db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    """One normalization rule across the identity surface.

    `Settings.is_admin_email` (`core/config.py`, the `is_admin_email` /
    `workspace_admin_email_set` pair) normalizes with **strip + lower**; the
    index normalizes with `lower(...)`. The lower-casing halves must agree, or a
    mailbox could be one identity for sign-in and a different one for the
    workspace-admin check — precisely the fragmentation ADR 0032 decision 6
    forbids.

    The *strip* half is deliberately not in the index (no writer produces
    surrounding whitespace and an expression index cannot be relied on to
    normalize what the application never stores); it stays application-level, so
    this test pins the overlap that does exist.
    """
    email = f"Mixed.Case.{uuid.uuid4().hex[:8]}@Example.COM"
    db_session.add(User(aad_object_id=None, email=email))
    db_session.flush()

    normalized_by_config = email.strip().lower()
    normalized_by_index = db_session.scalar(
        select(func.lower(User.email)).where(User.email == email)
    )
    assert normalized_by_index == normalized_by_config

    # The allowlist is written in the normalized form the index produces, and the
    # admin check still matches the row's verbatim mixed-case address.
    make_workspace_admin(normalized_by_index)
    assert get_settings().is_admin_email(email) is True
    # …and it is genuinely a match on THIS address, not a permissive check.
    assert get_settings().is_admin_email(f"other-{email}") is False
