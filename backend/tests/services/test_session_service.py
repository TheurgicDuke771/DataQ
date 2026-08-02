"""Sessions must be revocable and expirable AT THE SEAM, not merely on paper (#734).

ADR 0032 decision 3 is explicit that stored `expires_at` / `revoked_at` columns do
not count: what has to hold is that the *next request* carrying that token is a
uniform 401. So every test here goes through `resolve_token`, the function the auth
seam actually calls, rather than asserting on the row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.core.config import Settings
from backend.app.db.models import User, UserSession
from backend.app.services import session_service as svc


def _user(db: Any, email: str | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        aad_object_id=uuid.uuid4().hex,
        email=email or f"{uuid.uuid4().hex[:10]}@sessions.io",
    )
    db.add(user)
    db.commit()
    return user


def test_a_minted_token_carries_the_prefix_and_resolves_to_its_owner(db_session: Any) -> None:
    user = _user(db_session)
    row, token = svc.create_session(db_session, user)

    assert token.startswith(svc.TOKEN_PREFIX)
    # ~256 bits of url-safe randomness after the prefix, not a short id.
    assert len(token) - len(svc.TOKEN_PREFIX) >= 40
    assert svc.resolve_token(db_session, token).id == user.id
    # The plaintext is NEVER stored — only its digest.
    assert row.token_hash != token
    assert token not in row.token_hash


def test_the_plaintext_is_not_recoverable_from_the_row(db_session: Any) -> None:
    """A verifier secret: nothing in the database can reconstruct the cookie."""
    user = _user(db_session)
    row, token = svc.create_session(db_session, user)
    stored = {str(v) for v in (row.id, row.user_id, row.token_hash, row.expires_at, row.created_at)}
    assert not any(token in value for value in stored)


def test_expiry_is_enforced_on_resolve_not_just_stored(db_session: Any) -> None:
    """The AC that matters: an expired session is a 401 on the NEXT resolve.

    Written by ageing the row rather than by patching the clock, because what has
    to hold is that `resolve_token` compares against `now` at all — a version that
    read the column and never compared it would pass any clock-patching test.
    """
    user = _user(db_session)
    row, token = svc.create_session(db_session, user)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(svc.SessionAuthError) as caught:
        svc.resolve_token(db_session, token)
    assert caught.value.status_code == 401


def test_revocation_is_enforced_on_resolve(db_session: Any) -> None:
    user = _user(db_session)
    _, token = svc.create_session(db_session, user)
    assert svc.resolve_token(db_session, token).id == user.id  # works before logout

    assert svc.revoke(db_session, token) is True
    with pytest.raises(svc.SessionAuthError):
        svc.resolve_token(db_session, token)


def test_revoke_is_idempotent_and_silent_for_an_unknown_token(db_session: Any) -> None:
    user = _user(db_session)
    _, token = svc.create_session(db_session, user)
    assert svc.revoke(db_session, token) is True
    assert svc.revoke(db_session, token) is False  # second call is a no-op, not an error
    # A token that never existed: logout must not raise (the browser still needs
    # its cookie cleared).
    assert svc.revoke(db_session, svc.TOKEN_PREFIX + "never-existed") is False


def test_the_fk_is_what_makes_an_orphan_unreachable(db_session: Any) -> None:
    """The first half of the orphan story: the database refuses to create one."""
    db_session.add(
        UserSession(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),  # nobody
            token_hash=svc._hash(svc.TOKEN_PREFIX + "orphan"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    with pytest.raises(Exception):  # noqa: B017 — the FK violation IS the assertion
        db_session.commit()
    db_session.rollback()


def test_an_orphaned_session_still_fails_closed(db_session: Any) -> None:
    """The second half: if one ever existed anyway, it must 401 — not 500, and
    certainly not authenticate as `None`.

    The FK above makes this state unconstructible through the database, so the
    only way to exercise the guard is to hand `resolve_token` a session object
    whose `get(User, …)` misses. That substitutes the *database*, not the function
    under test — `resolve_token`'s own logic runs unmodified. A guard nothing can
    reach is a guard nothing proves, and this one is the difference between a
    401 and an `AttributeError` on `None.id` three layers up.
    """
    user = _user(db_session)
    _, token = svc.create_session(db_session, user)

    class _UserlessSession:
        def __init__(self, real: Any) -> None:
            self._real = real

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return self._real.execute(*args, **kwargs)

        def get(self, *_args: Any, **_kwargs: Any) -> None:
            return None  # the user row vanished between the two reads

    with pytest.raises(svc.SessionAuthError) as caught:
        svc.resolve_token(_UserlessSession(db_session), token)  # type: ignore[arg-type]
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        "dq_sess_completely-made-up",
        "",
        "dq_live_wrong-credential-type",
        "not-even-close",
    ],
)
def test_every_failure_mode_is_the_same_401_and_the_same_message(
    db_session: Any, token: str
) -> None:
    """One message for unknown / expired / revoked / orphaned.

    Distinguishing them would confirm to a probing caller that a session exists —
    the `ApiKeyAuthError` discipline, restated for a credential that arrives from
    a browser rather than a script.
    """
    user = _user(db_session)
    _, live = svc.create_session(db_session, user)
    svc.revoke(db_session, live)

    with pytest.raises(svc.SessionAuthError) as unknown:
        svc.resolve_token(db_session, token)
    with pytest.raises(svc.SessionAuthError) as revoked:
        svc.resolve_token(db_session, live)

    assert unknown.value.message == revoked.value.message
    assert unknown.value.code == revoked.value.code == "invalid_session"
    assert unknown.value.status_code == revoked.value.status_code == 401


def test_the_ttl_comes_from_settings(db_session: Any) -> None:
    user = _user(db_session)
    row, _ = svc.create_session(db_session, user, settings=Settings(auth_session_ttl_hours=1))
    lifetime = row.expires_at - datetime.now(UTC)
    assert timedelta(minutes=50) < lifetime <= timedelta(hours=1)


def test_two_sessions_for_one_user_are_independent(db_session: Any) -> None:
    """Signing out on one device must not sign the user out everywhere.

    (And, more importantly, revoking by token must not revoke by user id — a
    plausible "simplification" that would silently change the product's behaviour.)
    """
    user = _user(db_session)
    _, phone = svc.create_session(db_session, user)
    _, laptop = svc.create_session(db_session, user)

    svc.revoke(db_session, phone)
    with pytest.raises(svc.SessionAuthError):
        svc.resolve_token(db_session, phone)
    assert svc.resolve_token(db_session, laptop).id == user.id


def test_the_token_never_appears_in_a_log_line(db_session: Any, capsys: Any) -> None:
    """Prefix-only logging, through the REAL logging pipeline (#849's lesson)."""
    import io
    import logging

    from backend.app.core.logging import configure_logging

    user = _user(db_session)
    _, token = svc.create_session(db_session, user)

    configure_logging()
    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    original = handler.stream  # type: ignore[attr-defined]
    handler.stream = buffer  # type: ignore[attr-defined]
    try:
        svc.resolve_token(db_session, token)  # success path logs
        with pytest.raises(svc.SessionAuthError):
            svc.resolve_token(db_session, svc.TOKEN_PREFIX + "bad-token-value")  # failure path
    finally:
        handler.stream = original  # type: ignore[attr-defined]

    emitted = buffer.getvalue()
    assert emitted, "nothing was emitted — the assertions below would be vacuous"
    assert token not in emitted
    assert token[len(svc.TOKEN_PREFIX) :][:12] not in emitted
