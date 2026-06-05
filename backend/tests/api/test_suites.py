"""Suite endpoint tests against a real Postgres (db_session) via TestClient.

get_db is overridden to the shared test session; auth runs in dev-bypass mode
(conftest), which upserts the dev user for the suite's created_by. A connection
(with its own owner) is inserted directly for the FK. Skips without
TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _connection(db_session: Any) -> Connection:
    """Insert a connection (with its own owner) for suites to bind to."""
    owner = User(aad_object_id=uuid.uuid4().hex, email="owner@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _payload(connection_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "finance-checks",
        "description": "row + null checks for finance tables",
        "connection_id": str(connection_id),
    }
    body.update(overrides)
    return body


# ───────────────────────── create ──────────────────────────────────


def test_create_returns_201(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    resp = client.post("/api/v1/suites", json=_payload(conn.id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "finance-checks"
    assert body["connection_id"] == str(conn.id)
    assert body["created_by"] is not None  # the dev-bypass user
    # persisted
    assert db_session.get(Suite, uuid.UUID(body["id"])) is not None


def test_create_unknown_connection_returns_422(client: TestClient) -> None:
    resp = client.post("/api/v1/suites", json=_payload(uuid.uuid4()))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_connection_invalid"


def test_create_blank_name_returns_422(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    resp = client.post("/api/v1/suites", json=_payload(conn.id, name=""))
    assert resp.status_code == 422


def test_create_allows_null_description(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    resp = client.post("/api/v1/suites", json=_payload(conn.id, description=None))
    assert resp.status_code == 201
    assert resp.json()["description"] is None


# ───────────────────────── read / list ─────────────────────────────


def test_get_returns_suite(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.get(f"/api/v1/suites/{sid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == sid


def test_get_unknown_returns_404(client: TestClient) -> None:
    resp = client.get(f"/api/v1/suites/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "suite_not_found"


def test_list_filters_by_connection(client: TestClient, db_session: Any) -> None:
    conn_a = _connection(db_session)
    conn_b = _connection(db_session)
    client.post("/api/v1/suites", json=_payload(conn_a.id, name="a1"))
    client.post("/api/v1/suites", json=_payload(conn_a.id, name="a2"))
    client.post("/api/v1/suites", json=_payload(conn_b.id, name="b1"))

    all_a = client.get(f"/api/v1/suites?connection_id={conn_a.id}").json()
    assert {s["name"] for s in all_a} == {"a1", "a2"}
    assert len(client.get("/api/v1/suites").json()) == 3  # unfiltered


# ───────────────────────── update / delete ─────────────────────────


def test_patch_updates_name_and_description(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.patch(f"/api/v1/suites/{sid}", json={"name": "renamed", "description": "new"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"
    assert resp.json()["description"] == "new"


def test_patch_unknown_returns_404(client: TestClient) -> None:
    resp = client.patch(f"/api/v1/suites/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_returns_204_then_404(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/suites/{sid}").status_code == 404


def test_delete_cascades_to_checks(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    # attach a check directly (check CRUD lands in the next PR)
    db_session.add(
        Check(suite_id=uuid.UUID(sid), name="row_count", expectation_type="expect_table_row_count")
    )
    db_session.commit()

    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 204
    remaining = db_session.scalars(select(Check).where(Check.suite_id == uuid.UUID(sid))).all()
    assert remaining == []  # cascade removed the child check


# ───────────────────────── access enforcement (PR-E2) ──────────────


def _owner_b_e_suite(db_session: Any) -> tuple[User, User, User, str]:
    owner = User(aad_object_id=uuid.uuid4().hex, email="owner@ex")
    b = User(aad_object_id=uuid.uuid4().hex, email="b@ex")
    e = User(aad_object_id=uuid.uuid4().hex, email="e@ex")  # no access
    db_session.add_all([owner, b, e])
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
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.commit()
    return owner, b, e, str(suite.id)


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _share(client: TestClient, owner: User, sid: str, target: User, perm: str) -> None:
    _as(owner)
    granted = client.post(
        f"/api/v1/suites/{sid}/shares", json={"user_id": str(target.id), "permission": perm}
    )
    assert granted.status_code == 201


def test_viewer_reads_but_cannot_write(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _share(client, owner, sid, b, "view")
    _as(b)
    assert client.get(f"/api/v1/suites/{sid}").status_code == 200
    patched = client.patch(f"/api/v1/suites/{sid}", json={"name": "x"})
    assert patched.status_code == 403
    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 403


def test_editor_updates_but_cannot_delete(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _share(client, owner, sid, b, "edit")
    _as(b)
    edited = client.patch(f"/api/v1/suites/{sid}", json={"name": "x"})
    assert edited.status_code == 200
    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 403


def test_admin_can_delete(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _share(client, owner, sid, b, "admin")
    _as(b)
    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 204


def test_outsider_sees_404_everywhere(client: TestClient, db_session: Any) -> None:
    _owner, _b, e, sid = _owner_b_e_suite(db_session)
    _as(e)
    assert client.get(f"/api/v1/suites/{sid}").status_code == 404
    patched = client.patch(f"/api/v1/suites/{sid}", json={"name": "x"})
    assert patched.status_code == 404
    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 404


def test_list_is_scoped_to_accessible_suites(client: TestClient, db_session: Any) -> None:
    owner, b, _e, owner_sid = _owner_b_e_suite(db_session)
    # B owns their own suite on the same connection (connections aren't access-gated)
    conn_id = db_session.get(Suite, uuid.UUID(owner_sid)).connection_id
    _as(b)
    b_sid = client.post(
        "/api/v1/suites", json={"name": "b-suite", "connection_id": str(conn_id)}
    ).json()["id"]
    # B sees only their own suite, not the owner's
    assert {s["id"] for s in client.get("/api/v1/suites").json()} == {b_sid}
    # once shared, the owner's suite appears for B too
    _share(client, owner, owner_sid, b, "view")
    _as(b)
    assert {s["id"] for s in client.get("/api/v1/suites").json()} == {b_sid, owner_sid}


# ───────────────────────── export / import ─────────────────────────


def _suite_with_checks(client: TestClient, db_session: Any) -> str:
    """A dev-owned suite with two checks (one thresholded, one plain)."""
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id, name="src")).json()["id"]
    db_session.add_all(
        [
            Check(
                suite_id=uuid.UUID(sid),
                name="rowcount",
                expectation_type="expect_table_row_count_to_be_between",
                kind="expectation",
                config={"min_value": 1},
                warn_threshold=Decimal("5"),
                fail_threshold=Decimal("7.5"),
            ),
            Check(
                suite_id=uuid.UUID(sid),
                name="notnull",
                expectation_type="expect_column_values_to_not_be_null",
                config={"column": "id"},
            ),
        ]
    )
    db_session.commit()
    return sid


def _check_set(checks: list[dict[str, Any]]) -> set[tuple[Any, ...]]:
    """Order-independent, representation-independent view of a document's checks."""
    return {
        (
            c["name"],
            c["kind"],
            c["expectation_type"],
            tuple(sorted(c["config"].items())),
            None if c["warn_threshold"] is None else Decimal(str(c["warn_threshold"])),
            None if c["fail_threshold"] is None else Decimal(str(c["fail_threshold"])),
            None if c["critical_threshold"] is None else Decimal(str(c["critical_threshold"])),
        )
        for c in checks
    }


