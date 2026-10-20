"""Detected seasonal periods over REST and MCP, and promoting one to the calendar
(ADR 0048 decision 5, #2440 phase 5)."""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.datasources import monitors
from backend.app.db.models import (
    AuditEvent,
    Check,
    Connection,
    MonitorBaseline,
    SeasonalPeriod,
    Share,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import anomaly
from backend.app.services.monitor_baseline import insert_baseline_if_absent

Role = Callable[..., tuple[Any, dict[str, str]]]
_CONFIG: dict[str, Any] = {
    "target_metric": "period_row_count",
    "date_column": "ordered_at",
    "window": 14,
    "min_points": 7,
    "yearly": True,
}
_SALES = (
    (date(2023, 11, 24), date(2023, 11, 30)),
    (date(2024, 11, 29), date(2024, 12, 5)),
    (date(2025, 11, 28), date(2025, 12, 4)),
)
_SPRING = (date(2025, 4, 10), date(2025, 4, 13))
_PROMOTE = "/api/v1/admin/seasonal-periods/promote"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _history() -> list[anomaly.Observation]:
    rng = random.Random(11)
    out: list[anomaly.Observation] = []
    day = date(2023, 1, 2)
    while day < date(2026, 1, 15):
        value = 1000 * (1 + rng.uniform(-0.05, 0.05))
        if any(a <= day <= b for a, b in (*_SALES, _SPRING)):
            value *= 2.5
        out.append(
            anomaly.Observation(
                ts=datetime.combine(day, time.min, tzinfo=UTC),
                value=value,
                observed=day >= date(2025, 6, 1),
            )
        )
        day += timedelta(days=1)
    return out


def _seeded_check(
    client: TestClient, db_session: Any, config: dict[str, Any] | None = None
) -> tuple[str, str]:
    """A suite owned by dev-bypass, with an anomaly check whose baseline holds three years."""
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "shop", "user": "u"},
        secret_ref="ref",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    suite = client.post(
        "/api/v1/suites",
        json={
            "name": f"s-{uuid.uuid4().hex[:6]}",
            "connection_id": str(conn.id),
            "target": {"schema": "shop", "table": "orders"},
        },
    )
    assert suite.status_code == 201, suite.text
    suite_id = suite.json()["id"]
    created = client.post(
        f"/api/v1/suites/{suite_id}/checks",
        json={
            "name": "daily orders",
            "kind": "anomaly",
            "expectation_type": "monitor:anomaly",
            "fail_threshold": "3",
            "config": config or _CONFIG,
        },
    )
    assert created.status_code == 201, created.text
    check_id = created.json()["id"]
    params = monitors.anomaly_params(config or _CONFIG)
    insert_baseline_if_absent(
        db_session,
        check_id=uuid.UUID(check_id),
        kind="anomaly",
        baseline=anomaly.dump_baseline(_history(), params),
    )
    db_session.commit()
    return suite_id, check_id


def _detected(suite_id: str, check_id: str) -> str:
    return f"/api/v1/suites/{suite_id}/checks/{check_id}/seasonal-periods/detected"


# ── reads ─────────────────────────────────────────────────────────────────────


