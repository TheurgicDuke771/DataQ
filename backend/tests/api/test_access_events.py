"""Does reading regulated data actually record it? — G1 / #431."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import (
    AuditEvent,
    Check,
    CheckVersion,
    Connection,
    Result,
    Run,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import check_service


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(
    db_session: Any,
    *,
    sample: dict[str, Any] | None,
    column: str,
    expectation_type: str = "expect_column_values_to_not_be_null",
) -> tuple[User, Run]:
    """A suite with one failed check and one result carrying `sample`."""
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
    check = Check(
        suite_id=suite.id,
        name="values",
        kind="expectation",
        expectation_type=expectation_type,
        config={"column": column},
    )
    db_session.add(check)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status="fail", sample_failures=sample))
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: owner
    return owner, run


def _events(db_session: Any, action: str, entity_id: uuid.UUID) -> list[AuditEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == action,
                AuditEvent.entity_id == entity_id,
                AuditEvent.action_class == "access",
            )
        )
    )


def test_reading_a_run_records_an_access_event(client: TestClient, db_session: Any) -> None:
    """Who read which run's results, and when."""
    owner, run = _seed(
        db_session,
        sample={"partial_unexpected_list": [1, 2, 3], "unexpected_count": 3},
        column="line_total",
    )
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200, resp.text

    events = _events(db_session, "run_results.read", run.id)
    assert len(events) == 1
    event = events[0]
    assert event.action_class == "access", "a read is not a config event"
    assert event.actor_user_id == owner.id
    assert event.after is not None
    assert event.after["result_count"] == 1


def test_an_exposing_read_is_distinguishable_from_a_redacted_one(
    client: TestClient, db_session: Any
) -> None:
    """`exposed` is what makes the log answer the question it exists for."""
    _owner, safe_run = _seed(
        db_session,
        sample={"partial_unexpected_list": [1, 2, 3], "unexpected_count": 3},
        column="line_total",
    )
    client.get(f"/api/v1/runs/{safe_run.id}")
    safe = _events(db_session, "run_results.read", safe_run.id)[0]

    _owner2, pii_run = _seed(
        db_session,
        sample={"partial_unexpected_list": ["a@b.com"], "unexpected_count": 1},
        column="email",
    )
    client.get(f"/api/v1/runs/{pii_run.id}")
    pii = _events(db_session, "run_results.read", pii_run.id)[0]

    assert safe.after is not None and pii.after is not None
    assert safe.after["exposed"] is True, "a non-PII failing value WAS surfaced"
    assert safe.after["exposed_result_ids"], "and the event must say which result"
    assert pii.after["exposed"] is False, "a fully-masked sample surfaced nothing"
    assert pii.after["exposed_result_ids"] == []


def test_the_event_records_which_result_never_what_it_contained(
    client: TestClient, db_session: Any
) -> None:
    """ADR 0041 §2.6.3. Copying a sample into an append-only table with a LONGER
    retention would silently defeat both the #1253 purge and the #432 erasure
    path — an audit log that quietly becomes a second, unpurged copy of the
    personal data it is auditing is worse than no audit log.
    """
    secret = "alice@example.com"
    _owner, run = _seed(
        db_session,
        sample={"partial_unexpected_list": [secret], "unexpected_count": 1},
        column="line_total",
    )
    client.get(f"/api/v1/runs/{run.id}")

    import json

    event = _events(db_session, "run_results.read", run.id)[0]
    serialized = json.dumps({"before": event.before, "after": event.after})
    assert secret not in serialized
    assert "partial_unexpected_list" not in serialized


