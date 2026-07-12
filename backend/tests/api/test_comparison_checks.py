"""Comparison-check authoring tests (ADR 0015) against a real Postgres.

Covers the two-connection model's authoring surface end-to-end through the API:
the source-ref validation matrix, the kind⇔presence schema contract, source
repointing, version snapshots, the connection-delete 409 guard, and the
export/import (name, env) round-trip. Skips without TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

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


def _connection(db_session: Any, conn_type: str = "snowflake", env: str = "dev") -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    config = {"account": "ab12345.eu-west-1"} if conn_type == "snowflake" else {}
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env=env,
        config=config,
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _suite_id(client: TestClient, connection: Connection) -> str:
    resp = client.post(
        "/api/v1/suites",
        json={"name": f"recon-{uuid.uuid4().hex[:6]}", "connection_id": str(connection.id)},
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _payload(source_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "orders reconcile",
        "kind": "comparison",
        "expectation_type": "comparison:records",
        "source_connection_id": source_id,
        "config": {
            "source": {"table": "ORDERS", "schema": "RETAIL"},
            "keys": ["order_id"],
        },
    }
    body.update(overrides)
    return body


def _error_code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


# ───────────────────────── create: happy path ───────────────────────


def test_create_comparison_check(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source = _connection(db_session)
    sid = _suite_id(client, suite_conn)

    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id)))

    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "comparison"
    assert body["source_connection_id"] == str(source.id)
    assert body["config"]["keys"] == ["order_id"]


def test_create_allows_cross_env_source(client: TestClient, db_session: Any) -> None:
    # DEV-vs-QA parity is a headline use case (ADR 0015 §1) — env never matches.
    suite_conn = _connection(db_session, env="dev")
    source = _connection(db_session, env="qa")
    sid = _suite_id(client, suite_conn)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id)))
    assert resp.status_code == 201


def test_create_accepts_source_query_and_key_mapping(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source = _connection(db_session)  # snowflake — SQL-queryable
    sid = _suite_id(client, suite_conn)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            str(source.id),
            config={
                "source": {"query": "SELECT id AS order_id, total FROM RETAIL.ORDERS"},
                "target_query": "SELECT order_id, total FROM RETAIL.ORDERS_COPY",
                "keys": [{"source": "order_id", "target": "order_id"}],
                "max_rows": 100_000,
            },
        ),
    )
    assert resp.status_code == 201


# ───────────────────────── create: validation matrix ────────────────


def test_create_rejects_missing_source_ref(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, _connection(db_session))
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload("ignored", source_connection_id=None),
    )
    assert resp.status_code == 422
    assert _error_code(resp) == "check_config_invalid"


def test_create_rejects_unknown_source_connection(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, _connection(db_session))
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(uuid.uuid4())))
    assert resp.status_code == 422
    assert _error_code(resp) == "check_config_invalid"


def test_create_rejects_orchestration_source(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, _connection(db_session))
    airflow = _connection(db_session, conn_type="airflow")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(airflow.id)))
    assert resp.status_code == 422
    assert "orchestration" in resp.json()["error"]["message"]


def test_create_rejects_wrong_expectation_type(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, _connection(db_session))
    source = _connection(db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(str(source.id), expectation_type="comparison:cells"),
    )
    assert resp.status_code == 422
    assert "comparison:records" in resp.json()["error"]["message"]


def test_create_columns_grain_and_tolerance(client: TestClient, db_session: Any) -> None:
    # `comparison:columns` is authorable (#799), and tolerance validates.
    sid = _suite_id(client, _connection(db_session))
    source = _connection(db_session)
    ok = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            str(source.id),
            expectation_type="comparison:columns",
            config={
                "source": {"table": "ORDERS", "schema": "RETAIL"},
                "keys": ["order_id"],
                "tolerance": {"relative": 0.001},
            },
        ),
    )
    assert ok.status_code == 201
    bad_tolerance = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            str(source.id),
            config={
                "source": {"table": "ORDERS"},
                "keys": ["order_id"],
                "tolerance": {"absolute": -1},
            },
        ),
    )
    assert bad_tolerance.status_code == 422
    assert bad_tolerance.json()["error"]["code"] == "check_config_invalid"


def test_create_rejects_source_ref_on_expectation_kind(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, _connection(db_session))
    source = _connection(db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "not null",
            "kind": "expectation",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "id"},
            "source_connection_id": str(source.id),
        },
    )
    assert resp.status_code == 422
    assert "only comparison checks" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    "bad_config",
    [
        {"keys": ["order_id"]},  # no source spec
        {"source": "RETAIL.ORDERS", "keys": ["order_id"]},  # source not a dict
        {"source": {"schema": "RETAIL"}, "keys": ["order_id"]},  # unresolvable spec (no table)
        {"source": {"table": "ORDERS"}},  # no keys
        {"source": {"table": "ORDERS"}, "keys": []},  # empty keys
        {"source": {"table": "ORDERS"}, "keys": [""]},  # blank key
        {"source": {"table": "ORDERS"}, "keys": [{"source": "id"}]},  # half a mapping
        {"source": {"table": "ORDERS"}, "keys": ["id"], "max_rows": 0},  # bad cap
        {"source": {"table": "ORDERS"}, "keys": ["id"], "max_rows": "many"},  # bad cap type
        {"source": {"table": "ORDERS"}, "keys": ["id"], "max_rows": True},  # bool is int (== 1!)
        {"source": {"query": "DROP TABLE ORDERS"}, "keys": ["id"]},  # writeful query
        {"source": {"table": "ORDERS"}, "keys": ["x" * 10_001]},  # #651 string-size cap
    ],
)
def test_create_rejects_bad_config(
    client: TestClient, db_session: Any, bad_config: dict[str, Any]
) -> None:
    sid = _suite_id(client, _connection(db_session))
    source = _connection(db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id), config=bad_config)
    )
    assert resp.status_code == 422
    assert _error_code(resp) == "check_config_invalid"


def test_create_rejects_query_on_non_sql_source(client: TestClient, db_session: Any) -> None:
    # An S3 source reads natively — a SQL projection has nothing to run on.
    sid = _suite_id(client, _connection(db_session))
    s3 = _connection(db_session, conn_type="s3")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(str(s3.id), config={"source": {"query": "SELECT 1"}, "keys": ["id"]}),
    )
    assert resp.status_code == 422


def test_create_rejects_target_query_on_non_sql_suite(client: TestClient, db_session: Any) -> None:
    s3_suite_conn = _connection(db_session, conn_type="s3")
    source = _connection(db_session)
    sid = _suite_id(client, s3_suite_conn)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            str(source.id),
            config={
                "source": {"table": "ORDERS"},
                "target_query": "SELECT * FROM t",
                "keys": ["id"],
            },
        ),
    )
    assert resp.status_code == 422
    assert "config.target_query" in resp.json()["error"]["message"]


# ───────────────────────── update ───────────────────────────────────


def test_patch_repoints_source_and_snapshots_version(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source_a = _connection(db_session)
    source_b = _connection(db_session)
    sid = _suite_id(client, suite_conn)
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source_a.id)))
    cid = created.json()["id"]

    patched = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"source_connection_id": str(source_b.id)},
    )
    assert patched.status_code == 200
    assert patched.json()["source_connection_id"] == str(source_b.id)

    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert [v["source_connection_id"] for v in versions] == [str(source_b.id), str(source_a.id)]


def test_patch_rejects_source_ref_on_expectation_check(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, _connection(db_session))
    source = _connection(db_session)
    created = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "not null",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "id"},
        },
    )
    cid = created.json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"source_connection_id": str(source.id)},
    )
    assert resp.status_code == 422


def test_patch_rejects_repoint_to_orchestration(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source = _connection(db_session)
    airflow = _connection(db_session, conn_type="airflow")
    sid = _suite_id(client, suite_conn)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id))).json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"source_connection_id": str(airflow.id)},
    )
    assert resp.status_code == 422


# ───────────────────────── delete guard (RESTRICT + 409) ────────────


def test_source_connection_delete_blocked_then_allowed(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source = _connection(db_session)
    sid = _suite_id(client, suite_conn)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id))).json()["id"]

    blocked = client.delete(f"/api/v1/connections/{source.id}")
    assert blocked.status_code == 409
    body = blocked.json()["error"]
    assert body["code"] == "connection_in_use"
    assert body["detail"]["checks"][0]["name"] == "orders reconcile"
    # The sample is bounded; total + truncated let a caller trust the payload.
    assert body["detail"]["total"] == 1
    assert body["detail"]["truncated"] is False

    delete_check = client.delete(f"/api/v1/suites/{sid}/checks/{cid}")
    assert delete_check.status_code == 204
    delete_connection = client.delete(f"/api/v1/connections/{source.id}")
    assert delete_connection.status_code == 204


# ───────────────────────── export / import round-trip ───────────────


def test_export_import_round_trips_source_ref(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source = _connection(db_session)
    sid = _suite_id(client, suite_conn)
    client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id)))

    doc = client.get(f"/api/v1/suites/{sid}/export").json()
    assert doc["checks"][0]["source_connection"] == {"name": source.name, "env": source.env}

    imported = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(suite_conn.id), "document": doc},
    )
    assert imported.status_code == 201
    new_sid = imported.json()["id"]
    checks = client.get(f"/api/v1/suites/{new_sid}/checks").json()
    assert checks[0]["source_connection_id"] == str(source.id)


def test_import_rejects_unresolvable_source_ref(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source = _connection(db_session)
    sid = _suite_id(client, suite_conn)
    client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id)))
    doc = client.get(f"/api/v1/suites/{sid}/export").json()
    doc["checks"][0]["source_connection"] = {"name": "nowhere", "env": "dev"}

    resp = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(suite_conn.id), "document": doc},
    )
    assert resp.status_code == 422
    assert _error_code(resp) == "suite_import_invalid"


def test_import_rejects_comparison_without_source_ref(client: TestClient, db_session: Any) -> None:
    suite_conn = _connection(db_session)
    source = _connection(db_session)
    sid = _suite_id(client, suite_conn)
    client.post(f"/api/v1/suites/{sid}/checks", json=_payload(str(source.id)))
    doc = client.get(f"/api/v1/suites/{sid}/export").json()
    doc["checks"][0]["source_connection"] = None

    resp = client.post(
        "/api/v1/suites/import",
        json={"connection_id": str(suite_conn.id), "document": doc},
    )
    assert resp.status_code == 422


# ───────────────────────── schema contract (DB CHECK) ────────────────


def test_db_check_constraint_enforces_presence_iff_comparison(db_session: Any) -> None:
    # Defence-in-depth below the service: the table CHECK rejects rows the
    # validation layer would never write (comparison without ref; ref on
    # expectation), so no future code path can persist an inconsistent row.
    from sqlalchemy.exc import IntegrityError

    owner = User(aad_object_id=uuid.uuid4().hex, email="c@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(name="c1", type="snowflake", env="dev", config={}, created_by=owner.id)
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()

    db_session.add(
        Check(
            suite_id=suite.id,
            name="bad",
            kind="comparison",
            expectation_type="comparison:records",
            config={},
        )
    )
    with pytest.raises(IntegrityError, match="comparison_source_presence"):
        db_session.flush()
    db_session.rollback()