def test_a_viewer_with_a_share_reads_the_detected_periods(
    client: TestClient, db_session: Any, as_role: Role
) -> None:
    suite_id, check_id = _seeded_check(client, db_session)
    viewer, headers = as_role("viewer")
    db_session.add(Share(suite_id=uuid.UUID(suite_id), user_id=viewer.id, permission="view"))
    db_session.commit()

    resp = client.get(_detected(suite_id, check_id), headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reason"] is None
    assert [(p["kind"], p["start"], p["years_seen"]) for p in body["periods"]] == [
        ("seen_once", "2025-04-10", 1),
        ("recurring", "2025-11-28", 3),
    ]
    recurring = body["periods"][1]
    assert recurring["range"] == {
        "start_month": 11,
        "start_day": 24,
        "end_month": 12,
        "end_day": 5,
    }
    assert [o["backfilled_periods"] for o in recurring["occurrences"]] == [7, 7, 0]
    assert body["history"]["points"] == len(_history())
    assert body["thresholds"] == {"ratio": 1.5, "min_consecutive": 2, "same_time_days": 7}


def test_a_member_with_no_share_gets_a_404_not_the_periods(
    client: TestClient, db_session: Any, as_role: Role
) -> None:
    suite_id, check_id = _seeded_check(client, db_session)
    _, headers = as_role("member")

    resp = client.get(_detected(suite_id, check_id), headers=headers)

    assert resp.status_code == 404


def test_a_check_of_another_suite_is_a_404(client: TestClient, db_session: Any) -> None:
    suite_id, _ = _seeded_check(client, db_session)
    _, other_check = _seeded_check(client, db_session)

    assert client.get(_detected(suite_id, other_check)).status_code == 404


def test_a_whole_table_metric_says_why_it_has_nothing(client: TestClient, db_session: Any) -> None:
    suite_id, check_id = _seeded_check(client, db_session, {"target_metric": "row_count"})

    body = client.get(_detected(suite_id, check_id)).json()

    assert (body["reason"], body["periods"]) == ("not_a_per_period_metric", [])


def test_reading_the_detected_periods_stores_nothing(client: TestClient, db_session: Any) -> None:
    suite_id, check_id = _seeded_check(client, db_session)
    before = db_session.scalar(
        select(MonitorBaseline.baseline).where(MonitorBaseline.check_id == uuid.UUID(check_id))
    )

    client.get(_detected(suite_id, check_id))

    db_session.expire_all()
    after = db_session.scalar(
        select(MonitorBaseline.baseline).where(MonitorBaseline.check_id == uuid.UUID(check_id))
    )
    assert after == before
    assert db_session.scalars(select(SeasonalPeriod)).all() == []


# ── promote ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_only_an_admin_promotes(
    client: TestClient, db_session: Any, as_role: Role, role: str
) -> None:
    suite_id, check_id = _seeded_check(client, db_session)
    user, headers = as_role(role)
    db_session.add(Share(suite_id=uuid.UUID(suite_id), user_id=user.id, permission="view"))
    db_session.commit()

    resp = client.post(
        _PROMOTE,
        json={"suite_id": suite_id, "check_id": check_id, "start": "2025-11-28", "name": "BF"},
        headers=headers,
    )

    assert resp.status_code == 403
    assert db_session.scalars(select(SeasonalPeriod)).all() == []


def test_a_recurring_period_becomes_a_month_day_entry_attached_and_audited(
    client: TestClient, db_session: Any
) -> None:
    suite_id, check_id = _seeded_check(client, db_session)

    resp = client.post(
        _PROMOTE,
        json={
            "suite_id": suite_id,
            "check_id": check_id,
            "start": "2025-11-28",
            "name": "Black Friday",
            "attach": True,
        },
    )

    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert (entry["kind"], entry["start_month"], entry["start_day"]) == ("recurring", 11, 24)
    assert (entry["end_month"], entry["end_day"], entry["attached_checks"]) == (12, 5, 1)
    check = db_session.get(Check, uuid.UUID(check_id))
    db_session.refresh(check)
    assert check.config["seasonal_periods"] == [entry["id"]]
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action.in_(["seasonal_period.create", "check.update"]))
    ).all()
    assert sorted(e.action for e in events) == ["check.update", "seasonal_period.create"]
    created = next(e for e in events if e.action == "seasonal_period.create")
    assert created.after["promoted_from"]["check_id"] == check_id
    assert created.after["promoted_from"]["years_seen"] == 3
    # Attached now, the check's own detection names the entry beside the period.
    detected = client.get(_detected(suite_id, check_id)).json()
    assert [d["name"] for d in detected["periods"][1]["declared"]] == ["Black Friday"]


