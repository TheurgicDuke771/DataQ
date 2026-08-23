"""What the access write costs, and the shape of that cost — G1 / #431 AC-3."""

from __future__ import annotations

import time
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

#: The two sizes whose overheads are compared. A 10x spread in results, so an
#: O(results) write would show up as a large ratio while jitter cannot.
_SMALL = 5
_LARGE = 50

_ROUNDS = 5

#: How much more the write may cost at 10x the results.
_MAX_GROWTH = 3.0


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


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2]


def _time_read(client: TestClient, run: Run) -> float:
    samples = []
    for _ in range(_ROUNDS):
        start = time.perf_counter()
        resp = client.get(f"/api/v1/runs/{run.id}")
        elapsed = time.perf_counter() - start
        assert resp.status_code == 200, resp.text
        samples.append(elapsed)
    return _median(samples)


def _overhead(client: TestClient, db_session: Any, monkeypatch: Any, results: int) -> float:
    """Median cost the access write adds to one read of `results` results."""
    from backend.app.services import audit_service

    run = _seed(db_session, results)
    client.get(f"/api/v1/runs/{run.id}")  # warm: first call pays import/compile costs

    audited = _time_read(client, run)
    monkeypatch.setattr(audit_service, "record_access", lambda *a, **k: None)
    baseline = _time_read(client, run)
    monkeypatch.undo()

    print(
        f"\n[G1 AC-3] {results:>3} results: read {baseline * 1000:6.2f}ms → "
        f"{audited * 1000:6.2f}ms with the access event "
        f"(+{max(audited - baseline, 0) * 1000:.2f}ms)"
    )
    return max(audited - baseline, 0.0)


def test_the_access_write_cost_does_not_grow_with_the_result_count(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    """The machine-independent property: one event per READ, so the write is O(1)."""
    small = _overhead(client, db_session, monkeypatch, _SMALL)
    large = _overhead(client, db_session, monkeypatch, _LARGE)

    # A floor guards against dividing by a near-zero measurement on a fast, idle
    # machine, which would manufacture an enormous ratio out of noise.
    growth = large / small if small > 0.0005 else 1.0
    print(f"[G1 AC-3] cost growth from {_SMALL} to {_LARGE} results: {growth:.2f}x\n")

    assert growth <= _MAX_GROWTH, (
        f"the access write costs {growth:.1f}x more at {_LARGE} results than at "
        f"{_SMALL} — it is scaling with the data, so the design has drifted from "
        "one event per READ. Check nothing is writing an event per result or "
        "querying per row."
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
