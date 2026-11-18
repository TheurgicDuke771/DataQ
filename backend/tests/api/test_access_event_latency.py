"""What the access write costs, and the shape of that cost — G1 / #431 AC-3 (counted, not timed)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import AuditEvent, Check, Connection, Result, Run, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app

#: The two sizes whose audit SQL is compared. A 10x spread in results, so an
#: O(results) write shows up as more statements.
_SMALL = 5
_LARGE = 50


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(db_session: Any, results: int) -> Run:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="orders", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    db_session.add(run)
    db_session.flush()
    for i in range(results):
        check = Check(
            suite_id=suite.id,
            name=f"c{i}",
            kind="expectation",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "line_total"},
        )
        db_session.add(check)
        db_session.flush()
        db_session.add(
            Result(
                run_id=run.id,
                check_id=check.id,
                status="fail",
                sample_failures={
                    "partial_unexpected_list": list(range(20)),
                    "unexpected_count": 20,
                },
            )
        )
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: owner
    return run


def _read_statements(client: TestClient, db_session: Any, results: int) -> list[str]:
    """Every SQL statement one read of a run with `results` results sends, by its leading text."""
    from sqlalchemy import event

    run = _seed(db_session, results)
    seen: list[str] = []
    connection = db_session.connection()

    def _record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        # Savepoint names carry a running counter, which differs between any two reads.
        seen.append(re.sub(r"sa_savepoint_\d+", "sa_savepoint", " ".join(statement.split()))[:80])

    event.listen(connection, "before_cursor_execute", _record)
    try:
        assert client.get(f"/api/v1/runs/{run.id}").status_code == 200
    finally:
        event.remove(connection, "before_cursor_execute", _record)
    return seen


def test_the_access_write_cost_does_not_grow_with_the_result_count(
    client: TestClient, db_session: Any
) -> None:
    """One event per READ, so the write is O(1): a read sends the same SQL at 5 and at 50
    results. Counting statements replaces the timing ratio this test used to assert, which flaked
    on shared CI runners (#2573, #2115): a millisecond-scale difference is mostly noise, while
    the cause it was measuring (an event or a query per result) shows up as more statements. Every
    statement is compared, not only the audit ones, so a query per row anywhere fails it too.
    What it cannot see is a slower single statement; that is a timing question, not a scaling one.
    """
    small = _read_statements(client, db_session, _SMALL)
    large = _read_statements(client, db_session, _LARGE)

    assert any(
        "audit_" in statement for statement in small
    ), "a read must send the access event to the audit tables"
    assert sorted(large) == sorted(small), (
        f"a read sends {len(large)} statements at {_LARGE} results but {len(small)} at "
        f"{_SMALL} — it is scaling with the data, so the design has drifted from one event per "
        "READ. Check nothing is writing an event per result or querying per row."
    )


def test_one_event_per_read_not_one_per_result(client: TestClient, db_session: Any) -> None:
    """The same property asserted directly, so it does not rest on timing at all."""
    run = _seed(db_session, _LARGE)
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200

    db_session.expire_all()
    events = list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == run.id, AuditEvent.action_class == "access"
            )
        )
    )
    assert len(events) == 1, f"{_LARGE} results must produce ONE event, got {len(events)}"
    assert events[0].after is not None
    assert events[0].after["result_count"] == _LARGE
