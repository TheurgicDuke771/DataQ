"""`/admin/seasonal-periods` (ADR 0048 decision 5, #2440 phase 4): authz, CRUD, audit, and the
attachment round trips through check authoring, export/import and check versions."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import AuditEvent, Check, Connection, User
from backend.app.db.session import get_db
from backend.app.main import app

Role = Callable[..., tuple[Any, dict[str, str]]]
_BASE = "/api/v1/admin/seasonal-periods"
_BF = {
    "name": "Black Friday",
    "kind": "recurring",
    "start_month": 11,
    "start_day": 24,
    "end_month": 11,
    "end_day": 30,
}
_SPRING = {
    "name": "Spring sale 2026",
    "kind": "dated",
    "start_date": "2026-04-03",
    "end_date": "2026-04-12",
    "series": "Spring sale",
}
_CONFIG: dict[str, Any] = {
    "target_metric": "period_row_count",
    "date_column": "ordered_at",
    "window": 14,
    "min_points": 7,
    "yearly": True,
}
_ANOMALY: dict[str, Any] = {
    "name": "daily orders",
    "kind": "anomaly",
    "expectation_type": "monitor:anomaly",
    "fail_threshold": "3",
    "config": _CONFIG,
}


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _suite(client: TestClient, db_session: Any) -> str:
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
    resp = client.post(
        "/api/v1/suites",
        json={
            "name": f"s-{uuid.uuid4().hex[:6]}",
            "connection_id": str(conn.id),
            "target": {"schema": "shop", "table": "orders"},
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _check(client: TestClient, suite_id: str, ids: list[str]) -> Any:
    body = {**_ANOMALY, "config": {**_CONFIG, "seasonal_periods": ids}}
    return client.post(f"/api/v1/suites/{suite_id}/checks", json=body)


# ── authz ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_anyone_signed_in_reads_the_calendar_and_only_an_admin_changes_it(
    client: TestClient, as_role: Role, role: str
) -> None:
    created = client.post(_BASE, json=_BF).json()
    _, headers = as_role(role)

    statuses = {
        "list": client.get(_BASE, headers=headers).status_code,
        "get": client.get(f"{_BASE}/{created['id']}", headers=headers).status_code,
        "create": client.post(_BASE, json=_SPRING, headers=headers).status_code,
        "update": client.patch(
            f"{_BASE}/{created['id']}", json={"name": "x"}, headers=headers
        ).status_code,
        "delete": client.delete(f"{_BASE}/{created['id']}", headers=headers).status_code,
    }

    assert statuses == {"list": 200, "get": 200, "create": 403, "update": 403, "delete": 403}
    assert client.get(f"{_BASE}/{created['id']}").json()["name"] == "Black Friday"


def test_an_admin_runs_the_whole_lifecycle_and_each_change_is_audited(
    client: TestClient, as_role: Role, db_session: Any
) -> None:
    _, admin = as_role("admin")

    created = client.post(_BASE, json=_BF, headers=admin)
    body = created.json()
    updated = client.patch(f"{_BASE}/{body['id']}", json={"end_day": 29}, headers=admin)
    listed = client.get(_BASE, headers=admin).json()
    deleted = client.delete(f"{_BASE}/{body['id']}", headers=admin)

    assert created.status_code == 201
    assert (body["kind"], body["attached_checks"], body["series"]) == ("recurring", 0, None)
    assert updated.status_code == 200 and updated.json()["end_day"] == 29
    assert [e["id"] for e in listed] == [body["id"]]
    assert deleted.status_code == 204
    assert client.get(f"{_BASE}/{body['id']}").status_code == 404
    actions = db_session.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_type == "seasonal_period")
    ).all()
    assert sorted(actions) == [
        "seasonal_period.create",
        "seasonal_period.delete",
        "seasonal_period.update",
    ]


# ── validation ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({**_BF, "name": "x" * 129}, 422, None),
        ({**_BF, "name": "bad\x00name"}, 422, None),
        ({**_BF, "kind": "weekly"}, 422, None),
        ({**_BF, "start_day": 31}, 422, "seasonal_period_invalid"),
        ({**_BF, "series": "x"}, 422, "seasonal_period_invalid"),
        ({**_SPRING, "end_date": "2026-04-02"}, 422, "seasonal_period_invalid"),
        ({**_SPRING, "start_month": 4}, 422, "seasonal_period_invalid"),
        ({**_BF, "unknown": 1}, 422, None),
    ],
)
def test_an_invalid_entry_is_refused_and_nothing_is_stored(
    client: TestClient, payload: dict[str, Any], status: int, code: str | None
) -> None:
    resp = client.post(_BASE, json=payload)

    listed = client.get(_BASE).json()

    assert resp.status_code == status
    if code is not None:
        assert resp.json()["error"]["code"] == code
    assert listed == []


def test_a_case_variant_of_an_existing_name_is_a_409(client: TestClient) -> None:
    client.post(_BASE, json=_BF)

    resp = client.post(_BASE, json={**_BF, "name": "black friday"})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "seasonal_period_name_taken"


def test_29_february_is_accepted_as_a_recurring_bound(client: TestClient) -> None:
    resp = client.post(
        _BASE,
        json={
            **_BF,
            "name": "Leap",
            "start_month": 2,
            "start_day": 29,
            "end_month": 3,
            "end_day": 1,
        },
    )

    assert resp.status_code == 201 and resp.json()["start_day"] == 29


# ── attachments ───────────────────────────────────────────────────────────────


def test_a_check_attaches_known_entries_only_and_the_entry_then_cannot_be_deleted(
    client: TestClient, db_session: Any
) -> None:
    entry = client.post(_BASE, json=_BF).json()
    ghost = str(uuid.uuid4())
    suite_id = _suite(client, db_session)

    refused = _check(client, suite_id, [entry["id"], ghost])
    saved = _check(client, suite_id, [entry["id"]])
    delete = client.delete(f"{_BASE}/{entry['id']}")

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "check_config_invalid"
    assert ghost in refused.json()["error"]["message"]
    assert saved.status_code == 201
    assert saved.json()["config"]["seasonal_periods"] == [entry["id"]]
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "seasonal_period_in_use"
    assert [c["id"] for c in delete.json()["error"]["detail"]["checks"]] == [saved.json()["id"]]
    assert client.get(f"{_BASE}/{entry['id']}").json()["attached_checks"] == 1


def test_an_attached_id_is_stored_canonically_and_matched_whatever_its_spelling(
    client: TestClient, db_session: Any
) -> None:
    """The delete guard searches the stored config: an id spelled in upper case or without
    hyphens must still count as an attachment."""
    entry = client.post(_BASE, json=_BF).json()
    suite_id = _suite(client, db_session)
    shouted = entry["id"].upper()

    saved = _check(client, suite_id, [shouted])
    stored = db_session.get(Check, uuid.UUID(saved.json()["id"]))
    # A row written by another door with a non-canonical spelling.
    stored.config = {**stored.config, "seasonal_periods": [entry["id"].replace("-", "")]}
    db_session.commit()
    delete = client.delete(f"{_BASE}/{entry['id']}")
    read, listed = client.get(f"{_BASE}/{entry['id']}").json(), client.get(_BASE).json()

    assert saved.status_code == 201
    assert saved.json()["config"]["seasonal_periods"] == [entry["id"]]
    assert delete.status_code == 409
    assert (read["attached_checks"], listed[0]["attached_checks"]) == (1, 1)


def test_a_kind_change_sends_the_new_kinds_fields_only(client: TestClient) -> None:
    entry = client.post(_BASE, json=_BF).json()

    resp = client.patch(
        f"{_BASE}/{entry['id']}",
        json={"kind": "dated", "start_date": "2026-11-24", "end_date": "2026-11-30"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["kind"], body["start_date"], body["start_month"], body["end_day"]) == (
        "dated",
        "2026-11-24",
        None,
        None,
    )


def test_a_case_only_correction_of_the_only_entry_of_a_series_lands(client: TestClient) -> None:
    entry = client.post(_BASE, json={**_SPRING, "series": "spring sale"}).json()

    resp = client.patch(f"{_BASE}/{entry['id']}", json={"series": "Spring Sale"})

    assert resp.status_code == 200
    assert resp.json()["series"] == "Spring Sale"


def test_attaching_without_yearly_or_on_a_whole_table_metric_is_refused(
    client: TestClient, db_session: Any
) -> None:
    entry = client.post(_BASE, json=_BF).json()
    suite_id = _suite(client, db_session)

    no_yearly = client.post(
        f"/api/v1/suites/{suite_id}/checks",
        json={
            **_ANOMALY,
            "config": {**_CONFIG, "yearly": False, "seasonal_periods": [entry["id"]]},
        },
    )
    whole_table = client.post(
        f"/api/v1/suites/{suite_id}/checks",
        json={
            **_ANOMALY,
            "config": {"target_metric": "row_count", "seasonal_periods": [entry["id"]]},
        },
    )

    assert no_yearly.status_code == 422 and "yearly" in no_yearly.json()["error"]["message"]
    assert whole_table.status_code == 422


def test_attachments_round_trip_through_export_and_import(
    client: TestClient, db_session: Any
) -> None:
    entry = client.post(_BASE, json=_BF).json()
    source = _suite(client, db_session)
    _check(client, source, [entry["id"]])
    document = client.get(f"/api/v1/suites/{source}/export").json()
    target_conn = db_session.scalar(select(Connection))

    imported = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(target_conn.id), "document": {**document, "name": "copy"}},
    )
    checks = client.get(f"/api/v1/suites/{imported.json()['id']}/checks").json()

    assert document["checks"][0]["config"]["seasonal_periods"] == [entry["id"]]
    assert imported.status_code == 201, imported.text
    assert checks[0]["config"]["seasonal_periods"] == [entry["id"]]
    assert client.get(f"{_BASE}/{entry['id']}").json()["attached_checks"] == 2


def test_importing_a_document_naming_an_entry_this_workspace_lacks_is_refused(
    client: TestClient, db_session: Any
) -> None:
    source = _suite(client, db_session)
    entry = client.post(_BASE, json=_BF).json()
    _check(client, source, [entry["id"]])
    document = client.get(f"/api/v1/suites/{source}/export").json()
    foreign = str(uuid.uuid4())
    document["checks"][0]["config"]["seasonal_periods"] = [foreign]
    target_conn = db_session.scalar(select(Connection))

    resp = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(target_conn.id), "document": {**document, "name": "copy"}},
    )

    assert resp.status_code == 422
    assert foreign in resp.json()["error"]["message"]


def test_attachments_round_trip_through_check_versions(client: TestClient, db_session: Any) -> None:
    entry = client.post(_BASE, json=_BF).json()
    suite_id = _suite(client, db_session)
    check = _check(client, suite_id, [entry["id"]]).json()
    base = f"/api/v1/suites/{suite_id}/checks/{check['id']}"

    detached = client.patch(base, json={"config": dict(_CONFIG)})
    versions = client.get(f"{base}/versions").json()
    restored = client.post(f"{base}/versions/1/restore")

    assert detached.status_code == 200
    assert "seasonal_periods" not in detached.json()["config"]
    assert [v["config"].get("seasonal_periods") for v in versions] == [None, [entry["id"]]]
    assert restored.status_code == 200
    assert restored.json()["config"]["seasonal_periods"] == [entry["id"]]
    assert client.get(base).json()["config"]["seasonal_periods"] == [entry["id"]]


def test_restoring_a_version_that_names_a_deleted_entry_is_refused(
    client: TestClient, db_session: Any
) -> None:
    entry = client.post(_BASE, json=_BF).json()
    suite_id = _suite(client, db_session)
    check = _check(client, suite_id, [entry["id"]]).json()
    base = f"/api/v1/suites/{suite_id}/checks/{check['id']}"
    client.patch(base, json={"config": dict(_CONFIG)})
    deleted = client.delete(f"{_BASE}/{entry['id']}")
    assert deleted.status_code == 204

    restored = client.post(f"{base}/versions/1/restore")

    assert restored.status_code == 422
    assert entry["id"] in restored.json()["error"]["message"]
