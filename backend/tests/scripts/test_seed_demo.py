"""The demo profile's suites are valid, runnable checks (#1706)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from backend.app.db.models import Check, Connection, Schedule, User
from backend.scripts import seed_demo


def test_every_demo_suite_authors_through_the_real_services(db_session: Any) -> None:
    owner = User(id=uuid.uuid4(), aad_object_id=None, email="demo@example.com", role="admin")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=seed_demo.CONNECTION_NAME,
        type="postgres",
        env="dev",
        config={"host": "demo-warehouse", "database": "shop", "user": seed_demo.READER},
        secret_ref="kv-demo",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()

    suites = [seed_demo._suite(db_session, owner, conn, spec) for spec in seed_demo._SUITES]
    again = [seed_demo._suite(db_session, owner, conn, spec) for spec in seed_demo._SUITES]

    assert [s.id for s in again] == [s.id for s in suites]
    for suite, spec in zip(suites, seed_demo._SUITES, strict=True):
        names = set(db_session.scalars(select(Check.name).where(Check.suite_id == suite.id)))
        assert names == {check[0] for check in spec["checks"]}
        assert suite.target == {"schema": seed_demo.SCHEMA, "table": spec["table"]}
    schedules = db_session.scalars(select(Schedule)).all()
    assert [s.cron for s in schedules] == ["0 * * * *"]
