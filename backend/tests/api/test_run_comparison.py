"""`GET /runs/{id}/compare/{other}` — the diff of two runs of one suite."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
    Share,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.run_comparison import metric_delta, status_change

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = _T0 + timedelta(days=1)


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _user(db_session: Any, email: str) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=email)
    db_session.add(user)
    db_session.flush()
    return user


def _suite(db_session: Any, owner: User, *, policy: dict[str, Any] | None = None) -> Suite:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name="orders",
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
        column_policy=policy,
    )
    db_session.add(suite)
    db_session.flush()
    return suite


def _check(db_session: Any, suite: Suite, name: str, **kw: Any) -> Check:
    check = Check(
        suite_id=suite.id,
        name=name,
        expectation_type=kw.pop("expectation_type", "expect_column_values_to_not_be_null"),
        config=kw.pop("config", {"column": name}),
        **kw,
    )
    db_session.add(check)
    db_session.flush()
    return check


def _run(db_session: Any, suite: Suite, *, at: datetime, status: str = "succeeded") -> Run:
    run = Run(
        suite_id=suite.id,
        status=status,
        triggered_by="manual",
        created_at=at,
        started_at=at,
        finished_at=at + timedelta(seconds=5),
    )
    db_session.add(run)
    db_session.flush()
    return run


def _result(
    db_session: Any,
    run: Run,
    check: Check,
    status: str,
    *,
    metric: str | None = None,
    observed: dict[str, Any] | None = None,
) -> Result:
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status=status,
        metric_value=Decimal(metric) if metric is not None else None,
        observed_value=observed,
        created_at=run.created_at,
    )
    db_session.add(result)
    db_session.flush()
    return result


def _compare(client: TestClient, base: Run, target: Run) -> Any:
    return client.get(f"/api/v1/runs/{base.id}/compare/{target.id}")


def _rows_by_label(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["label"]: row for row in body["rows"]}


# ───────────────────────────── the diff itself ─────────────────────────────


def test_compare_diffs_status_metric_added_and_removed(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    suite = _suite(db_session, owner)
    regressed = _check(db_session, suite, "regressed")
    improved = _check(db_session, suite, "improved")
    drifted = _check(db_session, suite, "drifted")
    removed = _check(db_session, suite, "removed")
    added = _check(db_session, suite, "added")
    half_metric = _check(db_session, suite, "half_metric")
    errored = _check(db_session, suite, "errored")
    base = _run(db_session, suite, at=_T0)
    target = _run(db_session, suite, at=_T1)
    _result(db_session, base, regressed, "pass", metric="0")
    _result(db_session, target, regressed, "fail", metric="12.5")
    _result(db_session, base, improved, "critical", metric="40")
    _result(db_session, target, improved, "warn", metric="4")
    _result(db_session, base, drifted, "pass", metric="10")
    _result(db_session, target, drifted, "pass", metric="12")
    _result(db_session, base, removed, "pass", metric="1")
    _result(db_session, target, added, "warn", metric="3")
    _result(db_session, base, half_metric, "skip")
    _result(db_session, target, half_metric, "pass", metric="7")
    _result(db_session, base, errored, "pass", metric="0")
    _result(db_session, target, errored, "error")
    db_session.commit()

    _as(owner)
    resp = _compare(client, base, target)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["base"]["id"] == str(base.id) and body["target"]["id"] == str(target.id)
    assert body["base"]["results_final"] is True and body["target"]["results_final"] is True
    # The outcome counts are grafted like `/runs` does (#571): 6 results, skip excluded.
    assert body["base"]["checks_total"] == 5 and body["base"]["checks_passed"] == 4
    assert body["summary"] == {
        "changed": 4,  # regressed + improved + half_metric (skip→pass) + errored (pass→error)
        "unchanged": 1,
        "improved": 1,
        "regressed": 1,
        "added": 1,
        "removed": 1,
        "metric_changed": 5,  # regressed, improved, drifted, half_metric (null→7), errored (0→null)
    }
    rows = _rows_by_label(body)
    assert [r["label"] for r in body["rows"]] == sorted(rows, key=str.casefold)

    assert rows["regressed"]["delta"] == {
        "status_change": "regressed",
        "metric_delta": 12.5,
        "metric_delta_pct": None,  # base was 0: no percentage is defined
        "metric_changed": True,
    }
    assert rows["improved"]["delta"]["status_change"] == "improved"
    assert rows["improved"]["delta"]["metric_delta"] == -36.0
    assert rows["improved"]["delta"]["metric_delta_pct"] == pytest.approx(-90.0)
    assert rows["drifted"]["delta"] == {
        "status_change": "same",
        "metric_delta": 2.0,
        "metric_delta_pct": pytest.approx(20.0),
        "metric_changed": True,
    }
    assert rows["removed"]["base"]["status"] == "pass" and rows["removed"]["target"] is None
    assert rows["removed"]["delta"]["status_change"] == "removed"
    assert rows["added"]["base"] is None and rows["added"]["target"]["status"] == "warn"
    assert rows["added"]["delta"]["status_change"] == "added"
    assert rows["added"]["delta"]["metric_delta"] is None
    # A null metric on one side: the metric CHANGED, but no delta can be stated.
    assert rows["half_metric"]["delta"] == {
        "status_change": "changed",
        "metric_delta": None,
        "metric_delta_pct": None,
        "metric_changed": True,
    }
    # An operational status has no rank: pass→error is neither improved nor regressed.
    assert rows["errored"]["delta"]["status_change"] == "changed"


@pytest.mark.parametrize(
    ("base", "target", "expected"),
    [
        (None, "pass", "added"),
        ("pass", None, "removed"),
        ("warn", "warn", "same"),
        ("pass", "warn", "regressed"),
        ("warn", "critical", "regressed"),
        ("critical", "fail", "improved"),
        ("fail", "pass", "improved"),
        ("skip", "pass", "changed"),
        ("pass", "skip", "changed"),
        ("error", "skip", "changed"),
    ],
)
def test_status_change_uses_the_shared_severity_ranking(
    base: str | None, target: str | None, expected: str
) -> None:
    assert status_change(base, target) == expected


def test_metric_delta_is_undefined_without_both_sides_or_a_nonzero_base() -> None:
    assert metric_delta(None, 3.0) == (None, None)
    assert metric_delta(3.0, None) == (None, None)
    assert metric_delta(0.0, 3.0) == (3.0, None)
    assert metric_delta(-4.0, -2.0) == (2.0, 50.0)


# ───────────────────── each side as the check WAS at that run ──────────────


def test_each_side_carries_the_check_as_it_was_at_that_run(
    client: TestClient, db_session: Any
) -> None:
    """A check renamed, re-engined and re-classified between the two runs renders the OLD
    identity on the base side and the NEW one on the target side — never today's on both."""
    owner = _user(db_session, "o@ex")
    suite = _suite(db_session, owner)
    check = _check(
        db_session,
        suite,
        "orders_new_name",
        engine="dmf",
        dimension="completeness",
        config={"column": "ORDER_ID"},
    )
    db_session.add_all(
        [
            CheckVersion(
                check_id=check.id,
                version_no=1,
                name="orders_old_name",
                kind="expectation",
                engine="gx",
                expectation_type="expect_column_values_to_not_be_null",
                dimension=None,
                config={"column": "LEGACY_ID"},
                created_at=_T0 - timedelta(hours=1),
            ),
            CheckVersion(
                check_id=check.id,
                version_no=2,
                name="orders_new_name",
                kind="expectation",
                engine="dmf",
                expectation_type="expect_column_values_to_not_be_null",
                dimension="completeness",
                config={"column": "ORDER_ID"},
                created_at=_T0 + timedelta(hours=12),
            ),
        ]
    )
    base = _run(db_session, suite, at=_T0)
    target = _run(db_session, suite, at=_T1)
    _result(db_session, base, check, "pass", metric="0")
    _result(db_session, target, check, "fail", metric="5")
    db_session.commit()

    _as(owner)
    body = _compare(client, base, target).json()
    (row,) = body["rows"]
    assert row["label"] == "orders_new_name"
    assert row["base"]["check"] == {
        "name": "orders_old_name",
        "kind": "expectation",
        "engine": "gx",
        "expectation_type": "expect_column_values_to_not_be_null",
        "dimension": None,
        "column": "LEGACY_ID",
    }
    assert row["target"]["check"]["name"] == "orders_new_name"
    assert row["target"]["check"]["engine"] == "dmf"
    assert row["target"]["check"]["dimension"] == "completeness"
    assert row["target"]["check"]["column"] == "ORDER_ID"


