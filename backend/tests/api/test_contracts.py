"""REST surface for data contracts (ADR 0052): import, reads, export, delete, authz."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.app.db.models import Connection, Share, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store

AsRole = Callable[..., tuple[Any, dict[str, str]]]


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    override_secret_store(app, FakeSecretStore())
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _connection(db: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:8]}",
        type="postgres",
        env="dev",
        config={"host": "db.example.com", "database": "analytics"},
        secret_ref="kv-pg",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    return conn


_DOC_YAML = """
apiVersion: v3.2.0
kind: DataContract
id: orders-contract
name: orders
version: 1.0.0
status: active
schema:
  - name: orders
    physicalName: public.orders
    properties:
      - name: order_id
        logicalType: integer
        required: true
      - name: email
        logicalType: string
        classification: restricted
    quality:
      - id: floor
        metric: rowCount
        mustBeGreaterThan: 0
slaProperties:
  - property: retention
    value: 7
    unit: y
"""


def _import(client: TestClient, headers: dict[str, str], conn: Connection, **body: Any) -> Any:
    payload: dict[str, Any] = {"connection_id": str(conn.id), "document_yaml": _DOC_YAML}
    payload.update(body)
    return client.post("/api/v1/contracts/import", json=payload, headers=headers)


def test_import_returns_the_contract_suites_and_unmapped_clauses(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    resp = _import(client, headers, conn)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    contract = body["contract"]
    assert (contract["odcs_id"], contract["version"], contract["api_version"]) == (
        "orders-contract",
        "1.0.0",
        "v3.2.0",
    )
    assert len(body["created_suite_ids"]) == 1 and body["untargeted_objects"] == []
    assert contract["suites"][0]["runnable"] is True
    assert [u["field"] for u in contract["unmapped"]] == ["slaProperties.retention"]
    assert body["checks_added"] == 3  # required, schema drift, volume


def test_a_viewer_cannot_import(client: TestClient, db_session: Any, as_role: AsRole) -> None:
    viewer, headers = as_role("viewer")
    conn = _connection(db_session, viewer)
    assert _import(client, headers, conn).status_code == 403


def test_an_invalid_document_is_a_422_naming_where(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    resp = _import(client, headers, conn, document_yaml="apiVersion: v3.2.0\nkind: DataContract\n")
    assert resp.status_code == 422
    assert "id" in resp.json()["error"]["message"]


def test_v2_and_v4_documents_are_refused(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    for api in ("v2.2.2", "v4.0.0"):
        resp = _import(client, headers, conn, document_yaml=_DOC_YAML.replace("v3.2.0", api))
        assert resp.status_code == 422, api


def test_send_exactly_one_document(client: TestClient, db_session: Any, as_role: AsRole) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    resp = client.post(
        "/api/v1/contracts/import", json={"connection_id": str(conn.id)}, headers=headers
    )
    assert resp.status_code == 422


def test_reimporting_the_same_version_is_a_409(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    assert _import(client, headers, conn).status_code == 201
    again = _import(client, headers, conn)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "contract_version_exists"


def test_reads_are_scoped_to_suites_the_caller_can_see(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    owner, owner_headers = as_role("member")
    conn = _connection(db_session, owner)
    contract_id = _import(client, owner_headers, conn).json()["contract"]["id"]
    _, stranger_headers = as_role("member")

    listed = client.get("/api/v1/contracts", headers=owner_headers)
    assert [c["id"] for c in listed.json()] == [contract_id]
    stranger_list = client.get("/api/v1/contracts", headers=stranger_headers)
    assert stranger_list.json() == []
    hidden = client.get(f"/api/v1/contracts/{contract_id}", headers=stranger_headers)
    assert hidden.status_code == 404


def test_a_view_share_sees_the_contract_but_cannot_delete_it(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    owner, owner_headers = as_role("member")
    conn = _connection(db_session, owner)
    body = _import(client, owner_headers, conn).json()
    viewer, viewer_headers = as_role("member")
    db_session.add(
        Share(
            suite_id=uuid.UUID(body["created_suite_ids"][0]), user_id=viewer.id, permission="view"
        )
    )
    db_session.commit()
    contract_id = body["contract"]["id"]
    seen = client.get(f"/api/v1/contracts/{contract_id}", headers=viewer_headers)
    assert seen.status_code == 200
    denied = client.delete(f"/api/v1/contracts/{contract_id}", headers=viewer_headers)
    assert denied.status_code == 403


def test_export_is_a_valid_odcs_document_in_json_and_yaml(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    contract_id = _import(client, headers, conn).json()["contract"]["id"]

    as_json = client.get(f"/api/v1/contracts/{contract_id}/export", headers=headers)
    assert as_json.status_code == 200
    document = as_json.json()
    assert document["apiVersion"] == "v3.2.0" and document["id"] == "orders-contract"
    as_yaml = client.get(f"/api/v1/contracts/{contract_id}/export?format=yaml", headers=headers)
    assert as_yaml.headers["content-type"].startswith("application/yaml")
    assert yaml.safe_load(as_yaml.text)["id"] == "orders-contract"


def test_delete_unlinks_and_keeps_the_suites(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    body = _import(client, headers, conn).json()
    contract_id = body["contract"]["id"]
    deleted = client.delete(f"/api/v1/contracts/{contract_id}", headers=headers)
    assert deleted.status_code == 204
    gone = client.get(f"/api/v1/contracts/{contract_id}", headers=headers)
    assert gone.status_code == 404
    suite = client.get(f"/api/v1/suites/{body['created_suite_ids'][0]}", headers=headers)
    assert suite.status_code == 200


def test_get_contract_carries_the_compliance_rollup(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    contract_id = _import(client, headers, conn).json()["contract"]["id"]
    body = client.get(f"/api/v1/contracts/{contract_id}", headers=headers).json()
    compliance = body["compliance"]
    assert compliance["not_enforced"] == 1
    assert {c["state"] for c in compliance["clauses"]} == {"not_run"}  # nothing has run yet
    assert compliance["suites"][0]["results_final"] is False


def test_patch_links_and_unlinks_a_suite_and_audits_it(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    from sqlalchemy import select

    from backend.app.db.models import AuditEvent, Suite

    member, headers = as_role("member")
    conn = _connection(db_session, member)
    contract_id = _import(client, headers, conn).json()["contract"]["id"]
    suite = Suite(name="hand-made", connection_id=conn.id, created_by=member.id)
    db_session.add(suite)
    db_session.commit()
    url = f"/api/v1/suites/{suite.id}"

    linked = client.patch(
        url,
        json={"contract": {"contract_id": contract_id, "contract_object": "orders"}},
        headers=headers,
    )
    assert linked.status_code == 200, linked.text
    assert (linked.json()["contract_id"], linked.json()["contract_object"]) == (
        contract_id,
        "orders",
    )
    renamed = client.patch(url, json={"name": "still linked"}, headers=headers)
    assert renamed.json()["contract_id"] == contract_id  # omitted = untouched
    unknown = client.patch(
        url,
        json={"contract": {"contract_id": contract_id, "contract_object": "nope"}},
        headers=headers,
    )
    assert unknown.status_code == 422
    unlinked = client.patch(url, json={"contract": None}, headers=headers)
    assert (unlinked.json()["contract_id"], unlinked.json()["contract_object"]) == (None, None)

    events = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "suite.update", AuditEvent.entity_id == suite.id
        )
    ).all()
    assert any((e.after or {}).get("contract_object") == "orders" for e in events)


def test_linking_to_a_contract_the_caller_cannot_see_is_a_404(
    client: TestClient, db_session: Any, as_role: AsRole
) -> None:
    from backend.app.db.models import Suite

    owner, owner_headers = as_role("member")
    conn = _connection(db_session, owner)
    contract_id = _import(client, owner_headers, conn).json()["contract"]["id"]
    stranger, stranger_headers = as_role("member")
    theirs = Suite(name="theirs", connection_id=conn.id, created_by=stranger.id)
    db_session.add(theirs)
    db_session.commit()
    resp = client.patch(
        f"/api/v1/suites/{theirs.id}",
        json={"contract": {"contract_id": contract_id, "contract_object": "orders"}},
        headers=stranger_headers,
    )
    assert resp.status_code == 404