def test_a_failed_audit_write_does_not_fail_the_read(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    """The phase-2 contract, and the opposite of phase 1's."""
    from structlog.testing import capture_logs

    from backend.app.services import audit_service

    _owner, run = _seed(
        db_session,
        sample={"partial_unexpected_list": [1], "unexpected_count": 1},
        column="line_total",
    )

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(audit_service, "record", _boom)
    with capture_logs() as logs:
        resp = client.get(f"/api/v1/runs/{run.id}")
    monkeypatch.undo()

    assert resp.status_code == 200, "the read must survive an audit failure"
    assert resp.json()["results"], "and must still return its data"
    assert any(
        entry.get("event") == "audit_access_write_failed" for entry in logs
    ), "a compliance control that stops recording must be visible in telemetry"


# ── Review findings (PR #1459) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("observed", "column", "expectation_type", "expect_exposed", "why"),
    [
        (
            {"observed_value": ["a@b.com", "c@d.com"]},
            "email",
            "expect_column_values_to_not_be_null",
            False,
            "a fully MASKED distinct-value list exposes nothing — but it is non-None",
        ),
        (
            {"observed_value": 34680},
            "line_total",
            "expect_column_values_to_not_be_null",
            False,
            "a row count is a MEASUREMENT, not personal data, however large — even "
            "though this check has a real tested column",
        ),
        (
            {"error": "connection refused"},
            "line_total",
            "expect_column_values_to_not_be_null",
            False,
            "an error message is not a cell value",
        ),
        (
            {"observed_value": ["ACME", "GLOBEX"]},
            "vendor_name",
            "expect_column_values_to_not_be_null",
            True,
            "an UNMASKED distinct-value list is raw cells from the tested column",
        ),
        (
            {"unparsed_value": "not-a-date", "column": "order_ts"},
            "order_ts",
            "expect_column_values_to_not_be_null",
            True,
            "an unparsed cell that survived masking is a raw cell",
        ),
        (
            {"observed_value": "a very personal note"},
            "notes",
            "expect_column_max_to_be_between",
            True,
            "#1486: an UNMASKED scalar from a known cell-reporting expectation "
            "(max/min) is a real cell, same as the list case",
        ),
        (
            {"observed_value": "<redacted>"},
            "email",
            "expect_column_max_to_be_between",
            False,
            "#1486: a MASKED scalar exposes nothing, even from a cell-reporting type",
        ),
    ],
)
def test_observed_value_exposure_is_not_a_null_check(
    client: TestClient,
    db_session: Any,
    observed: dict[str, Any],
    column: str,
    expectation_type: str,
    expect_exposed: bool,
    why: str,
) -> None:
    """`exposed` must reflect what was SURFACED, not whether a field is non-None."""
    _owner, run = _seed(db_session, sample=None, column=column, expectation_type=expectation_type)
    result = db_session.scalars(select(Result).where(Result.run_id == run.id)).one()
    result.observed_value = observed
    db_session.commit()

    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200

    event = _events(db_session, "run_results.read", run.id)[0]
    assert event.after is not None
    assert event.after["exposed"] is expect_exposed, why


def test_the_access_commit_does_not_expire_the_callers_objects(
    client: TestClient, db_session: Any
) -> None:
    """`expire_on_commit` is True by default, and a read path commits in the
    MIDDLE of building its response.
    """
    _owner, run = _seed(
        db_session,
        sample={"partial_unexpected_list": [1], "unexpected_count": 1},
        column="line_total",
    )
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200
    assert resp.json()["results"], "the response must survive the access commit"
    # The caller's session must be left with its default semantics restored, or
    # every later commit in the process silently stops expiring.
    assert db_session.expire_on_commit is True


# ── Review finding on PR #1488 (#1489) ────────────────────────────────────────


def test_editing_a_check_does_not_retroactively_relabel_an_old_result(
    client: TestClient, db_session: Any
) -> None:
    """A result must be classified by what the check WAS when the row was
    written, not what it is now.
    """
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
    db_session.commit()

    check = check_service.create_check(
        db_session,
        suite_id=suite.id,
        name="avg",
        kind="expectation",
        expectation_type="expect_column_mean_to_be_between",
        config={"column": "line_total"},
        warn_threshold=None,
        fail_threshold=None,
        critical_threshold=None,
    )
    # Explicit created_at throughout, rather than relying on wall-clock separation between commits:
    # every server_default=func.now() row inside this fixture's outer transaction ties to the SAME
    # instant (Postgres' now() is fixed for a transaction's lifetime, and this fixture never really
    # commits until teardown), which collapses the ordering this test exists to prove — confirmed
    # by running it and observing all timestamps identical.
    version_1 = db_session.scalars(
        select(CheckVersion).where(CheckVersion.check_id == check.id, CheckVersion.version_no == 1)
    ).one()
    version_1.created_at = datetime(2026, 1, 1, tzinfo=UTC)  # T0
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    db_session.add(run)
    db_session.flush()
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="pass",
        observed_value={"observed_value": 1250.5},
        created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),  # T0 + 1h — after v1, before the edit
    )
    db_session.add(result)
    db_session.commit()

    # The edit #1489 is about: expectation_type AND the tested column both
    # change. Non-PII column (see the docstring above for why that matters).
    check_service.update_check(
        db_session,
        suite.id,
        check.id,
        expectation_type="expect_column_max_to_be_between",
        config={"column": "unit_price"},
    )
    version_2 = db_session.scalars(
        select(CheckVersion).where(CheckVersion.check_id == check.id, CheckVersion.version_no == 2)
    ).one()
    version_2.created_at = datetime(
        2026, 1, 2, tzinfo=UTC
    )  # T0 + 1 day — strictly after the result
    db_session.commit()

    app.dependency_overrides[get_current_user] = lambda: owner
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200, resp.text

    event = _events(db_session, "run_results.read", run.id)[0]
    assert event.after is not None
    assert event.after["exposed"] is False, (
        "the mean was never a cell — re-reading it after the check was edited to "
        "a max/min type must not retroactively count it as one"
    )
