"""Does a REST access event actually reach the database? — G1 / #431.

**This file exists because every other access-event test is structurally blind to
the question.** They run inside `db_session`'s shared, rolled-back transaction and
read the row back through that same session — which proves the row was *added*,
not that it *persists*. The first version of the access writer only `add`-ed it:
`get_db` never commits (services do, and a read route has nothing of its own to
commit), so `db.close()` rolled it straight back. The read returned 200, four
tests passed, and **nothing was ever recorded**. A compliance control that records
nothing while the suite says it works.

The only view that can tell those apart is an **independent connection**, which by
definition cannot see uncommitted work. That needs a database of its own — the
shared fixture's own seed data is uncommitted too, so a second connection to it
would see neither the seed nor the event and the test would pass vacuously for the
wrong reason.

So this builds a real database, seeds it with a real committed session, drives the
app against it, and verifies on a second connection. Slow by the standards of this
suite, and worth it exactly once, for the property that cannot be checked any
other way.

Needs a Postgres role able to CREATE DATABASE; skips otherwise.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.auth import get_current_user
from backend.app.db.models import (
    COMPARISON_KIND,
    AuditEvent,
    Base,
    Check,
    Connection,
    Result,
    Run,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.tests.conftest import TEST_DATABASE_URL

_DB = "dataq_access_persistence_probe"


@pytest.fixture
def probe_engine() -> Iterator[Any]:
    if not TEST_DATABASE_URL:
        pytest.skip("needs TEST_DATABASE_URL")
    admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            try:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{_DB}"'))
                conn.execute(text(f'CREATE DATABASE "{_DB}"'))
            except ProgrammingError as exc:  # pragma: no cover - permission-dependent
                pytest.skip(f"cannot create a probe database: {exc}")
    finally:
        admin.dispose()

    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{_DB}"
    engine = create_engine(url)
    try:
        # The whole schema, not a hand-picked subset: the read path joins more
        # tables than the ones this test writes (assets, incidents, …), and a
        # missing one surfaces as an opaque UndefinedTable rather than as the
        # property under test.
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_DB}"'))
        admin.dispose()


def test_a_rest_read_commits_its_access_event(probe_engine: Any) -> None:
    """Read on one connection; verify on another.

    The verifying session is opened *after* the request completes and shares
    nothing with it, so a row it can see is a row that was committed. That is the
    entire point of the file, and the assertion that the earlier tests could not
    make.
    """
    make_session = sessionmaker(bind=probe_engine)

    setup: Session = make_session()
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    setup.add(owner)
    setup.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    setup.add(conn)
    setup.flush()
    suite = Suite(name="orders", connection_id=conn.id, created_by=owner.id)
    setup.add(suite)
    setup.flush()
    check = Check(
        suite_id=suite.id,
        name="values",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "line_total"},
    )
    setup.add(check)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    setup.add(run)
    setup.flush()
    setup.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            sample_failures={"partial_unexpected_list": [1, 2], "unexpected_count": 2},
        )
    )
    # Committed, so the request's own connection can see it — the fixture
    # transaction that hides this problem is exactly what is avoided here.
    setup.commit()
    run_id, owner_id = run.id, owner.id
    setup.close()

    request_session: Session = make_session()
    app.dependency_overrides[get_db] = lambda: request_session
    app.dependency_overrides[get_current_user] = lambda: request_session.get(User, owner_id)
    try:
        resp = TestClient(app).get(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()
        request_session.close()  # rolls back anything left pending — the bug's mechanism

    verify: Session = make_session()
    try:
        events = list(
            verify.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == run_id,
                    AuditEvent.action_class == "access",
                )
            )
        )
    finally:
        verify.close()

    assert len(events) == 1, (
        "the access event did not survive the request's session — it was added but "
        "never committed, so nothing was recorded (G1/#431)"
    )
    assert events[0].actor_user_id == owner_id
    assert events[0].after is not None and events[0].after["exposed"] is True


def test_a_failed_report_render_leaves_no_committed_download_event(probe_engine: Any) -> None:
    """An event for a download that never happened is worse than a missing one —
    a reader cannot tell it from a real access.

    **This lives here, not beside the other access-event tests, for the same
    reason the persistence test does.** `record_access` commits, so a version that
    recorded the download BEFORE rendering would leave a real, committed event
    behind when the render failed. Inside the shared fixture transaction that is
    invisible: `get_db` rolls back on the exception and the rollback unwinds the
    fixture's savepoint, erasing the event — so the test passes either way, which
    is exactly what happened to the first version of it.

    Only a real database, where the commit is real and the rollback cannot undo
    it, can tell the two orderings apart.
    """
    from backend.app.api.v1 import runs as runs_api

    make_session = sessionmaker(bind=probe_engine)
    setup: Session = make_session()
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    setup.add(owner)
    setup.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    setup.add(conn)
    setup.flush()
    suite = Suite(name="cmp", connection_id=conn.id, created_by=owner.id)
    setup.add(suite)
    setup.flush()
    check = Check(
        suite_id=suite.id,
        name="cmp",
        kind=COMPARISON_KIND,
        expectation_type="expect_table_row_count_to_equal_other_table",
        source_connection_id=conn.id,
        config={"column": "line_total"},
    )
    setup.add(check)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    setup.add(run)
    setup.flush()
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        sample_failures={"partial_unexpected_list": [1], "unexpected_count": 1},
    )
    setup.add(result)
    setup.commit()
    run_id, result_id, owner_id = run.id, result.id, owner.id
    setup.close()

    request_session: Session = make_session()
    app.dependency_overrides[get_db] = lambda: request_session
    app.dependency_overrides[get_current_user] = lambda: request_session.get(User, owner_id)
    original = runs_api.build_report  # type: ignore[attr-defined]

    def _render_fails(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("render blew up")

    monkeypatch_target = "build_report"
    setattr(runs_api, monkeypatch_target, _render_fails)
    try:
        resp = TestClient(app, raise_server_exceptions=False).get(
            f"/api/v1/runs/{run_id}/results/{result_id}/comparison_report?fmt=csv"
        )
        assert resp.status_code >= 400, "the failed render must not return a file"
    finally:
        setattr(runs_api, monkeypatch_target, original)
        app.dependency_overrides.clear()
        request_session.close()

    verify: Session = make_session()
    try:
        events = list(
            verify.scalars(
                select(AuditEvent).where(AuditEvent.action == "comparison_report.download")
            )
        )
    finally:
        verify.close()
    assert events == [], (
        "a committed download event survived a render that failed — the access is "
        "recorded before the file exists, so the log claims a download that never "
        "happened"
    )
