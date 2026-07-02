"""Suite endpoint tests against a real Postgres (db_session) via TestClient.

get_db is overridden to the shared test session; auth runs in dev-bypass mode
(conftest), which upserts the dev user for the suite's created_by. A connection
(with its own owner) is inserted directly for the FK. Skips without
TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import profile_service


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
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _orchestration_connection(db_session: Any, provider: str = "adf") -> Connection:
    """Insert an ADF/Airflow connection — an orchestration provider, never a
    suite datasource (CLAUDE.md §4)."""
    owner = User(aad_object_id=uuid.uuid4().hex, email="orch@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"{provider}-{uuid.uuid4().hex[:8]}",
        type=provider,
        env="prod",
        config={},
        secret_ref=f"kv-{provider}",
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


def test_create_on_orchestration_connection_rejected(client: TestClient, db_session: Any) -> None:
    # ADF/Airflow are orchestration providers, never suite datasources (#242).
    for provider in ("adf", "airflow"):
        conn = _orchestration_connection(db_session, provider)
        resp = client.post("/api/v1/suites", json=_payload(conn.id))
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


# ───────────────────────── run target (#215) ───────────────────────


def test_create_with_target_persists_storage_shape(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)  # snowflake
    resp = client.post(
        "/api/v1/suites",
        json=_payload(conn.id, target={"table": "ORDERS", "schema": "SALES"}),
    )
    assert resp.status_code == 201
    # Stored with the canonical `schema` key (not the `schema_` alias), no nulls.
    assert resp.json()["target"] == {"table": "ORDERS", "schema": "SALES"}


def test_create_with_flatfile_batch_target_persists(client: TestClient, db_session: Any) -> None:
    """A flat-file batch target (pattern/strategy/prefix) round-trips through the
    API — the SuiteTarget model must carry the batch keys, else the batch run path
    (A4) is unreachable (the keys would be stripped before persistence)."""
    owner = User(aad_object_id=uuid.uuid4().hex, email="ff@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"s3-{uuid.uuid4().hex[:8]}",
        type="s3",
        env="dev",
        config={"bucket": "b", "region": "r"},
        secret_ref="kv-s3",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    target = {
        "prefix": "orders/",
        "pattern": r"orders_(\d{4}-\d{2}-\d{2})\.csv",
        "strategy": "latest",
    }
    resp = client.post("/api/v1/suites", json=_payload(conn.id, target=target))
    assert resp.status_code == 201
    assert resp.json()["target"] == target  # batch keys survived to storage


def test_create_without_target_is_null(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    resp = client.post("/api/v1/suites", json=_payload(conn.id))
    assert resp.status_code == 201
    assert resp.json()["target"] is None


def test_create_with_wrong_datasource_target_returns_422(
    client: TestClient, db_session: Any
) -> None:
    conn = _connection(db_session)  # snowflake needs `table`, not `path`
    resp = client.post("/api/v1/suites", json=_payload(conn.id, target={"path": "data/o.csv"}))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_target_invalid"


def test_patch_sets_target(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.patch(f"/api/v1/suites/{sid}", json={"target": {"table": "T2"}})
    assert resp.status_code == 200
    assert resp.json()["target"] == {"table": "T2"}


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


def test_delete_succeeds_after_a_run_with_results(client: TestClient, db_session: Any) -> None:
    """#540: results.check_id was the only FK without an ondelete, so deleting
    a suite whose checks had RESULT rows hit fk_results_check_id_checks and
    500'd — any suite that had ever run was undeletable. Found live (W7 smoke)."""
    from backend.app.db.models import Result, Run

    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    check = Check(
        suite_id=uuid.UUID(sid), name="row_count", expectation_type="expect_table_row_count"
    )
    db_session.add(check)
    db_session.flush()
    run = Run(suite_id=uuid.UUID(sid), status="succeeded", triggered_by="test:540")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status="pass"))
    db_session.commit()
    run_id = run.id  # capture before the cascade detaches the instance

    # Call hoisted out of the assert (CodeQL py/side-effect-in-assert): the
    # delete must run unconditionally, not live inside an assert expression.
    resp = client.delete(f"/api/v1/suites/{sid}")
    assert resp.status_code == 204
    db_session.expire_all()
    assert db_session.scalars(select(Result).where(Result.run_id == run_id)).all() == []


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
    got = client.get(f"/api/v1/suites/{sid}")
    assert got.status_code == 200
    # The read stamps the caller's effective level so the UI can gate actions.
    assert got.json()["my_permission"] == "view"
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


