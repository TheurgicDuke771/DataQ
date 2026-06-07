"""Idempotent dev-data seed (run by scripts/setup.sh, safe to re-run).

Gives a fresh local database a minimal baseline so the UI and API aren't empty
on first boot:

- the **dev-bypass user** (the same fixed identity `auth_dev_bypass` resolves
  every request to), so seeded rows are owned by the user you actually log in as
  locally; and
- the **probe Connection + Suite + Check**, reusing `ensure_probe_fixtures` so
  the seed never drifts from what the Week-1 probe endpoint expects.

Both steps get-or-create, so running this repeatedly is a no-op. Run via:

    conda run -n dataq python backend/scripts/seed_dev.py
"""

from __future__ import annotations

from backend.app.core.auth import (
    DEV_BYPASS_AAD_OID,
    DEV_BYPASS_DISPLAY_NAME,
    DEV_BYPASS_EMAIL,
    _upsert_user,
)
from backend.app.core.config import get_settings
from backend.app.db.session import get_session
from backend.app.services.probe import ensure_probe_fixtures


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
        print(
            "Seeded dev data: "
            f"user={user.email} connection={connection.name} "
            f"suite={suite.name} checks={len(checks)}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    seed()
