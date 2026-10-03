"""Seeds create the users they need without going through a sign-in door (#2331)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from backend.app.core.auth import DEV_BYPASS_AAD_OID, DEV_BYPASS_EMAIL, _upsert_user
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.services.membership_service import MembershipDeniedError
from backend.scripts import seed_dev
from backend.scripts.demo_data import ensure_seed_user

# The prebuilt-image stack's default: email codes for one address, the bypass off.
_OTP_ONLY: dict[str, Any] = {
    "auth_email_smtp_host": "mailpit",
    "auth_email_username": "dataq-local",
    "auth_email_from": "dataq@dataq.local",
    "auth_email_password_secret_name": "dataq-local-smtp",
    "auth_otp_allowed_emails": "evaluator@example.com",
    "workspace_admin_emails": "evaluator@example.com",
    "auth_dev_bypass": False,
}


def _owner(db_session: Any) -> User:
    return ensure_seed_user(
        db_session, aad_object_id=DEV_BYPASS_AAD_OID, email=DEV_BYPASS_EMAIL, display_name=None
    )


def test_the_owner_is_seeded_where_the_sign_in_door_refuses_it(db_session: Any) -> None:
    settings = Settings(**_OTP_ONLY)
    # The premise: this address may not sign in here, so the seed cannot use that door.
    with pytest.raises(MembershipDeniedError):
        _upsert_user(
            db_session,
            aad_object_id=DEV_BYPASS_AAD_OID,
            email=DEV_BYPASS_EMAIL,
            display_name=None,
            settings=settings,
        )

    owner = _owner(db_session)

    assert owner.email == DEV_BYPASS_EMAIL
    assert owner.aad_object_id == DEV_BYPASS_AAD_OID


def test_seeding_the_owner_twice_keeps_one_row(db_session: Any) -> None:
    first = _owner(db_session)
    second = _owner(db_session)

    assert second.id == first.id
    count = db_session.scalar(
        select(func.count()).select_from(User).where(func.lower(User.email) == DEV_BYPASS_EMAIL)
    )
    assert count == 1


def test_e2e_fixtures_are_seeded_only_where_their_tokens_can_land(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATAQ_ROLE_TOKENS_PATH", raising=False)
    # The published image: no frontend/e2e directory.
    monkeypatch.setattr(seed_dev, "ROLE_TOKENS_PATH", tmp_path / "absent" / ".role-tokens.json")
    assert seed_dev._e2e_fixtures_wanted() is False
    # A source checkout.
    monkeypatch.setattr(seed_dev, "ROLE_TOKENS_PATH", tmp_path / ".role-tokens.json")
    assert seed_dev._e2e_fixtures_wanted() is True
    # A named destination (the docs capture stack).
    monkeypatch.setattr(seed_dev, "ROLE_TOKENS_PATH", tmp_path / "absent" / ".role-tokens.json")
    monkeypatch.setenv("DATAQ_ROLE_TOKENS_PATH", str(tmp_path / "absent" / "t.json"))
    assert seed_dev._e2e_fixtures_wanted() is True
