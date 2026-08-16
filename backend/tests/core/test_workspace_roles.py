"""Workspace-role model + resolution seam — ADR 0033 slice #740.

Three things are under test here, and they are deliberately separated:

* **`resolve_role`** — the pure composition of the two admin sources (stored
  `users.role` OR the `WORKSPACE_ADMIN_EMAILS` break-glass allowlist).
* **`require_role`** — the coarse-axis FastAPI gate, exercised through a real
  app + `TestClient` rather than by calling the closure directly. Calling the
  function would prove the rank comparison and nothing about the `Depends`
  composition, which is the half that actually breaks (a dependency factory that
  forgets to annotate `current_user` fails at wiring time, not at rank time).
* **The write-through rules** (`bootstrap_role` / `promoted_role`) and their two
  real sign-in call sites, because the ADR's precedence decision lives in them.

The `_reset_caches` autouse fixture (conftest) clears the cached `Settings`
between tests, so `monkeypatch.setenv` + `get_settings.cache_clear()` is the
supported way to vary the allowlist.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from backend.app.core import auth as auth_mod
from backend.app.core.auth import DEV_BYPASS_EMAIL, require_role
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import register_exception_handlers
from backend.app.core.roles import (
    ROLE_RANK,
    bootstrap_role,
    is_workspace_admin,
    resolve_role,
    should_promote_to_admin,
)
from backend.app.db.models import (
    ADMIN_ROLE,
    DEFAULT_WORKSPACE_ROLE,
    WORKSPACE_ROLES,
    Connection,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.services import api_key_service, otp_service, suite_authz


def _user(email: str, role: str = DEFAULT_WORKSPACE_ROLE) -> User:
    """A detached User — enough for the pure resolvers, which never touch the DB."""
    return User(id=uuid.uuid4(), aad_object_id=None, email=email, role=role)


def _allowlist(monkeypatch: pytest.MonkeyPatch, *emails: str) -> None:
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", ",".join(emails))
    get_settings.cache_clear()


# ── the rank ladder ──────────────────────────────────────────────────────────


def test_role_rank_covers_every_stored_role() -> None:
    """`_ROLE_RANK` is written out by hand (so a reorder of `WORKSPACE_ROLES`
    can't silently invert the ladder). This is the guard that keeps the two in
    sync — without it, adding a fourth role would make `require_role` raise
    `ValueError` for a role the CHECK constraint happily stores."""
    assert set(ROLE_RANK) == set(WORKSPACE_ROLES)
    assert ROLE_RANK["viewer"] < ROLE_RANK["member"]
    assert ROLE_RANK["member"] < ROLE_RANK[ADMIN_ROLE]


# ── resolve_role: the two sources compose ────────────────────────────────────


@pytest.mark.parametrize("role", WORKSPACE_ROLES)
def test_stored_role_resolves_as_itself_without_an_allowlist(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allowlist(monkeypatch)  # empty
    assert resolve_role(_user("nobody@acme.io", role)) == role


def test_allowlist_raises_a_member_to_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bootstrap path: a stored `member` on the env allowlist IS an admin,
    which is what makes the upgrade a zero-config one for deployments that have
    only ever had the allowlist."""
    user = _user("ada@acme.io", "member")
    _allowlist(monkeypatch, "ada@acme.io")
    assert resolve_role(user) == ADMIN_ROLE
    assert is_workspace_admin(user) is True


def test_allowlist_raises_even_a_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break-glass must work from ANY stored tier — an operator locked out of a
    workspace whose admins all left may well have been demoted to viewer first."""
    _allowlist(monkeypatch, "ada@acme.io")
    assert resolve_role(_user("ada@acme.io", "viewer")) == ADMIN_ROLE


def test_allowlist_is_matched_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    _allowlist(monkeypatch, "  Ada@ACME.io ")
    assert resolve_role(_user("ada@acme.io", "member")) == ADMIN_ROLE


def test_allowlist_never_lowers_a_stored_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The asymmetry that makes the env path recoverable rather than a second
    source of truth: absence from the allowlist is NOT a demotion signal. If it
    were, removing one env entry would strip every in-app admin at once."""
    _allowlist(monkeypatch, "someone@else.io")
    assert resolve_role(_user("ada@acme.io", ADMIN_ROLE)) == ADMIN_ROLE
    assert is_workspace_admin(_user("ada@acme.io", ADMIN_ROLE)) is True


def test_is_workspace_admin_agrees_with_resolve_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """The alias must stay an alias — these two are read by different gates
    (`require_workspace_admin` vs `require_role`) and a divergence would mean a
    role that says one thing at the router and another at the suite ladder."""
    _allowlist(monkeypatch, "ada@acme.io")
    for email in ("ada@acme.io", "bob@acme.io"):
        for role in WORKSPACE_ROLES:
            user = _user(email, role)
            assert is_workspace_admin(user) is (resolve_role(user) == ADMIN_ROLE)


# ── require_role: the gate, wired as a real dependency ───────────────────────


@pytest.fixture
def gated_client(db_session: Any) -> Iterator[TestClient]:
    """A minimal app exposing one route per role tier, all behind `require_role`.

    A throwaway app rather than the real one because #740 ships the seam and no
    call sites — the enforcement points land in #741. Wiring it for real here is
    what proves the factory composes with `get_current_user`; #741's own tests
    then cover the specific endpoints.
    """
    app = FastAPI()
    # The real app's handlers, not FastAPI's defaults — without them a
    # `DataQError` propagates as a raw exception and the 403 envelope assertions
    # below would be testing a 500.
    register_exception_handlers(app)

    @app.get("/viewer-plus")
    def _viewer_plus(_: User = Depends(require_role("viewer"))) -> dict[str, str]:
        return {"ok": "viewer"}

    @app.get("/member-plus")
    def _member_plus(_: User = Depends(require_role("member"))) -> dict[str, str]:
        return {"ok": "member"}

    @app.get("/admin-only")
    def _admin_only(_: User = Depends(require_role(ADMIN_ROLE))) -> dict[str, str]:
        return {"ok": "admin"}

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _pat_headers(db_session: Any, user: User) -> dict[str, str]:
    _, token = api_key_service.create_key(db_session, user, name=f"k-{uuid.uuid4().hex[:6]}")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("role", "viewer_plus", "member_plus", "admin_only"),
    [
        ("viewer", 200, 403, 403),
        ("member", 200, 200, 403),
        (ADMIN_ROLE, 200, 200, 200),
    ],
)
def test_require_role_enforces_the_full_matrix(
    gated_client: TestClient,
    db_session: Any,
    role: str,
    viewer_plus: int,
    member_plus: int,
    admin_only: int,
) -> None:
    user = _user(f"m-{uuid.uuid4().hex[:8]}@example.com", role)
    db_session.add(user)
    db_session.commit()
    headers = _pat_headers(db_session, user)

    # Hoisted out of the asserts on purpose (`test_assert_hygiene`): an assert
    # that performs its own request vanishes under `python -O`, taking the
    # request with it, and the test then passes while doing nothing.
    viewer_resp = gated_client.get("/viewer-plus", headers=headers)
    member_resp = gated_client.get("/member-plus", headers=headers)
    admin_resp = gated_client.get("/admin-only", headers=headers)

    assert viewer_resp.status_code == viewer_plus
    assert member_resp.status_code == member_plus
    assert admin_resp.status_code == admin_only