def test_a_removed_check_is_labelled_by_its_base_side_name(
    client: TestClient, db_session: Any
) -> None:
    owner = _user(db_session, "o@ex")
    suite = _suite(db_session, owner)
    check = _check(db_session, suite, "only_in_base")
    base = _run(db_session, suite, at=_T0)
    target = _run(db_session, suite, at=_T1)
    _result(db_session, base, check, "pass")
    db_session.commit()
    _as(owner)
    body = _compare(client, base, target).json()
    assert body["rows"][0]["label"] == "only_in_base"
    assert body["summary"]["removed"] == 1


# ──────────────────────────────── redaction ────────────────────────────────


def test_observed_values_are_redacted_on_both_sides(client: TestClient, db_session: Any) -> None:
    """The same path the run-detail read uses: a PII column's observed values are masked."""
    owner = _user(db_session, "o@ex")
    suite = _suite(db_session, owner, policy={"pii_columns": ["email"]})
    check = _check(
        db_session,
        suite,
        "email domain",
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": "email"},
    )
    base = _run(db_session, suite, at=_T0)
    target = _run(db_session, suite, at=_T1)
    _result(db_session, base, check, "fail", observed={"observed_value": ["alice@example.com"]})
    _result(db_session, target, check, "fail", observed={"observed_value": ["bob@example.com"]})
    db_session.commit()

    _as(owner)
    resp = _compare(client, base, target)
    assert resp.status_code == 200, resp.text
    serialized = json.dumps(resp.json())
    assert "alice@example.com" not in serialized
    assert "bob@example.com" not in serialized
    (row,) = resp.json()["rows"]
    assert row["base"]["observed_value"]["observed_value"] == ["<redacted>"]
    assert row["target"]["observed_value"]["observed_value"] == ["<redacted>"]
    assert "sample_failures" not in row["base"]


