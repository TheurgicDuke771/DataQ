"""Idempotent dev-data seed (run by scripts/setup.sh, safe to re-run).

Gives a fresh local database a minimal baseline so the UI and API aren't empty
on first boot:

- the **dev-bypass user** (the same fixed identity `auth_dev_bypass` resolves
  every request to), so seeded rows are owned by the user you actually log in as
  locally; and
- the **probe Connection + Suite + Check**, reusing `ensure_probe_fixtures` so
  the seed never drifts from what the Week-1 probe endpoint expects; and
- an `edit` **share of every seeded suite to each allow-listed OTP address**
  (#1150), because the local stacks now sign you in as *yourself* rather than as
  the dev-bypass identity that owns all of this — without it an evaluator
  following the "comes up seeded with demo data" promise would land in an empty
  workspace and conclude the seed had failed.

Every step get-or-creates, so running this repeatedly is a no-op. Run as a module
(so `backend.*` imports resolve from the repo root):

    conda run -n dataq python -m backend.scripts.seed_dev
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth import (
    DEV_BYPASS_AAD_OID,
    DEV_BYPASS_DISPLAY_NAME,
    DEV_BYPASS_EMAIL,
    _upsert_user,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import Suite, User
from backend.app.db.session import get_session
from backend.app.services import otp_service, share_service
from backend.app.services.probe import ensure_probe_fixtures
from backend.scripts.demo_data import seed_demo_data


def _otp_operator_emails(settings: Settings) -> list[str]:
    """Who to share the seeded suites with — the allowlist, or the compose switch.

    Two sources because the seed runs in two places with different visibility:

    * The **eval stack** runs it inside the migrate container, which carries the
      full app env — so `AUTH_OTP_ALLOWED_EMAILS` is right there and is the
      authoritative answer (it is literally who may sign in).
    * **`scripts/setup.sh`** runs it on the HOST, reading `.env.app` — which does
      not carry the mailer block at all (compose injects that). Exporting a bare
      allowlist there would trip the fail-closed validator, since an allowlist
      with no mailer is a *partial* OTP config and refuses to boot. So on that
      path the only visible signal is `DATAQ_SIGNIN_EMAIL`, the root-`.env`
      switch `setup.sh` itself just wrote.

    Allowlist first: when both are present it is the app-level truth. Note that
    `AUTH_OTP_ALLOWED_DOMAINS` deliberately contributes nothing — a domain names
    no individual mailbox, and pre-creating rows for a whole domain would be
    inventing users.
    """
    allow_listed = sorted(settings.auth_otp_allowed_email_set)
    if allow_listed:
        return allow_listed
    switch = os.environ.get("DATAQ_SIGNIN_EMAIL", "").strip().lower()
    return [switch] if switch else []


def _share_with_otp_operators(session: Session, *, owner: User, settings: Settings) -> int:
    """Share every seeded suite with each allow-listed OTP address (#1150).

    Without this the local stacks — which now sign you in as *yourself* — would
    drop an evaluator into an empty workspace, because every seeded row is owned
    by the dev-bypass identity.

    `edit`, deliberately: the dev-bypass identity keeps OWNERSHIP so the downgrade
    path and the Playwright bypass lane are unchanged, and `edit` is the strongest
    tier `grant_share` will hand out at all — `admin`/`owner` are ungrantable by
    design (`_reject_ungrantable_permission`). It is enough to browse, edit and
    run everything, and the address is a WORKSPACE admin besides, so `/admin`
    still shows the whole estate.

    Idempotent, like the rest of this script — re-running skips suites that are
    already shared.
    """
    granted = 0
    for email in _otp_operator_emails(settings):
        # Same provisioning path a real sign-in takes, so the row this creates IS
        # the row the OTP flow will resolve (one user per normalized email, ADR
        # 0032 decision 6) — a hand-rolled INSERT here could differ and fragment
        # the identity across two rows for one human.
        operator = otp_service.resolve_or_create_user(session, email)
        if operator.id == owner.id:  # pragma: no cover - only if seeded as the owner
            continue
        for suite in session.scalars(select(Suite).where(Suite.created_by == owner.id)):
            existing = share_service.list_shares(session, suite.id, actor_id=owner.id)
            if any(share.user_id == operator.id for share in existing):
                continue
            share_service.grant_share(
                session,
                suite.id,
                actor_id=owner.id,
                target_user_id=operator.id,
                permission="edit",
            )
            granted += 1
    if granted:
        session.commit()
    return granted


def seed() -> None:
    settings = get_settings()
    session = get_session()
    try:
        user = _upsert_user(
            session,
            aad_object_id=DEV_BYPASS_AAD_OID,
            email=DEV_BYPASS_EMAIL,
            display_name=DEV_BYPASS_DISPLAY_NAME,
        )
        connection, suite, checks = ensure_probe_fixtures(session, user=user, settings=settings)
        # Plus a representative dataset (all six connection types, several suites
        # with varied checks, a cross-user share) for the UI / E2E smoke.
        summary = seed_demo_data(session, owner=user, secret_store=get_secret_store())
        operator_shares = _share_with_otp_operators(session, owner=user, settings=settings)
        print(
            "Seeded dev data: "
            f"user={user.email} probe_connection={connection.name} "
            f"probe_suite={suite.name} probe_checks={len(checks)} | "
            f"demo connections={summary['connections']} suites={summary['suites']} "
            f"checks={summary['checks']} shares={summary['shares']} "
            f"otp_operator_shares={operator_shares}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    seed()
