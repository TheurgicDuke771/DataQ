"""Idempotent dev-data seed (run by scripts/setup.sh, safe to re-run).

Gives a fresh local database a minimal baseline so the UI and API aren't empty
on first boot:

- the **dev-bypass user** (the same fixed identity `auth_dev_bypass` resolves
  every request to), so seeded rows are owned by the user you actually log in as
  locally; and
- the **probe Connection + Suite + Check**, reusing `ensure_probe_fixtures` so
  the seed never drifts from what the Week-1 probe endpoint expects.

Both steps get-or-create, so running this repeatedly is a no-op. Run as a module
(so `backend.*` imports resolve from the repo root):

    conda run -n dataq python -m backend.scripts.seed_dev
"""

from __future__ import annotations

from backend.app.core.auth import (
    DEV_BYPASS_AAD_OID,
    DEV_BYPASS_DISPLAY_NAME,
    DEV_BYPASS_EMAIL,
    _upsert_user,
)
from backend.app.core.config import get_settings
from backend.app.core.secrets import get_secret_store
from backend.app.db.session import get_session
from backend.app.services.probe import ensure_probe_fixtures
from backend.scripts.demo_data import seed_demo_data


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
        print(
            "Seeded dev data: "
            f"user={user.email} probe_connection={connection.name} "
            f"probe_suite={suite.name} probe_checks={len(checks)} | "
            f"demo connections={summary['connections']} suites={summary['suites']} "
            f"checks={summary['checks']} shares={summary['shares']}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    seed()
