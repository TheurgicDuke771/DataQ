"""The four membership choke points — ADR 0043 decision 4 and its verification bar.

Every probe here uses a REAL identity that was admitted and then removed, per
credential kind: a fabricated id fails earlier for an accepted-looking reason and
a sweep built on one passes with the gate deleted.
"""

from __future__ import annotations

import inspect
import operator
import uuid
from typing import Annotated, Any

import pytest
from fastapi import Depends
from sqlalchemy.orm import Session
from starlette.requests import Request

from backend.app.core import auth as auth_mod
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.mcp import auth as mcp_auth
from backend.app.services import api_key_service, membership_service, otp_service, session_service
from backend.tests.support.membership_gates import (
    CREDENTIALS,
    SURFACES,
    Door,
    credentials_covered,
    surfaces_covered,
)

#: A deployment where dev bypass is not the selected mode, so the gates run.
_OIDC = Settings(
    environment="prod",
    auth_dev_bypass=False,
    oidc_issuer="https://example-idp.test",
    oidc_audience="dataq-client-id",
)

_OTP = Settings(
    environment="prod",
    auth_dev_bypass=False,
    auth_email_smtp_host="smtp.example.com",
    auth_email_username="dataq@example.com",
    auth_email_from="dataq@example.com",
    auth_email_password_secret_name="auth-email-password",
    auth_otp_allowed_domains="acme.io",
)


@pytest.fixture
def enforcing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the PROCESS out of dev-bypass mode.

    `session_service` and `api_key_service` resolve their own `get_settings()`,
    and the test process defaults to `AUTH_DEV_BYPASS=true`, which the exemption
    honours. Monkeypatching only `auth_mod._settings` leaves those two doors
    exempt, and a revocation test written that way passes with the gate deleted.
    """
    monkeypatch.setenv("AUTH_DEV_BYPASS", "false")
    get_settings.cache_clear()


@pytest.fixture
def otp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the OTP block in the PROCESS env, not only in a `Settings` object.

    `session_service` and `api_key_service` resolve their own `get_settings()`.
    A test that patches only a local `Settings` leaves those doors reading an
    allowlist-free deployment, which is a different question than the one it
    means to ask.
    """
    for name, value in {
        "AUTH_DEV_BYPASS": "false",
        "ENVIRONMENT": "prod",
        "AUTH_EMAIL_SMTP_HOST": "smtp.example.com",
        "AUTH_EMAIL_USERNAME": "dataq@example.com",
        "AUTH_EMAIL_FROM": "dataq@example.com",
        "AUTH_EMAIL_PASSWORD_SECRET_NAME": "auth-email-password",
        "AUTH_OTP_ALLOWED_DOMAINS": "acme.io",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _addr(prefix: str = "p") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _admin(db: Any) -> User:
    row = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=_addr("admin"), role="admin")
    db.add(row)
    db.flush()
    return row


