"""The demo seed populates the Incidents panel (#761 fix batch, PR #775 review).

`scripts/demo_data.py` seeds failing results — without the incident sync a fresh
stack's AssetDetail showed an empty Incidents panel. `_seed_incidents` rolls the
seeded runs through the REAL lifecycle engine (`sync_incidents_for_run`), so this
asserts ≥1 open incident lands and that a re-run of the seed is idempotent (no
spurious occurrence attaches to the same runs).

Skips without TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from backend.app.core.secrets import get_secret_store
from backend.app.db.models import INCIDENT_ACTIVE_STATUSES, Incident, User
from backend.scripts.demo_data import seed_demo_data


def test_demo_seed_creates_open_incident_and_is_idempotent(
    db_session: Any, clean_kv_env: Any
) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email="seed-owner@example.com")
    db_session.add(owner)
    db_session.flush()

    summary = seed_demo_data(db_session, owner=owner, secret_store=get_secret_store())
    assert summary["incidents"] >= 1

    active = db_session.scalars(
        select(Incident).where(Incident.status.in_(INCIDENT_ACTIVE_STATUSES))
    ).all()
    assert active, "the seeded failing runs must open at least one incident"
    # Through the real engine: anchored + evidence snapshotted, not hand-inserted.
    assert all(i.asset_id is not None and i.evidence is not None for i in active)

    # Re-running the seed attaches nothing new (idempotency guard).
    occurrences_before = {i.id: i.occurrence_count for i in active}
    summary2 = seed_demo_data(db_session, owner=owner, secret_store=get_secret_store())
    assert summary2["incidents"] == 0
    for incident_id, count in occurrences_before.items():
        assert db_session.get(Incident, incident_id).occurrence_count == count