def test_require_role_403_carries_have_and_need(gated_client: TestClient, db_session: Any) -> None:
    """The uniform envelope: a denial must say what the caller HAS and what was
    NEEDED, the same shape the suite ladder reports, so a client can render one
    message for both authz axes."""
    user = _user(f"v-{uuid.uuid4().hex[:8]}@example.com", "viewer")
    db_session.add(user)
    db_session.commit()

    resp = gated_client.get("/admin-only", headers=_pat_headers(db_session, user))
    assert resp.status_code == 403
    body = resp.json()["error"]
    assert body["code"] == "workspace_role_required"
    assert body["detail"] == {"have": "viewer", "need": ADMIN_ROLE}


def test_require_role_rejects_an_unknown_minimum() -> None:
    """Fails at import/wiring time, not at request time — a typo'd tier must not
    become a route that silently admits everyone (or nobody)."""
    with pytest.raises(ValueError, match="unknown workspace role"):
        require_role("superuser")


def test_allowlisted_caller_clears_an_admin_gate_with_a_member_row(
    gated_client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break-glass, end to end through the gate — not just through `resolve_role`."""
    user = _user(f"bg-{uuid.uuid4().hex[:8]}@example.com", "member")
    db_session.add(user)
    db_session.commit()
    headers = _pat_headers(db_session, user)

    before = gated_client.get("/admin-only", headers=headers)
    assert before.status_code == 403

    _allowlist(monkeypatch, user.email)

    after = gated_client.get("/admin-only", headers=headers)
    assert after.status_code == 200


# ── the "resolves per request" property (ADR 0033 decision 7) ────────────────


def test_a_role_change_takes_effect_on_the_next_request_with_the_same_pat(
    gated_client: TestClient, db_session: Any
) -> None:
    """The whole reason no token-revocation machinery is needed: a PAT
    authenticates AS its user (ADR 0026), and the role is read per request from
    the row. Demote the user and the *already-issued* token demotes with them.

    Asserted with ONE token across the change — re-minting would prove nothing.
    """
    user = _user(f"p-{uuid.uuid4().hex[:8]}@example.com", ADMIN_ROLE)
    db_session.add(user)
    db_session.commit()
    headers = _pat_headers(db_session, user)

    as_admin = gated_client.get("/admin-only", headers=headers)
    assert as_admin.status_code == 200

    user.role = "viewer"
    db_session.commit()

    admin_after = gated_client.get("/admin-only", headers=headers)
    member_after = gated_client.get("/member-plus", headers=headers)
    viewer_after = gated_client.get("/viewer-plus", headers=headers)

    assert admin_after.status_code == 403
    assert member_after.status_code == 403
    assert viewer_after.status_code == 200


def test_a_promotion_takes_effect_on_the_next_request_too(
    gated_client: TestClient, db_session: Any
) -> None:
    """The other direction — a promoted user must not have to sign out and back
    in, which is what a role cached in a session or token would have required."""
    user = _user(f"q-{uuid.uuid4().hex[:8]}@example.com", "viewer")
    db_session.add(user)
    db_session.commit()
    headers = _pat_headers(db_session, user)

    before = gated_client.get("/admin-only", headers=headers)
    assert before.status_code == 403

    user.role = ADMIN_ROLE
    db_session.commit()

    after = gated_client.get("/admin-only", headers=headers)
    assert after.status_code == 200


# ── the stored column: default + CHECK ───────────────────────────────────────


def test_a_new_row_defaults_to_member(db_session: Any) -> None:
    """`server_default 'member'` — the value the migration backfills existing
    rows with, asserted against the real column rather than the Python default
    (there is none; the default lives in the DDL)."""
    user = User(id=uuid.uuid4(), aad_object_id=None, email=f"d-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.role == DEFAULT_WORKSPACE_ROLE


def test_the_check_constraint_rejects_an_unknown_role(db_session: Any) -> None:
    """The database is the last line: a service-layer bug that writes 'owner'
    (a *suite* level, not a workspace role — the two vocabularies overlap on
    'admin', which is exactly how such a bug would happen) must not persist."""
    user = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=f"x-{uuid.uuid4().hex[:8]}@example.com",
        role="owner",
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ── suite_authz reads the same source ────────────────────────────────────────


def _suite_for(db_session: Any, owner: User) -> Suite:
    conn = Connection(
        id=uuid.uuid4(),
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        id=uuid.uuid4(),
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=owner.id,
    )
    db_session.add(suite)
    db_session.commit()
    return suite


def test_a_stored_admin_is_implicit_suite_admin_with_an_empty_allowlist(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression the pre-#740 short-circuit would have caused.

    `_is_workspace_admin` used to `return False` immediately when
    `WORKSPACE_ADMIN_EMAILS` was empty. That was sound when the allowlist was the
    only source; with a stored role it is a silent authz hole in the direction
    that matters — after #742, a workspace managed entirely in-app sets NO env
    allowlist at all, so every stored admin would have lost implicit suite-admin
    while `require_workspace_admin` still let them into `/admin`.
    """
    _allowlist(monkeypatch)  # explicitly empty — the pre-#740 short-circuit case
    owner = _user(f"o-{uuid.uuid4().hex[:8]}@example.com")
    admin = _user(f"a-{uuid.uuid4().hex[:8]}@example.com", ADMIN_ROLE)
    db_session.add_all([owner, admin])
    db_session.commit()
    suite = _suite_for(db_session, owner)

    assert suite_authz.effective_permission(db_session, suite, admin.id) == suite_authz.ADMIN
    assert suite_authz.effective_permissions(db_session, [suite], admin.id) == {
        suite.id: suite_authz.ADMIN
    }
    # And the gate itself, not just the resolver.
    assert suite_authz.require_permission(db_session, suite.id, admin.id, minimum="admin") is suite


def test_a_stored_member_is_not_implicit_suite_admin(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half — the seam must not have made everyone an admin."""
    _allowlist(monkeypatch)
    owner = _user(f"o-{uuid.uuid4().hex[:8]}@example.com")
    member = _user(f"m-{uuid.uuid4().hex[:8]}@example.com", "member")
    db_session.add_all([owner, member])
    db_session.commit()
    suite = _suite_for(db_session, owner)

    assert suite_authz.effective_permission(db_session, suite, member.id) is None


# ── the write-through rules ──────────────────────────────────────────────────


def test_bootstrap_role_prefers_the_allowlist_over_the_signup_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0033 decision 8's precedence. An operator on BOTH lists must be stored
    `admin`, never `member`-stored-but-admin-effective — otherwise dropping the
    env entry later silently demotes the very admin #742's last-admin guard was
    counting on."""
    _allowlist(monkeypatch, "ada@acme.io")
    assert bootstrap_role("ada@acme.io", default="viewer") == ADMIN_ROLE
    assert bootstrap_role("bob@acme.io", default="viewer") == "viewer"
    assert bootstrap_role("bob@acme.io") == DEFAULT_WORKSPACE_ROLE


def test_should_promote_to_admin_is_true_only_for_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The predicate every existing-row writer branches on. False must mean
    "write nothing", not "write the stored value back" — see the docstring; the
    three call sites are covered individually below."""
    _allowlist(monkeypatch, "ada@acme.io")
    assert should_promote_to_admin("ada@acme.io") is True
    assert should_promote_to_admin("ADA@ACME.IO") is True
    assert should_promote_to_admin("bob@acme.io") is False


def test_otp_signup_uses_the_configured_default_role(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_OTP_DEFAULT_ROLE", "viewer")
    _allowlist(monkeypatch)
    email = f"otp-{uuid.uuid4().hex[:8]}@example.com"

    user = otp_service.resolve_or_create_user(db_session, email)
    assert user.role == "viewer"


def test_otp_signup_default_is_member(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _allowlist(monkeypatch)
    user = otp_service.resolve_or_create_user(db_session, f"otp-{uuid.uuid4().hex[:8]}@example.com")
    assert user.role == DEFAULT_WORKSPACE_ROLE


def test_otp_signup_allowlist_beats_the_default_role(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precedence rule at its real call site, not just in the helper."""
    email = f"otp-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setenv("AUTH_OTP_DEFAULT_ROLE", "viewer")
    _allowlist(monkeypatch, email)

    user = otp_service.resolve_or_create_user(db_session, email)
    assert user.role == ADMIN_ROLE


def test_oidc_signup_uses_the_configured_default_role(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AUTH_OIDC_DEFAULT_ROLE` — the OTP knob's twin on the SSO path.

    ADR 0033 decision 8 specified the knob only for OTP, on the reasoning that an
    OIDC issuer was already the deployment's identity boundary. #1386's
    `OIDC_ALLOWED_DOMAINS` retired that reasoning: an operator can now admit a
    whole domain on this path too, and without this knob "anyone at acme.io may
    sign in" would necessarily also mean "anyone at acme.io may author suites".
    """
    monkeypatch.setenv("AUTH_OIDC_DEFAULT_ROLE", "viewer")
    _allowlist(monkeypatch)
    user = auth_mod._upsert_user(
        db_session,
        aad_object_id=uuid.uuid4().hex[:32],
        email=f"sso-{uuid.uuid4().hex[:8]}@example.com",
        display_name="S",
    )
    assert user.role == "viewer"


def test_oidc_signup_allowlist_beats_the_default_role(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same precedence as the OTP path — one rule, enforced in `bootstrap_role`."""
    email = f"sso-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setenv("AUTH_OIDC_DEFAULT_ROLE", "viewer")
    _allowlist(monkeypatch, email)
    user = auth_mod._upsert_user(
        db_session, aad_object_id=uuid.uuid4().hex[:32], email=email, display_name="S"
    )
    assert user.role == ADMIN_ROLE


def test_auth_oidc_default_role_rejects_admin() -> None:
    """Same reasoning as its OTP twin: a signup default must not become a third,
    silent admin-minting mechanism."""
    with pytest.raises(ValueError):
        Settings(auth_oidc_default_role="admin")


def test_auth_otp_default_role_rejects_admin() -> None:
    """`admin` is deliberately not a signup default: it would make the signup
    allowlist a third, silent admin-minting mechanism beside the two the ADR
    sanctions."""
    with pytest.raises(ValueError):
        # The type: ignore IS the assertion's other half — the Literal rejects
        # this statically, and pydantic must reject it at runtime too, because
        # the value arrives from an env var that no annotation guards.
        Settings(auth_otp_default_role="admin")


def test_otp_sign_in_promotes_an_existing_row_on_the_allowlist(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write-through on an EXISTING row — the step that turns an effective
    break-glass admin into a *stored* one, which is the only kind #742's guard
    can count."""
    email = f"otp-{uuid.uuid4().hex[:8]}@example.com"
    _allowlist(monkeypatch)
    user = otp_service.resolve_or_create_user(db_session, email)
    assert user.role == DEFAULT_WORKSPACE_ROLE

    _allowlist(monkeypatch, email)
    again = otp_service.resolve_or_create_user(db_session, email)
    assert again.id == user.id
    assert again.role == ADMIN_ROLE


def test_otp_sign_in_never_demotes(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Signing in must not undo an in-app promotion just because the signer-in
    is not on the env allowlist."""
    email = f"otp-{uuid.uuid4().hex[:8]}@example.com"
    _allowlist(monkeypatch)
    user = otp_service.resolve_or_create_user(db_session, email)
    user.role = ADMIN_ROLE
    db_session.commit()

    again = otp_service.resolve_or_create_user(db_session, email)
    assert again.role == ADMIN_ROLE


def test_oidc_upsert_seeds_member_and_promotes_on_the_allowlist(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AAD/OIDC sign-in path — same two rules, different call site."""
    email = f"aad-{uuid.uuid4().hex[:8]}@example.com"
    oid = uuid.uuid4().hex[:32]
    _allowlist(monkeypatch)

    user = auth_mod._upsert_user(db_session, aad_object_id=oid, email=email, display_name="A")
    assert user.role == DEFAULT_WORKSPACE_ROLE

    _allowlist(monkeypatch, email)
    promoted = auth_mod._upsert_user(db_session, aad_object_id=oid, email=email, display_name="A")
    assert promoted.id == user.id
    assert promoted.role == ADMIN_ROLE


def test_oidc_upsert_never_demotes(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-request upsert runs on EVERY real-mode request. If it wrote the
    role back, an in-app promotion would be reverted by the promoted user's very
    next page load — the same shape as the `display_name` regression #1139 fixed.
    """
    email = f"aad-{uuid.uuid4().hex[:8]}@example.com"
    oid = uuid.uuid4().hex[:32]
    _allowlist(monkeypatch)
    user = auth_mod._upsert_user(db_session, aad_object_id=oid, email=email, display_name="A")
    user.role = ADMIN_ROLE
    db_session.commit()

    again = auth_mod._upsert_user(db_session, aad_object_id=oid, email=email, display_name="A")
    assert again.role == ADMIN_ROLE


def test_claiming_an_otp_row_from_aad_promotes_but_does_not_demote(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity-linking path (ADR 0032 decision 6) is a third writer of
    `users.role` and must obey the same promote-only rule as the other two."""
    email = f"link-{uuid.uuid4().hex[:8]}@example.com"
    _allowlist(monkeypatch)
    otp_row = otp_service.resolve_or_create_user(db_session, email)
    otp_row.role = ADMIN_ROLE
    db_session.commit()

    claimed = auth_mod._upsert_user(
        db_session, aad_object_id=uuid.uuid4().hex[:32], email=email, display_name="L"
    )
    assert claimed.id == otp_row.id
    assert claimed.role == ADMIN_ROLE, "linking an AAD identity must not reset the stored role"


# ── /me exposes the effective role ───────────────────────────────────────────


@pytest.fixture
def me_client(db_session: Any) -> Iterator[TestClient]:
    from backend.app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("role", WORKSPACE_ROLES)
def test_me_reports_the_stored_role(me_client: TestClient, db_session: Any, role: str) -> None:
    user = _user(f"me-{uuid.uuid4().hex[:8]}@example.com", role)
    db_session.add(user)
    db_session.commit()

    body = me_client.get("/api/v1/me", headers=_pat_headers(db_session, user)).json()
    assert body["role"] == role
    assert body["is_workspace_admin"] is (role == ADMIN_ROLE)


def test_me_reports_the_effective_role_not_the_stored_column(
    me_client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A break-glass admin's row still says `member` until their next sign-in
    writes it through. `/me` must report what the GATES will do, not what the
    column happens to say — otherwise the frontend hides the Admin nav from
    someone every admin endpoint would have let in."""
    user = _user(f"me-{uuid.uuid4().hex[:8]}@example.com", "member")
    db_session.add(user)
    db_session.commit()
    _allowlist(monkeypatch, user.email)

    body = me_client.get("/api/v1/me", headers=_pat_headers(db_session, user)).json()
    assert user.role == "member", "precondition: the stored column is untouched"
    assert body["role"] == ADMIN_ROLE
    assert body["is_workspace_admin"] is True


def test_me_dev_bypass_user_reports_member(
    me_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allowlist(monkeypatch)
    body = me_client.get("/api/v1/me").json()
    assert body["email"] == DEV_BYPASS_EMAIL
    assert body["role"] == DEFAULT_WORKSPACE_ROLE
