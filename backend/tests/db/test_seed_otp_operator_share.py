"""The seed shares its suites with the OTP operator (#1150).

The local/eval stacks now sign you in as *yourself* rather than as the dev-bypass
identity that owns every seeded row — so without an explicit share, an evaluator
following the "comes up seeded with demo data" promise lands in an empty
workspace and concludes the seed failed. This pins the share, its source
precedence, and its idempotency.

Skips without TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.db.models import Connection, Share, Suite, User
from backend.scripts.seed_dev import _otp_operator_emails, _share_with_otp_operators

_MAILER: dict[str, Any] = {
    "auth_email_smtp_host": "mailpit",
    "auth_email_username": "dataq-local",
    "auth_email_from": "dataq@dataq.local",
    "auth_email_password_secret_name": "dataq-local-smtp",
}


@pytest.fixture(autouse=True)
def _isolate_signin_email_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_otp_operator_emails` falls back to the ambient `DATAQ_SIGNIN_EMAIL`
    compose switch, and `scripts/setup.sh` sets that var in the gitignored
    `.env` for every local stack (#1150 made OTP the local default). Without
    this, any `Settings()` built in this module without an explicit allowlist
    would silently pick up whatever a developer's own machine happens to have
    exported — green in CI (which never sets it), red on a correctly
    configured dev box (#1200). Tests that need the switch set still do so
    explicitly via their own `monkeypatch.setenv`, which simply overrides this
    default afterwards."""
    monkeypatch.delenv("DATAQ_SIGNIN_EMAIL", raising=False)


def _owner_with_suite(db_session: Any) -> tuple[User, Suite]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name=f"seeded-{uuid.uuid4().hex[:8]}", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    return owner, suite


# ── which addresses the seed targets ─────────────────────────────────────────


def test_the_allowlist_is_the_source_when_the_app_env_is_visible() -> None:
    settings = Settings(**_MAILER, auth_otp_allowed_emails="Ada@Acme.io, grace@acme.io")
    assert _otp_operator_emails(settings) == ["ada@acme.io", "grace@acme.io"]


def test_the_compose_switch_is_the_fallback_when_the_allowlist_is_not_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`scripts/setup.sh` seeds from the HOST, reading `.env.app` — which carries no
    mailer block, so a bare allowlist there would trip the fail-closed validator.
    `DATAQ_SIGNIN_EMAIL` is the only signal available on that path."""
    monkeypatch.setenv("DATAQ_SIGNIN_EMAIL", "  Operator@Example.com  ")
    assert _otp_operator_emails(Settings()) == ["operator@example.com"]


def test_the_allowlist_wins_over_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAQ_SIGNIN_EMAIL", "switch@example.com")
    settings = Settings(**_MAILER, auth_otp_allowed_emails="allowlist@example.com")
    assert _otp_operator_emails(settings) == ["allowlist@example.com"]


def test_a_domain_only_allowlist_targets_nobody() -> None:
    """A domain names no individual mailbox; pre-creating rows for a whole domain
    would be inventing users. Empty is the honest answer, not a guess."""
    settings = Settings(**_MAILER, auth_otp_allowed_domains="acme.io")
    assert _otp_operator_emails(settings) == []


def test_neither_source_set_targets_nobody() -> None:
    """The dev-bypass downgrade: no OTP config at all, so no share and no
    surprise user row. The `_isolate_signin_email_switch` autouse fixture
    already clears the switch; this test just relies on that default."""
    assert _otp_operator_emails(Settings()) == []


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_switch_targets_nobody(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("DATAQ_SIGNIN_EMAIL", blank)
    assert _otp_operator_emails(Settings()) == []


# ── the share itself ─────────────────────────────────────────────────────────


def test_the_operator_gets_an_edit_share_on_every_owned_suite(db_session: Any) -> None:
    owner, suite = _owner_with_suite(db_session)
    settings = Settings(**_MAILER, auth_otp_allowed_emails="operator@example.com")

    granted = _share_with_otp_operators(db_session, owner=owner, settings=settings)
    assert granted >= 1

    operator = db_session.scalar(select(User).where(User.email == "operator@example.com"))
    assert operator is not None
    # Provisioned the way a real sign-in provisions: by email, with no AAD id, so
    # the row the OTP flow later resolves IS this one (ADR 0032 decision 6).
    assert operator.aad_object_id is None

    share = db_session.scalar(
        select(Share).where(Share.suite_id == suite.id, Share.user_id == operator.id)
    )
    assert share is not None
    # `edit`, not `admin`: `admin`/`owner` are ungrantable by design, and the
    # dev-bypass identity must keep ownership so the downgrade path is unchanged.
    assert share.permission == "edit"
    assert suite.created_by == owner.id


def test_re_running_the_seed_grants_nothing_new(db_session: Any) -> None:
    owner, _suite = _owner_with_suite(db_session)
    settings = Settings(**_MAILER, auth_otp_allowed_emails="operator@example.com")

    first = _share_with_otp_operators(db_session, owner=owner, settings=settings)
    assert first >= 1
    assert _share_with_otp_operators(db_session, owner=owner, settings=settings) == 0


def test_suites_owned_by_somebody_else_are_untouched(db_session: Any) -> None:
    """The seed shares what the SEED created, never a suite that happens to exist
    in the database — a local stack pointed at a populated dev DB must not quietly
    hand a third party's suite to the address in `.env`."""
    owner, _suite = _owner_with_suite(db_session)
    stranger, stranger_suite = _owner_with_suite(db_session)
    settings = Settings(**_MAILER, auth_otp_allowed_emails="operator@example.com")

    _share_with_otp_operators(db_session, owner=owner, settings=settings)

    shares_on_stranger_suite = db_session.scalars(
        select(Share).where(Share.suite_id == stranger_suite.id)
    ).all()
    assert shares_on_stranger_suite == []
    assert stranger_suite.created_by == stranger.id