def test_workspace_admin_can_delete(
    client: TestClient, db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    # The non-owner admin is now the workspace-admin (ADR 0027), implicit on every
    # suite — they see `admin` and can delete a suite they don't own.
    _owner, b, _e, sid = _owner_b_e_suite(db_session)
    make_workspace_admin(b.email)
    _as(b)  # b owns nothing, has no share — only the allowlist makes them admin
    assert client.get(f"/api/v1/suites/{sid}").json()["my_permission"] == "admin"
    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 204


def test_workspace_admin_sees_all_suites(
    client: TestClient, db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    # A workspace-admin's list spans every suite (ADR 0027 option a), each stamped
    # `admin` — even a suite they neither own nor are shared on.
    _owner, b, _e, sid = _owner_b_e_suite(db_session)
    make_workspace_admin(b.email)
    _as(b)
    listed = {s["id"]: s["my_permission"] for s in client.get("/api/v1/suites").json()}
    assert sid in listed and listed[sid] == "admin"


def test_owner_sees_owner_permission(client: TestClient, db_session: Any) -> None:
    owner, _b, _e, sid = _owner_b_e_suite(db_session)
    _as(owner)
    got = client.get(f"/api/v1/suites/{sid}")
    assert got.json()["my_permission"] == "owner"
    # And the list stamps it too (batch path).
    listed = {s["id"]: s["my_permission"] for s in client.get("/api/v1/suites").json()}
    assert listed[sid] == "owner"


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
    return str(sid)


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


def test_import_onto_orchestration_connection_rejected(client: TestClient, db_session: Any) -> None:
    # A suite document can't be imported onto an ADF/Airflow connection (#242).
    src = _suite_with_checks(client, db_session)
    document = client.get(f"/api/v1/suites/{src}/export").json()
    target = _orchestration_connection(db_session, "airflow")
    resp = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(target.id), "document": document},
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


# ───────────────────────── column profiler ─────────────────────────


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def one(self) -> dict[str, Any]:
        return self._rows[0]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._rows)


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeConn:
    """Routes the aggregate query vs per-column top-values query by SQL text."""

    def __init__(self, aggregate: dict[str, Any], tops: dict[str, list[dict[str, Any]]]) -> None:
        self._aggregate = aggregate
        self._tops = tops

    def execute(self, clause: Any) -> _FakeResult:
        # Core statements render the column unquoted in str(); route the
        # aggregate query by its row_count label, top-values by column name.
        sql = str(clause)
        if "row_count" in sql:
            return _FakeResult([self._aggregate])
        for col, rows in self._tops.items():
            if col in sql:
                return _FakeResult(rows)
        return _FakeResult([])


def _patch_conn(
    monkeypatch: pytest.MonkeyPatch,
    aggregate: dict[str, Any],
    tops: dict[str, list[dict[str, Any]]],
) -> None:
    @contextmanager
    def fake_open(connection: Any, secret_store: Any) -> Iterator[_FakeConn]:
        yield _FakeConn(aggregate, tops)

    monkeypatch.setattr(profile_service, "_open_connection", fake_open)