def _request(authorization: str | None = None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _admit_then_remove(db: Any, admin: User, email: str) -> None:
    """Turn enforcement on WITH `email` admitted, then withdraw only that one.

    A second admin is seeded first so the last-admin guard does not stand in for
    the gate under test.
    """
    second = User(
        id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=_addr("second"), role="admin"
    )
    db.add(second)
    db.flush()
    added = membership_service.add_member(db, email=email, initial_role="member", actor=admin)
    membership_service.remove_member(db, added.member.id, actor=admin)


# ── choke point 1: `_upsert_user` (Azure AD + generic OIDC, REST and /mcp) ────


def test_azure_ad_sign_in_is_refused_for_a_removed_member(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Azure AD has never had an app-side gate at all; this is the one it gains."""
    monkeypatch.setattr(auth_mod, "_settings", Settings(environment="prod", auth_dev_bypass=False))
    admin = _admin(db_session)
    email = _addr("departed")
    _admit_then_remove(db_session, admin, email)

    with pytest.raises(DataQError) as exc:
        auth_mod._upsert_user(
            db_session, aad_object_id=uuid.uuid4().hex, email=email, display_name=None
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "not_a_workspace_member"


def test_an_admitted_azure_ad_identity_still_signs_in(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate's other side: enforcement on must not break a real member."""
    monkeypatch.setattr(auth_mod, "_settings", Settings(environment="prod", auth_dev_bypass=False))
    admin = _admin(db_session)
    email = _addr("kept")
    membership_service.add_member(db_session, email=email, initial_role="member", actor=admin)

    user = auth_mod._upsert_user(
        db_session, aad_object_id=uuid.uuid4().hex, email=email, display_name=None
    )
    assert user.email == email


def test_generic_oidc_admits_a_member_the_env_allowlist_does_not_name(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 7 from the other direction: the table admits, the allowlist only grants."""
    monkeypatch.setattr(
        auth_mod,
        "_settings",
        Settings(
            environment="prod",
            auth_dev_bypass=False,
            oidc_issuer="https://example-idp.test",
            oidc_audience="dataq-client-id",
            oidc_allowed_emails="someone-else@example.com",
        ),
    )
    admin = _admin(db_session)
    email = _addr("in-table")
    membership_service.add_member(db_session, email=email, initial_role="member", actor=admin)

    user = auth_mod._get_current_user_generic_oidc(
        _request(), {"sub": uuid.uuid4().hex, "email": email}, db_session
    )
    assert user.email == email


def test_generic_oidc_refuses_a_removed_member_even_on_an_open_allowlist(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    monkeypatch.setattr(auth_mod, "_settings", _OIDC)
    admin = _admin(db_session)
    email = _addr("departed")
    _admit_then_remove(db_session, admin, email)

    with pytest.raises(DataQError) as exc:
        auth_mod._get_current_user_generic_oidc(
            _request(), {"sub": uuid.uuid4().hex, "email": email}, db_session
        )
    assert exc.value.status_code == 403


# ── choke point 2: OTP browser sessions ──────────────────────────────────────


def test_a_live_session_cookie_stops_resolving_after_removal(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    """The gap this closes: the session was minted while admitted and, before
    ADR 0043, survived to its TTL no matter what.
    """
    monkeypatch.setattr(auth_mod, "_settings", _OTP)
    admin = _admin(db_session)
    email = _addr("departed")
    user = User(id=uuid.uuid4(), aad_object_id=None, email=email, role="member")
    db_session.add(user)
    db_session.flush()
    _, token = session_service.create_session(db_session, user)

    # Admitted: the cookie resolves.
    added = membership_service.add_member(
        db_session, email=email, initial_role="member", actor=admin
    )
    assert session_service.resolve_token(db_session, token).id == user.id

    membership_service.remove_member(db_session, added.member.id, actor=admin)
    with pytest.raises(DataQError) as exc:
        session_service.resolve_token(db_session, token)
    assert exc.value.status_code == 403


# ── choke point 3: PATs, on REST and /mcp ────────────────────────────────────


def _member_with_pat(db: Any, admin: User) -> tuple[User, str, uuid.UUID]:
    email = _addr("pat-owner")
    user = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email, role="member")
    db.add(user)
    db.flush()
    _, token = api_key_service.create_key(db, user, name="probe")
    added = membership_service.add_member(db, email=email, initial_role="member", actor=admin)
    return user, token, added.member.id


def test_a_live_pat_stops_resolving_after_removal(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    """The widest gap: a PAT is checked at mint and, before this, never again."""
    monkeypatch.setattr(auth_mod, "_settings", _OIDC)
    admin = _admin(db_session)
    user, token, member_id = _member_with_pat(db_session, admin)
    assert api_key_service.resolve_token(db_session, token).id == user.id

    membership_service.remove_member(db_session, member_id, actor=admin)
    with pytest.raises(DataQError) as exc:
        api_key_service.resolve_token(db_session, token)
    assert exc.value.status_code == 403


def test_the_same_pat_is_refused_on_the_rest_dependency(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    monkeypatch.setattr(auth_mod, "_settings", _OIDC)
    admin = _admin(db_session)
    _, token, member_id = _member_with_pat(db_session, admin)
    membership_service.remove_member(db_session, member_id, actor=admin)

    with pytest.raises(DataQError) as exc:
        auth_mod._get_current_user_generic_oidc(_request(f"Bearer {token}"), None, db_session)
    assert exc.value.status_code == 403


async def test_the_same_pat_is_refused_at_the_mcp_verifier(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    """At /mcp the denial is a 401 by construction: the verifier catches the
    resolver's DataQError and returns None, which fastmcp renders as an auth
    failure. ADR 0043 decision 4 records that asymmetry rather than hiding it.
    """
    from backend.app.db import session as db_session_mod

    monkeypatch.setattr(auth_mod, "_settings", _OIDC)
    monkeypatch.setattr(db_session_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    admin = _admin(db_session)
    _, token, member_id = _member_with_pat(db_session, admin)
    verifier = mcp_auth._PatOrJwtVerifier(None)
    assert await verifier.verify_token(token) is not None

    membership_service.remove_member(db_session, member_id, actor=admin)
    assert await verifier.verify_token(token) is None


# ── choke point 4: OTP eligibility, at both call sites ───────────────────────


def test_otp_eligibility_stops_for_a_removed_member(db_session: Any, enforcing_env: None) -> None:
    admin = _admin(db_session)
    email = _addr("otp-departed")
    added = membership_service.add_member(
        db_session, email=email, initial_role="member", actor=admin
    )
    assert otp_service.is_signup_eligible(db_session, email, _OTP) is True

    membership_service.remove_member(db_session, added.member.id, actor=admin)
    assert otp_service.is_signup_eligible(db_session, email, _OTP) is False


def test_removing_an_ENV_LISTED_address_does_not_revoke_it(
    db_session: Any, enforcing_env: None
) -> None:
    """The accepted cost the ADR names: env allowlists are grant-only, so an
    address the deployment config lists is re-admitted by it. Removing that one
    still takes an env edit and a restart.
    """
    admin = _admin(db_session)
    email = "someone@acme.io"  # on `AUTH_OTP_ALLOWED_DOMAINS`
    added = membership_service.add_member(
        db_session, email=email, initial_role="member", actor=admin
    )
    membership_service.remove_member(db_session, added.member.id, actor=admin)

    assert otp_service.env_signup_allowed(email, _OTP) is True
    assert otp_service.is_signup_eligible(db_session, email, _OTP) is True


def test_otp_admits_a_member_the_env_allowlist_does_not_name(
    db_session: Any, enforcing_env: None
) -> None:
    admin = _admin(db_session)
    email = _addr("off-domain")
    membership_service.add_member(db_session, email=email, initial_role="member", actor=admin)

    assert otp_service.env_signup_allowed(email, _OTP) is False
    assert otp_service.is_signup_eligible(db_session, email, _OTP) is True


# ── decision 9: initial_role seeds the NEW row only ──────────────────────────


def test_initial_role_seeds_the_first_sign_in(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_mod, "_settings", Settings(environment="prod", auth_dev_bypass=False))
    admin = _admin(db_session)
    email = _addr("preprovisioned")
    membership_service.add_member(db_session, email=email, initial_role="viewer", actor=admin)

    user = auth_mod._upsert_user(
        db_session, aad_object_id=uuid.uuid4().hex, email=email, display_name=None
    )
    assert user.role == "viewer"


def test_an_in_app_role_change_survives_the_next_sign_in(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tested from the other side, as the ADR requires: sign in, change the role
    in-app, sign in again. Routing `initial_role` through `_upsert_user`'s `role=`
    parameter would write it into the conflict branch and silently undo this.
    """
    monkeypatch.setattr(auth_mod, "_settings", Settings(environment="prod", auth_dev_bypass=False))
    admin = _admin(db_session)
    email = _addr("promoted")
    oid = uuid.uuid4().hex
    membership_service.add_member(db_session, email=email, initial_role="viewer", actor=admin)

    user = auth_mod._upsert_user(db_session, aad_object_id=oid, email=email, display_name=None)
    user.role = "member"
    db_session.commit()

    again = auth_mod._upsert_user(db_session, aad_object_id=oid, email=email, display_name=None)
    assert again.role == "member"


def test_an_unlisted_address_still_gets_the_signup_default(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_mod, "_settings", Settings(environment="prod", auth_dev_bypass=False))
    user = auth_mod._upsert_user(
        db_session, aad_object_id=uuid.uuid4().hex, email=_addr("plain"), display_name=None
    )
    assert user.role == "member"


# ── the dev-bypass exemption (decision 5) ────────────────────────────────────


def test_dev_bypass_keeps_working_once_enforcement_is_on(db_session: Any) -> None:
    """The local and eval stacks must stay bootable, and the E2E lane must not
    lock itself out by adding a member.
    """
    admin = _admin(db_session)
    membership_service.add_member(
        db_session, email=_addr("someone"), initial_role="member", actor=admin
    )

    user = auth_mod._get_current_user_dev_bypass(_request(), db_session)
    assert user.email == auth_mod.DEV_BYPASS_EMAIL


# ── the env allowlist grants at EVERY door, not two of four ──────────────────


def _env_listed_user(db: Any, email: str) -> User:
    """A user row for an env-listed address that has NO `workspace_members` row."""
    user = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email, role="member")
    db.add(user)
    db.flush()
    return user


def _enforcement_on(db: Any, admin: User) -> None:
    """Write a managed row so the table is non-empty, without naming `email`."""
    membership_service.add_member(
        db, email=_addr("someone-else"), initial_role="member", actor=admin
    )


def test_an_env_listed_address_gets_a_working_session_not_a_403_on_every_call(
    db_session: Any, otp_env: None
) -> None:
    """The reported defect: sign-in works and the workspace is unusable.

    The address is on `AUTH_OTP_ALLOWED_DOMAINS` and has no members row, so it
    passes `is_signup_eligible`, gets a code, verifies, and receives a cookie —
    then `session_service.resolve_token` refused it on every following request.
    """
    admin = _admin(db_session)
    email = "on-the-allowlist@acme.io"
    _enforcement_on(db_session, admin)
    assert otp_service.is_signup_eligible(db_session, email, _OTP) is True

    user = _env_listed_user(db_session, email)
    _, token = session_service.create_session(db_session, user)
    assert session_service.resolve_token(db_session, token).id == user.id


def test_an_env_listed_address_keeps_a_working_PAT_on_rest_and_mcp(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, otp_env: None
) -> None:
    """Under OTP a PAT is the ONLY /mcp credential, so refusing it here removed
    that deployment's whole AI surface for an admitted address.
    """
    from backend.app.db import session as db_session_mod

    monkeypatch.setattr(auth_mod, "_settings", _OTP)
    monkeypatch.setattr(db_session_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    admin = _admin(db_session)
    email = "on-the-allowlist@acme.io"
    _enforcement_on(db_session, admin)
    user = _env_listed_user(db_session, email)
    _, token = api_key_service.create_key(db_session, user, name="probe")

    assert api_key_service.resolve_token(db_session, token).id == user.id
    resolved = auth_mod._get_current_user_otp(_request(f"Bearer {token}"), db_session)
    assert resolved.id == user.id


async def test_the_env_listed_PAT_also_passes_the_mcp_verifier(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, otp_env: None
) -> None:
    from backend.app.db import session as db_session_mod

    monkeypatch.setattr(auth_mod, "_settings", _OTP)
    monkeypatch.setattr(db_session_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    admin = _admin(db_session)
    _enforcement_on(db_session, admin)
    user = _env_listed_user(db_session, "on-the-allowlist@acme.io")
    _, token = api_key_service.create_key(db_session, user, name="probe")

    assert await mcp_auth._PatOrJwtVerifier(None).verify_token(token) is not None


def test_the_break_glass_admin_allowlist_can_still_get_in(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    """`WORKSPACE_ADMIN_EMAILS` is the way back into a workspace whose table has
    locked everyone out. `_upsert_user` ignoring it made that recovery path dead.
    """
    operator = "on-call@example.com"
    # In the process env too: `bootstrap_role` reads `get_settings()`, so a
    # local Settings alone proves admission but not the admin seed.
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", operator)
    get_settings.cache_clear()
    settings = Settings(
        environment="prod",
        auth_dev_bypass=False,
        oidc_issuer="https://example-idp.test",
        oidc_audience="dataq-client-id",
        workspace_admin_emails=operator,
    )
    monkeypatch.setattr(auth_mod, "_settings", settings)
    admin = _admin(db_session)
    _enforcement_on(db_session, admin)

    user = auth_mod._upsert_user(
        db_session, aad_object_id=uuid.uuid4().hex, email=operator, display_name=None
    )
    assert user.email == operator
    assert user.role == "admin"


def test_a_lookalike_domain_is_still_refused_at_the_door_itself(
    db_session: Any, enforcing_env: None
) -> None:
    """The grant-only union must not become a suffix match: assert it through
    `is_signup_eligible`, which is what the OTP endpoints call, not only through
    the helper underneath it.
    """
    admin = _admin(db_session)
    _enforcement_on(db_session, admin)

    assert otp_service.is_signup_eligible(db_session, "ada@acme.io", _OTP) is True
    assert otp_service.is_signup_eligible(db_session, "ada@evil-acme.io", _OTP) is False
    assert otp_service.is_signup_eligible(db_session, "ada@acme.io.evil.net", _OTP) is False


# ── the doors, as data ───────────────────────────────────────────────────────


def _doors() -> list[Door]:
    """Every place a credential becomes a principal. See `support/membership_gates`."""
    return [
        Door(
            name="rest:get_current_user",
            credential="identity",
            surface="rest",
            # The SAME `aad_object_id` the probe row carries: a fresh one is an
            # email collision, which fails before ever reaching the gate.
            setup=lambda db, user: user,
            exercise=lambda db, user: auth_mod._upsert_user(
                db, aad_object_id=user.aad_object_id, email=user.email, display_name=None
            ),
        ),
        Door(
            name="session_service.resolve_token",
            credential="session",
            surface="rest",
            setup=lambda db, user: session_service.create_session(db, user)[1],
            exercise=lambda db, token: session_service.resolve_token(db, token),
        ),
        Door(
            name="api_key_service.resolve_token",
            credential="pat",
            surface="rest",
            setup=lambda db, user: api_key_service.create_key(db, user, name="sweep")[1],
            exercise=lambda db, token: api_key_service.resolve_token(db, token),
        ),
        Door(
            name="mcp.auth.resolve_current_user",
            credential="pat",
            surface="mcp",
            setup=lambda db, user: user,
            exercise=lambda db, user: membership_service.require_member(
                db, user.email, door="mcp", settings=get_settings()
            ),
        ),
    ]


def test_every_credential_kind_and_surface_has_a_declared_door() -> None:
    """A fifth credential kind that never lands here is a door nothing sweeps."""
    doors = _doors()
    assert credentials_covered(doors) == set(CREDENTIALS)
    assert surfaces_covered(doors) == set(SURFACES)


@pytest.mark.parametrize("door", _doors(), ids=operator.attrgetter("name"))
def test_every_declared_door_refuses_a_removed_member(
    door: Door, db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    """One sweep over the declared doors, each with a REAL credential minted
    while its owner was admitted. A fabricated one fails earlier for an
    acceptable-looking reason and the sweep passes with the gate deleted.
    """
    monkeypatch.setattr(auth_mod, "_settings", _OIDC)
    admin = _admin(db_session)
    email = _addr("sweep")
    user = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email, role="member")
    db_session.add(user)
    db_session.flush()
    added = membership_service.add_member(
        db_session, email=email, initial_role="member", actor=admin
    )
    handle = door.setup(db_session, user) if door.setup else email
    # Admitted: the door works, so a later refusal is the gate and not the set-up.
    door.exercise(db_session, handle)

    membership_service.remove_member(db_session, added.member.id, actor=admin)
    with pytest.raises(DataQError) as exc:
        door.exercise(db_session, handle)
    assert exc.value.status_code == 403


# ── the structural gate, probed where the choke points cannot reach ──────────
#
# The sweep above passes with BOTH wrappers deleted, because every door in it
# already gates at its own choke point — which is exactly the vacuous pass this
# repo has hit four times on the MCP tool surface. The wrapper exists for the
# door that does NOT gate: a fifth resolver added later. So it has to be probed
# with a resolver that yields a principal and checks nothing.


def _ungated_resolver(user: User) -> Any:
    """A future resolver that forgot to call the check — the case the wrapper is for."""

    def resolve(db: Annotated[Session, Depends(get_db)]) -> User:
        return user

    return resolve


def test_the_rest_binding_gates_a_resolver_that_checks_nothing(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    monkeypatch.setattr(auth_mod, "_settings", _OIDC)
    admin = _admin(db_session)
    email = _addr("ungated")
    user = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email, role="member")
    db_session.add(user)
    db_session.flush()
    added = membership_service.add_member(
        db_session, email=email, initial_role="member", actor=admin
    )
    gated = auth_mod.membership_gated(_ungated_resolver(user))

    # Admitted: the wrapper lets a member through, so the refusal below is the
    # gate rather than the wrapper being broken outright.
    assert gated(db=db_session).id == user.id

    membership_service.remove_member(db_session, added.member.id, actor=admin)
    with pytest.raises(DataQError) as exc:
        gated(db=db_session)
    assert exc.value.status_code == 403


def test_the_rest_binding_preserves_the_resolver_signature() -> None:
    """FastAPI resolves dependencies from the signature; a wrapper that hides it
    would silently stop injecting `db` and the gate would never run.
    """
    user = User(id=uuid.uuid4(), aad_object_id="x", email="a@b.c", role="member")
    gated = auth_mod.membership_gated(_ungated_resolver(user))
    assert "db" in inspect.signature(gated).parameters


def test_the_mcp_entry_point_gates_a_principal_the_resolver_did_not_check(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, enforcing_env: None
) -> None:
    admin = _admin(db_session)
    email = _addr("mcp-ungated")
    user = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email, role="member")
    db_session.add(user)
    db_session.flush()
    added = membership_service.add_member(
        db_session, email=email, initial_role="member", actor=admin
    )
    monkeypatch.setattr(mcp_auth, "_resolve_principal", lambda session: user)
    monkeypatch.setenv("AUTH_OTP_ALLOWED_DOMAINS", "acme.io")
    monkeypatch.setenv("AUTH_EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AUTH_EMAIL_USERNAME", "dataq@example.com")
    monkeypatch.setenv("AUTH_EMAIL_FROM", "dataq@example.com")
    monkeypatch.setenv("AUTH_EMAIL_PASSWORD_SECRET_NAME", "auth-email-password")
    get_settings.cache_clear()

    assert mcp_auth.resolve_current_user(db_session).id == user.id

    membership_service.remove_member(db_session, added.member.id, actor=admin)
    with pytest.raises(mcp_auth.McpAuthError):
        mcp_auth.resolve_current_user(db_session)