def test_a_period_seen_once_becomes_a_dated_entry_with_its_series(
    client: TestClient, db_session: Any
) -> None:
    suite_id, check_id = _seeded_check(client, db_session)

    resp = client.post(
        _PROMOTE,
        json={
            "suite_id": suite_id,
            "check_id": check_id,
            "start": "2025-04-10",
            "name": "Spring sale 2025",
            "series": "Spring sale",
        },
    )

    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert (entry["kind"], entry["start_date"], entry["end_date"]) == (
        "dated",
        "2025-04-10",
        "2025-04-13",
    )
    assert (entry["series"], entry["attached_checks"]) == ("Spring sale", 0)


def test_a_start_the_history_no_longer_shows_is_refused(
    client: TestClient, db_session: Any
) -> None:
    suite_id, check_id = _seeded_check(client, db_session)

    resp = client.post(
        _PROMOTE,
        json={"suite_id": suite_id, "check_id": check_id, "start": "2025-11-29", "name": "BF"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "detected_period_not_found"


def test_a_refused_attachment_leaves_no_entry_behind(client: TestClient, db_session: Any) -> None:
    """Without ``yearly`` the check cannot attach an entry; the entry is not created alone."""
    suite_id, check_id = _seeded_check(client, db_session, {**_CONFIG, "yearly": False})

    resp = client.post(
        _PROMOTE,
        json={
            "suite_id": suite_id,
            "check_id": check_id,
            "start": "2025-11-28",
            "name": "Black Friday",
            "attach": True,
        },
    )

    assert resp.status_code == 422, resp.text
    assert db_session.scalars(select(SeasonalPeriod)).all() == []
    assert "seasonal_periods" not in db_session.get(Check, uuid.UUID(check_id)).config


def test_a_taken_name_is_a_409(client: TestClient, db_session: Any) -> None:
    suite_id, check_id = _seeded_check(client, db_session)
    body = {"suite_id": suite_id, "check_id": check_id, "start": "2025-11-28", "name": "BF"}

    first = client.post(_PROMOTE, json=body)
    second = client.post(_PROMOTE, json={**body, "name": "bf"})

    assert first.status_code == 201
    assert second.status_code == 409


# ── MCP ───────────────────────────────────────────────────────────────────────


@contextmanager
def _mcp_as(monkeypatch: pytest.MonkeyPatch, db_session: Any, user: User) -> Iterator[Any]:
    from backend.app.mcp import server

    @contextmanager
    def _ctx() -> Any:
        yield db_session, user

    monkeypatch.setattr(server, "_ctx", _ctx)
    yield server


def test_mcp_reads_the_calendar_and_the_detected_periods(
    client: TestClient, db_session: Any, as_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite_id, check_id = _seeded_check(client, db_session)
    client.post(
        _PROMOTE,
        json={
            "suite_id": suite_id,
            "check_id": check_id,
            "start": "2025-11-28",
            "name": "Black Friday",
            "attach": True,
        },
    )
    viewer, _ = as_role("viewer")
    db_session.add(Share(suite_id=uuid.UUID(suite_id), user_id=viewer.id, permission="view"))
    db_session.commit()

    with _mcp_as(monkeypatch, db_session, viewer) as server:
        calendar = server.list_seasonal_periods()
        detected = server.get_detected_seasonal_periods(suite_id, check_id)

    assert [(e["name"], e["kind"], e["attached_checks"]) for e in calendar] == [
        ("Black Friday", "recurring", 1)
    ]
    assert (calendar[0]["start_month"], calendar[0]["end_day"]) == (11, 5)
    assert detected["reason"] is None
    assert [p["kind"] for p in detected["periods"]] == ["seen_once", "recurring"]
    assert detected["periods"][1]["declared"][0]["name"] == "Black Friday"
    assert detected["history"]["years"] > 3