def test_export_returns_document_without_db_identity(client: TestClient, db_session: Any) -> None:
    sid = _suite_with_checks(client, db_session)
    doc = client.get(f"/api/v1/suites/{sid}/export")
    assert doc.status_code == 200
    body = doc.json()
    assert body["version"] == 1
    assert body["name"] == "src"
    # no DB identity leaks into the portable document
    assert "id" not in body and "connection_id" not in body and "created_by" not in body
    assert len(body["checks"]) == 2
    for c in body["checks"]:
        assert "id" not in c and "suite_id" not in c
    # thresholds survive the trip (exact, regardless of number/string encoding)
    rowcount = next(c for c in body["checks"] if c["name"] == "rowcount")
    assert Decimal(str(rowcount["fail_threshold"])) == Decimal("7.5")


def test_export_requires_view_access(client: TestClient, db_session: Any) -> None:
    _owner, _b, e, sid = _owner_b_e_suite(db_session)
    _as(e)
    assert client.get(f"/api/v1/suites/{sid}/export").status_code == 404


def test_import_creates_owned_suite_with_checks(client: TestClient, db_session: Any) -> None:
    src = _suite_with_checks(client, db_session)
    document = client.get(f"/api/v1/suites/{src}/export").json()
    target = _connection(db_session)

    resp = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(target.id), "document": document},
    )
    assert resp.status_code == 201
    new = resp.json()
    assert new["id"] != src  # a fresh suite, not the source
    assert new["connection_id"] == str(target.id)  # bound to the chosen connection
    assert new["created_by"] is not None  # owned by the importer (dev user)
    # checks were recreated
    persisted = db_session.scalars(
        select(Check).where(Check.suite_id == uuid.UUID(new["id"]))
    ).all()
    assert {c.name for c in persisted} == {"rowcount", "notnull"}


def test_export_import_round_trips(client: TestClient, db_session: Any) -> None:
    src = _suite_with_checks(client, db_session)
    document = client.get(f"/api/v1/suites/{src}/export").json()
    target = _connection(db_session)

    new_id = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(target.id), "document": document},
    ).json()["id"]
    reexported = client.get(f"/api/v1/suites/{new_id}/export").json()

    assert reexported["name"] == document["name"]
    assert reexported["description"] == document["description"]
    assert _check_set(reexported["checks"]) == _check_set(document["checks"])


def test_import_unknown_connection_returns_422(client: TestClient, db_session: Any) -> None:
    src = _suite_with_checks(client, db_session)
    document = client.get(f"/api/v1/suites/{src}/export").json()
    resp = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(uuid.uuid4()), "document": document},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_import_connection_invalid"


def test_import_unknown_version_returns_422(client: TestClient, db_session: Any) -> None:
    target = _connection(db_session)
    resp = client.post(
        "/api/v1/suites/import",
        json={
            "connection_id": str(target.id),
            "document": {"version": 999, "name": "x", "description": None, "checks": []},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_import_invalid"


def test_import_unsupported_kind_is_atomic(client: TestClient, db_session: Any) -> None:
    target = _connection(db_session)
    before = db_session.scalar(select(func.count()).select_from(Suite))
    resp = client.post(
        "/api/v1/suites/import",
        json={
            "connection_id": str(target.id),
            "document": {
                "name": "x",
                "checks": [
                    {"name": "ok", "expectation_type": "expect_table_row_count_to_be_between"},
                    {
                        "name": "bad",
                        "kind": "freshness",
                        "expectation_type": "expect_column_max_to_be_between",
                    },
                ],
            },
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"
    # the valid check + suite must NOT have been written (validated before any write)
    after = db_session.scalar(select(func.count()).select_from(Suite))
    assert after == before