def _typed_connection(
    db_session: Any, ctype: str, config: dict[str, Any], *, secret_ref: str | None = "kv-test"
) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{ctype}@ex")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"{ctype}-{uuid.uuid4().hex[:8]}",
        type=ctype,
        env="dev",
        config=config,
        secret_ref=secret_ref,
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _patch_dataframe(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    def fake_read(
        connection: Any, *, path: str, file_format: str, columns: list[str], secret_store: Any
    ) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(profile_service, "_read_dataframe", fake_read)


def test_profile_returns_column_stats(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    _patch_conn(
        monkeypatch,
        aggregate={
            "row_count": 100,
            "nulls_0": 25,
            "distinct_0": 4,
            "min_0": 1,
            "max_0": 9,
            "nulls_1": 0,
            "distinct_1": 2,
            "min_1": "a",
            "max_1": "z",
        },
        tops={
            "amount": [{"value": 9, "freq": 40}],
            "status": [{"value": "a", "freq": 60}, {"value": "z", "freq": 40}],
        },
    )
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"table": "orders", "schema": "public", "columns": ["amount", "status"], "top_n": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["table"] == "orders" and body["schema"] == "public"
    assert body["row_count"] == 100
    amount = next(c for c in body["columns"] if c["column"] == "amount")
    assert amount["null_count"] == 25 and amount["null_fraction"] == 0.25
    assert amount["distinct_count"] == 4 and amount["min_value"] == 1 and amount["max_value"] == 9
    assert amount["top_values"] == [{"value": 9, "count": 40}]
    status = next(c for c in body["columns"] if c["column"] == "status")
    assert status["top_values"][0] == {"value": "a", "count": 60}


def test_profile_invalid_column_returns_422(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"table": "orders", "schema": "public", "columns": ["amount; DROP TABLE x"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_identifier_invalid"


def test_profile_unsupported_connection_type_returns_422(
    client: TestClient, db_session: Any
) -> None:
    # all four datasources are profilable now; an orchestration type (ADF) is not.
    # A suite can no longer be created on an ADF connection via the API (#242), so
    # seed one directly to still exercise the profiler's defensive rejection.
    conn = _typed_connection(db_session, "adf", {"subscription_id": "s", "factory_name": "f"})
    owner = db_session.get(User, conn.created_by)
    suite = Suite(name="adf-suite", connection_id=conn.id, created_by=conn.created_by)
    db_session.add(suite)
    db_session.commit()
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{suite.id}/profile",
        json={"table": "orders", "schema": "public", "columns": ["amount"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_unsupported"


def test_profile_execution_failure_returns_502(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]

    @contextmanager
    def boom(connection: Any, secret_store: Any) -> Iterator[Any]:
        raise RuntimeError("warehouse unreachable")
        yield  # pragma: no cover

    monkeypatch.setattr(profile_service, "_open_connection", boom)
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"table": "orders", "schema": "public", "columns": ["amount"]},
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "profile_failed"
    # the adapter exception is not echoed to the client
    assert "warehouse unreachable" not in resp.text


def test_profile_requires_edit_access(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _share(client, owner, sid, b, "view")
    _as(b)
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"table": "orders", "schema": "public", "columns": ["amount"]},
    )
    assert resp.status_code == 403


def test_profile_without_schema_when_connection_has_none_returns_422(
    client: TestClient, db_session: Any
) -> None:
    # the _connection helper's config carries no "schema"; omitting it in the
    # body leaves the profiler with no schema to qualify the table → 422.
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.post(f"/api/v1/suites/{sid}/profile", json={"table": "orders", "columns": ["c"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_identifier_invalid"


# ── column listing (dropdown introspection, #474) ──


class _ColumnsResult:
    """A cursor result that only exposes column names (SELECT * LIMIT 0)."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def keys(self) -> list[str]:
        return self._names


def test_list_columns_returns_target_columns(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]

    class _Conn:
        def execute(self, clause: Any) -> _ColumnsResult:
            return _ColumnsResult(["amount", "status", "created_at"])

    @contextmanager
    def fake_open(connection: Any, secret_store: Any) -> Iterator[_Conn]:
        yield _Conn()

    monkeypatch.setattr(profile_service, "_open_connection", fake_open)
    resp = client.get(
        f"/api/v1/suites/{sid}/columns", params={"table": "orders", "schema": "public"}
    )
    assert resp.status_code == 200
    assert resp.json()["columns"] == ["amount", "status", "created_at"]


def test_list_columns_invalid_table_returns_422(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.get(
        f"/api/v1/suites/{sid}/columns",
        params={"table": "orders; DROP TABLE x", "schema": "public"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_identifier_invalid"


def test_list_columns_requires_edit_access(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _share(client, owner, sid, b, "view")
    _as(b)
    resp = client.get(f"/api/v1/suites/{sid}/columns", params={"table": "orders", "schema": "s"})
    assert resp.status_code == 403


# ── flat-file (ADLS Gen2 / S3) profiling ──


def _s3_suite(client: TestClient, db_session: Any) -> str:
    conn = _typed_connection(db_session, "s3", {"bucket": "b", "region": "us-east-1"})
    return str(client.post("/api/v1/suites", json=_payload(conn.id, name="s3-suite")).json()["id"])


def test_profile_flat_file_returns_column_stats(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _s3_suite(client, db_session)
    _patch_dataframe(
        monkeypatch,
        pd.DataFrame({"amount": [10, 20, 20, 20], "city": ["x", "x", "y", None]}),
    )
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"path": "data/orders.csv", "columns": ["amount", "city"], "top_n": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    # flat-file identity in the response; SQL identity absent
    assert body["path"] == "data/orders.csv" and body["file_format"] == "csv"
    assert body["table"] is None and body["schema"] is None
    assert body["row_count"] == 4
    amount = next(c for c in body["columns"] if c["column"] == "amount")
    assert amount["null_count"] == 0 and amount["distinct_count"] == 2
    assert amount["min_value"] == 10 and amount["max_value"] == 20
    assert amount["top_values"][0] == {"value": 20, "count": 3}
    city = next(c for c in body["columns"] if c["column"] == "city")
    assert city["null_count"] == 1 and city["null_fraction"] == 0.25
    assert city["min_value"] == "x" and city["max_value"] == "y"


def test_profile_flat_file_explicit_format_overrides_extension(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _s3_suite(client, db_session)
    _patch_dataframe(monkeypatch, pd.DataFrame({"a": [1, 2]}))
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"path": "data/blob", "file_format": "parquet", "columns": ["a"]},
    )
    assert resp.status_code == 200
    assert resp.json()["file_format"] == "parquet"


def test_profile_flat_file_missing_path_returns_422(client: TestClient, db_session: Any) -> None:
    sid = _s3_suite(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/profile", json={"columns": ["a"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_target_invalid"


def test_profile_sql_missing_table_returns_422(client: TestClient, db_session: Any) -> None:
    # a SQL (Snowflake) connection profiled without a table → target invalid.
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.post(f"/api/v1/suites/{sid}/profile", json={"columns": ["a"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_target_invalid"


# ── Unity Catalog (Databricks) profiling ──


def _uc_suite(client: TestClient, db_session: Any) -> str:
    conn = _typed_connection(
        db_session,
        "unity_catalog",
        {"workspace_url": "https://adb-1.2.azuredatabricks.net", "warehouse_id": "w1"},
    )
    return str(client.post("/api/v1/suites", json=_payload(conn.id, name="uc-suite")).json()["id"])


def test_profile_unity_catalog_returns_stats(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _uc_suite(client, db_session)
    # same fake conn as the Snowflake path — profile_table runs through _open_connection
    _patch_conn(
        monkeypatch,
        aggregate={"row_count": 50, "nulls_0": 5, "distinct_0": 3, "min_0": 1, "max_0": 9},
        tops={"amt": [{"value": 9, "freq": 20}]},
    )
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"catalog": "main", "schema": "sales", "table": "orders", "columns": ["amt"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 3-level identity echoed back; flat-file identity absent
    assert body["catalog"] == "main" and body["schema"] == "sales" and body["table"] == "orders"
    assert body["path"] is None
    assert body["row_count"] == 50
    amt = body["columns"][0]
    assert amt["null_count"] == 5 and amt["min_value"] == 1
    assert amt["top_values"][0] == {"value": 9, "count": 20}


def test_profile_unity_catalog_missing_catalog_returns_422(
    client: TestClient, db_session: Any
) -> None:
    sid = _uc_suite(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"schema": "sales", "table": "orders", "columns": ["amt"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_target_invalid"


def test_profile_unity_catalog_bad_catalog_identifier_returns_422(
    client: TestClient, db_session: Any
) -> None:
    sid = _uc_suite(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"catalog": "main; DROP", "schema": "sales", "table": "orders", "columns": ["amt"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_identifier_invalid"


def test_profile_flat_file_unknown_format_returns_422(client: TestClient, db_session: Any) -> None:
    sid = _s3_suite(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/profile", json={"path": "data/orders.xml", "columns": ["a"]}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_target_invalid"


def test_profile_flat_file_missing_column_returns_422(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _s3_suite(client, db_session)
    _patch_dataframe(monkeypatch, pd.DataFrame({"amount": [1, 2]}))
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"path": "data/orders.csv", "columns": ["nonexistent"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_column_not_found"


def test_profile_flat_file_read_failure_returns_502(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _s3_suite(client, db_session)

    def boom(
        connection: Any, *, path: str, file_format: str, columns: list[str], secret_store: Any
    ) -> Any:
        raise RuntimeError("bucket credentials rejected")

    monkeypatch.setattr(profile_service, "_read_dataframe", boom)
    resp = client.post(
        f"/api/v1/suites/{sid}/profile", json={"path": "data/orders.csv", "columns": ["a"]}
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "profile_failed"
    assert "bucket credentials rejected" not in resp.text


def test_profile_secret_less_connection_returns_422(client: TestClient, db_session: Any) -> None:
    # a supported connection with no stored credential is a config 422, not a 502
    conn = _typed_connection(
        db_session, "s3", {"bucket": "b", "region": "us-east-1"}, secret_ref=None
    )
    sid = client.post("/api/v1/suites", json=_payload(conn.id, name="nosec")).json()["id"]
    resp = client.post(
        f"/api/v1/suites/{sid}/profile", json={"path": "data/orders.csv", "columns": ["a"]}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_target_invalid"


def test_profile_flat_file_messy_column_degrades_not_500(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # an object column mixing numbers and strings (common in real CSVs) must not
    # 500 the profile — min/max degrade to null, hashable stats still computed.
    sid = _s3_suite(client, db_session)
    _patch_dataframe(monkeypatch, pd.DataFrame({"amount": [10, "N/A", 20, "N/A"]}))
    resp = client.post(
        f"/api/v1/suites/{sid}/profile",
        json={"path": "data/orders.csv", "columns": ["amount"], "top_n": 2},
    )
    assert resp.status_code == 200
    col = resp.json()["columns"][0]
    assert col["min_value"] is None and col["max_value"] is None  # uncomparable → null
    assert col["distinct_count"] == 3  # hashable → still computed
    assert col["top_values"][0] == {"value": "N/A", "count": 2}


# ───────────────────────── column policy (#415) ────────────────────


def _new_suite(client: TestClient, db_session: Any) -> str:
    conn = _connection(db_session)
    return str(client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"])


def test_column_policy_defaults_empty(client: TestClient, db_session: Any) -> None:
    sid = _new_suite(client, db_session)
    body = client.get(f"/api/v1/suites/{sid}/column-policy").json()
    assert body == {"identifier_column": None, "pii_columns": []}


def test_column_policy_put_sets_and_reads_back(client: TestClient, db_session: Any) -> None:
    sid = _new_suite(client, db_session)
    resp = client.put(
        f"/api/v1/suites/{sid}/column-policy",
        json={"identifier_column": "ORDER_NUMBER", "pii_columns": ["EMAIL", "EMAIL", ""]},
    )
    assert resp.status_code == 200
    # de-duped + blanks dropped
    assert resp.json() == {"identifier_column": "ORDER_NUMBER", "pii_columns": ["EMAIL"]}
    # reflected on GET and on the suite read
    assert client.get(f"/api/v1/suites/{sid}/column-policy").json()["identifier_column"] == (
        "ORDER_NUMBER"
    )
    assert client.get(f"/api/v1/suites/{sid}").json()["column_policy"]["identifier_column"] == (
        "ORDER_NUMBER"
    )


def test_column_policy_identifier_cannot_be_pii_422(client: TestClient, db_session: Any) -> None:
    sid = _new_suite(client, db_session)
    resp = client.put(
        f"/api/v1/suites/{sid}/column-policy",
        json={"identifier_column": "EMAIL", "pii_columns": ["EMAIL"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "column_policy_invalid"


def test_column_policy_rejects_pii_identifier_422(client: TestClient, db_session: Any) -> None:
    # A shown locator must be non-PII: an email/account_number identifier is rejected.
    sid = _new_suite(client, db_session)
    for bad in ("EMAIL", "account_number"):
        resp = client.put(
            f"/api/v1/suites/{sid}/column-policy",
            json={"identifier_column": bad, "pii_columns": []},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "column_policy_invalid"


def test_column_policy_suggest_profiles_and_classifies(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import profile_service
    from backend.app.services.profile_service import ColumnProfile, ProfileResult

    # Patch the profile_service module the suites router calls (imported there as
    # `profile`); patching the module attribute reaches the same object.
    sid = _new_suite(client, db_session)
    monkeypatch.setattr(profile_service, "list_columns", lambda *a, **k: ["ORDER_NUMBER", "EMAIL"])

    def _fake_profile(*a: Any, **k: Any) -> ProfileResult:
        return ProfileResult(
            row_count=2,
            columns=[
                ColumnProfile(
                    "ORDER_NUMBER", 0, 0.0, 2, None, None, [{"value": "O-1", "count": 1}]
                ),
                ColumnProfile("EMAIL", 0, 0.0, 2, None, None, [{"value": "a@x.com", "count": 1}]),
            ],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    body = client.post(
        f"/api/v1/suites/{sid}/column-policy/suggest", json={"table": "ORDERS", "schema": "RETAIL"}
    ).json()
    assert body == {"identifier_column": "ORDER_NUMBER", "pii_columns": ["EMAIL"]}