def test_compare_records_one_access_event_per_run(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    suite = _suite(db_session, owner)
    check = _check(db_session, suite, "c")
    base = _run(db_session, suite, at=_T0)
    target = _run(db_session, suite, at=_T1)
    _result(db_session, base, check, "pass")
    _result(db_session, target, check, "pass")
    db_session.commit()
    _as(owner)
    assert _compare(client, base, target).status_code == 200
    db_session.expire_all()
    events = list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "run_results.read",
                AuditEvent.entity_id.in_([base.id, target.id]),
                AuditEvent.action_class == "access",
            )
        )
    )
    assert {e.entity_id for e in events} == {base.id, target.id}
    assert all(e.after["surface"] == "run_comparison" for e in events)


# ───────────────────────────── honesty + refusals ──────────────────────────


def test_a_running_side_is_reported_as_not_final(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    suite = _suite(db_session, owner)
    check = _check(db_session, suite, "c")
    base = _run(db_session, suite, at=_T0)
    target = _run(db_session, suite, at=_T1, status="running")
    _result(db_session, base, check, "pass")
    db_session.commit()
    _as(owner)
    body = _compare(client, base, target).json()
    assert body["base"]["results_final"] is True
    assert body["target"]["results_final"] is False
    # The partial side reads as "removed" today — the flag is what tells a reader not to trust it.
    assert body["summary"]["removed"] == 1


def test_runs_of_different_suites_are_refused(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    run_a = _run(db_session, _suite(db_session, owner), at=_T0)
    run_b = _run(db_session, _suite(db_session, owner), at=_T1)
    db_session.commit()
    _as(owner)
    resp = _compare(client, run_a, run_b)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "runs_not_comparable"


def test_a_run_is_not_comparable_with_itself(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    run = _run(db_session, _suite(db_session, owner), at=_T0)
    db_session.commit()
    _as(owner)
    resp = _compare(client, run, run)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "runs_not_comparable"


def test_unknown_run_in_either_position_is_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    run = _run(db_session, _suite(db_session, owner), at=_T0)
    db_session.commit()
    _as(owner)
    assert client.get(f"/api/v1/runs/{uuid.uuid4()}/compare/{run.id}").status_code == 404
    assert client.get(f"/api/v1/runs/{run.id}/compare/{uuid.uuid4()}").status_code == 404


def test_an_unshared_suites_run_is_404_in_both_positions(
    client: TestClient, db_session: Any
) -> None:
    """Existence-hiding holds for BOTH ids: a caller who can see one suite learns nothing
    about a run of another, whether it is given as the base or the other run."""
    owner = _user(db_session, "o@ex")
    stranger = _user(db_session, "s@ex")
    mine = _suite(db_session, stranger)
    theirs = _suite(db_session, owner)
    my_run = _run(db_session, mine, at=_T0)
    their_run = _run(db_session, theirs, at=_T1)
    db_session.commit()

    _as(stranger)
    assert _compare(client, my_run, their_run).status_code == 404
    assert _compare(client, their_run, my_run).status_code == 404
    # A `view` share on the other suite lifts the 404 — the pair is then refused for the
    # honest reason (different suites), proving it was the gate and not the pairing rule.
    db_session.add(Share(suite_id=theirs.id, user_id=stranger.id, permission="view"))
    db_session.commit()
    assert _compare(client, my_run, their_run).status_code == 422
    assert _compare(client, their_run, my_run).status_code == 422
