"""Tags (#888): vocabulary, assignment on the four entities, list filters, cascades, authz."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.db.models import (
    Asset,
    AuditEvent,
    Check,
    Connection,
    Suite,
    Tag,
    User,
    check_tags,
    suite_tags,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import tag_service
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store


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


def _suite(db: Any, owner: User, conn: Connection, name: str | None = None) -> Suite:
    suite = Suite(
        name=name or f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id
    )
    db.add(suite)
    db.commit()
    return suite


def _check(db: Any, suite: Suite) -> Check:
    check = Check(
        suite_id=suite.id,
        name=f"c-{uuid.uuid4().hex[:6]}",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.commit()
    return check


def _asset(db: Any) -> Asset:
    asset = Asset(namespace="postgres://db.example.com/analytics", name=f"t_{uuid.uuid4().hex[:6]}")
    db.add(asset)
    db.commit()
    return asset


def _join_rows(db: Any, table: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(table)) or 0)


# ───────────────────────── normalization / validation ─────────────────────────


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("Finance", "finance"),
        ("  Core  Data ", "core data"),
        ("team:Core-Data", "team:core-data"),
        ("Straße", "straße"),  # lower(), not casefold(): the picker previews with toLowerCase
    ],
)
def test_names_are_normalized(raw: str, stored: str) -> None:
    assert tag_service.normalize_tag_name(raw) == stored


@pytest.mark.parametrize("raw", ["", "   ", "a\tb\x01", "x" * 65])
def test_invalid_names_are_refused(raw: str) -> None:
    with pytest.raises(tag_service.TagNameInvalidError):
        tag_service.normalize_tag_name(raw)


def test_too_many_tags_refused() -> None:
    with pytest.raises(tag_service.TagNameInvalidError):
        tag_service.normalize_tag_names(f"t{i}" for i in range(tag_service.MAX_TAGS_PER_ENTITY + 1))


def test_ensure_tags_reuses_an_existing_row(db_session: Any) -> None:
    first = tag_service.ensure_tags(db_session, ["finance"])
    again = tag_service.ensure_tags(db_session, ["finance", "daily"])
    assert first[0].id == again[0].id
    assert (
        db_session.scalar(select(func.count()).select_from(Tag).where(Tag.name == "finance")) == 1
    )


# ───────────────────────── suites ─────────────────────────


def test_suite_patch_sets_normalized_deduped_tags_and_read_returns_them(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    suite = _suite(db_session, member, conn)

    r = client.patch(
        f"/api/v1/suites/{suite.id}",
        headers=headers,
        json={"tags": ["Finance", " finance", "Daily"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["daily", "finance"]
    read_back = client.get(f"/api/v1/suites/{suite.id}", headers=headers)
    assert read_back.json()["tags"] == ["daily", "finance"]
    listed = client.get("/api/v1/suites", headers=headers).json()
    assert [s["tags"] for s in listed if s["id"] == str(suite.id)] == [["daily", "finance"]]

    # Omitting `tags` leaves them; `[]` clears them.
    renamed = client.patch(f"/api/v1/suites/{suite.id}", headers=headers, json={"name": "renamed"})
    assert renamed.json()["tags"] == ["daily", "finance"]
    cleared = client.patch(f"/api/v1/suites/{suite.id}", headers=headers, json={"tags": []})
    assert cleared.json()["tags"] == []


def test_suite_create_accepts_tags(client: TestClient, db_session: Any, as_role: Any) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    r = client.post(
        "/api/v1/suites",
        headers=headers,
        json={"name": "orders", "connection_id": str(conn.id), "tags": ["Orders", "daily"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["tags"] == ["daily", "orders"]


@pytest.mark.parametrize("bad", [[""], ["x" * 65], ["ok", "a\x01b"]])
def test_suite_patch_refuses_invalid_tag_names(
    client: TestClient, db_session: Any, as_role: Any, bad: list[str]
) -> None:
    member, headers = as_role("member")
    suite = _suite(db_session, member, _connection(db_session, member))
    r = client.patch(f"/api/v1/suites/{suite.id}", headers=headers, json={"tags": bad})
    assert r.status_code == 422, r.text
    unchanged = client.get(f"/api/v1/suites/{suite.id}", headers=headers)
    assert unchanged.json()["tags"] == []


def test_suite_tag_filter_is_and_and_case_insensitive(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    both = _suite(db_session, member, conn, "both")
    only_a = _suite(db_session, member, conn, "only-a")
    _suite(db_session, member, conn, "none")
    client.patch(f"/api/v1/suites/{both.id}", headers=headers, json={"tags": ["a", "b"]})
    client.patch(f"/api/v1/suites/{only_a.id}", headers=headers, json={"tags": ["a"]})

    def names(r: Any) -> list[str]:
        return sorted(s["name"] for s in r.json())

    by_a = client.get("/api/v1/suites", headers=headers, params={"tag": ["a"]})
    assert names(by_a) == ["both", "only-a"]
    by_both = client.get("/api/v1/suites", headers=headers, params={"tag": ["A", "b"]})
    assert names(by_both) == ["both"]
    # A name no tag has matches nothing — the honest answer, not an error.
    r = client.get("/api/v1/suites", headers=headers, params={"tag": ["zzz"]})
    assert r.status_code == 200 and r.json() == []
    blank = client.get("/api/v1/suites", headers=headers, params={"tag": [""]})
    assert blank.status_code == 422


def test_viewer_cannot_tag_but_can_read_tags(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    owner, owner_headers = as_role("member")
    viewer, viewer_headers = as_role("viewer")
    suite = _suite(db_session, owner, _connection(db_session, owner))
    client.patch(f"/api/v1/suites/{suite.id}", headers=owner_headers, json={"tags": ["finance"]})
    resp_22 = client.post(
        f"/api/v1/suites/{suite.id}/shares",
        headers=owner_headers,
        json={"user_id": str(viewer.id), "permission": "view"},
    )
    assert resp_22.status_code == 201
    resp_21 = client.get(f"/api/v1/suites/{suite.id}", headers=viewer_headers)
    assert resp_21.json()["tags"] == ["finance"]
    r = client.patch(f"/api/v1/suites/{suite.id}", headers=viewer_headers, json={"tags": ["x"]})
    assert r.status_code == 403
    resp_20 = client.get("/api/v1/tags", headers=viewer_headers)
    assert resp_20.status_code == 200


# ───────────────────────── authz: no enumeration without the suite ─────────────────────────


def test_user_without_access_cannot_enumerate_a_suites_tags_by_any_route(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    owner, owner_headers = as_role("member")
    _outsider, outsider_headers = as_role("member")
    conn = _connection(db_session, owner)
    hidden = _suite(db_session, owner, conn, "hidden")
    check = _check(db_session, hidden)
    client.patch(
        f"/api/v1/suites/{hidden.id}", headers=owner_headers, json={"tags": ["secret-tag"]}
    )
    client.patch(
        f"/api/v1/suites/{hidden.id}/checks/{check.id}",
        headers=owner_headers,
        json={"tags": ["secret-check-tag"]},
    )

    # The suite itself, its checks, and the list — none reach the outsider.
    resp_19 = client.get(f"/api/v1/suites/{hidden.id}", headers=outsider_headers)
    assert resp_19.status_code == 404
    resp_18 = client.get(f"/api/v1/suites/{hidden.id}/checks", headers=outsider_headers)
    assert resp_18.status_code == 404
    resp_17 = client.get("/api/v1/suites", headers=outsider_headers)
    assert resp_17.json() == []
    # Filtering by the hidden suite's tag must not surface it either.
    resp_16 = client.get("/api/v1/suites", headers=outsider_headers, params={"tag": ["secret-tag"]})
    assert resp_16.json() == []
    # The vocabulary shows the NAME and workspace-wide counts (shared by design) but never an
    # entity id: nothing in the payload identifies the hidden suite or check.
    vocab = client.get("/api/v1/tags", headers=outsider_headers).json()
    body = str(vocab)
    assert str(hidden.id) not in body and str(check.id) not in body and "hidden" not in body
    entry = next(t for t in vocab if t["name"] == "secret-tag")
    assert set(entry) == {"id", "name", "created_at", "usage"}
    assert entry["usage"] == {"suites": 1, "assets": 0, "connections": 0, "checks": 0}


# ───────────────────────── vocabulary: list + admin delete ─────────────────────────


def test_delete_tag_is_admin_only_and_detaches_everywhere(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    _admin, admin_headers = as_role("admin")
    member, member_headers = as_role("member")
    conn = _connection(db_session, member)
    suite = _suite(db_session, member, conn)
    check = _check(db_session, suite)
    client.patch(
        f"/api/v1/suites/{suite.id}", headers=member_headers, json={"tags": ["shared", "keep"]}
    )
    client.patch(
        f"/api/v1/suites/{suite.id}/checks/{check.id}",
        headers=member_headers,
        json={"tags": ["shared"]},
    )
    tag_id = next(
        t["id"]
        for t in client.get("/api/v1/tags", headers=member_headers).json()
        if t["name"] == "shared"
    )

    resp_15 = client.delete(f"/api/v1/tags/{tag_id}", headers=member_headers)
    assert resp_15.status_code == 403
    resp_14 = client.delete(f"/api/v1/tags/{tag_id}", headers=admin_headers)
    assert resp_14.status_code == 204
    resp_13 = client.delete(f"/api/v1/tags/{tag_id}", headers=admin_headers)
    assert resp_13.status_code == 404

    resp_12 = client.get(f"/api/v1/suites/{suite.id}", headers=member_headers)
    assert resp_12.json()["tags"] == ["keep"]
    resp_11 = client.get(f"/api/v1/suites/{suite.id}/checks/{check.id}", headers=member_headers)
    assert resp_11.json()["tags"] == []
    resp_10 = client.get("/api/v1/tags", headers=member_headers)
    assert [t["name"] for t in resp_10.json()] == ["keep"]
    event = db_session.scalar(select(AuditEvent).where(AuditEvent.action == "tag.delete"))
    assert event is not None and str(event.entity_id) == tag_id
    assert event.after["detached"] == {"suites": 1, "assets": 0, "connections": 0, "checks": 1}


def test_deleting_an_entity_cascades_its_assignments(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    member, headers = as_role("member")
    conn = _connection(db_session, member)
    suite = _suite(db_session, member, conn)
    check = _check(db_session, suite)
    client.patch(f"/api/v1/suites/{suite.id}", headers=headers, json={"tags": ["a"]})
    client.patch(
        f"/api/v1/suites/{suite.id}/checks/{check.id}", headers=headers, json={"tags": ["a", "b"]}
    )
    assert (_join_rows(db_session, suite_tags), _join_rows(db_session, check_tags)) == (1, 2)

    # Deleting the check removes its rows; the tag rows stay in the vocabulary.
    resp_9 = client.delete(f"/api/v1/suites/{suite.id}/checks/{check.id}", headers=headers)
    assert resp_9.status_code == 204
    assert _join_rows(db_session, check_tags) == 0
    # Deleting the suite removes its own rows (and would cascade its checks' too).
    resp_8 = client.delete(f"/api/v1/suites/{suite.id}", headers=headers)
    assert resp_8.status_code == 204
    assert _join_rows(db_session, suite_tags) == 0
    resp_7 = client.get("/api/v1/tags", headers=headers)
    assert sorted(t["name"] for t in resp_7.json()) == [
        "a",
        "b",
    ]
    resp_6 = client.get("/api/v1/tags", headers=headers)
    assert all(
        t["usage"] == {"suites": 0, "assets": 0, "connections": 0, "checks": 0}
        for t in resp_6.json()
    )


# ───────────────────────── checks ─────────────────────────


def test_check_tags_only_patch_mints_no_version_but_is_audited(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    member, headers = as_role("member")
    suite = _suite(db_session, member, _connection(db_session, member))
    created = client.post(
        f"/api/v1/suites/{suite.id}/checks",
        headers=headers,
        json={
            "name": "id not null",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "id"},
            "tags": ["Critical"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["tags"] == ["critical"]
    cid = created.json()["id"]
    versions_before = len(
        client.get(f"/api/v1/suites/{suite.id}/checks/{cid}/versions", headers=headers).json()
    )

    r = client.patch(
        f"/api/v1/suites/{suite.id}/checks/{cid}",
        headers=headers,
        json={"tags": ["critical", "pii"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["critical", "pii"]
    # Labels are not part of the versioned definition.
    resp_5 = client.get(f"/api/v1/suites/{suite.id}/checks/{cid}/versions", headers=headers)
    assert len(resp_5.json()) == versions_before
    listed = client.get(f"/api/v1/suites/{suite.id}/checks", headers=headers).json()
    assert [c["tags"] for c in listed] == [["critical", "pii"]]
    audited = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "check.update", AuditEvent.entity_id == uuid.UUID(cid)
        )
    ).all()
    assert audited and audited[-1].after["tag_names"] == ["critical", "pii"]


# ───────────────────────── connections ─────────────────────────


def test_connection_tags_are_admin_only_and_a_tags_only_patch_runs_no_test(
    client: TestClient, db_session: Any, as_role: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import connection_service

    admin, admin_headers = as_role("admin")
    _member, member_headers = as_role("member")
    conn = _connection(db_session, admin)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("a tags-only PATCH must not test the connection")

    monkeypatch.setattr(connection_service, "_test_before_save", _boom)
    resp_4 = client.patch(
        f"/api/v1/connections/{conn.id}", headers=member_headers, json={"tags": ["x"]}
    )
    assert resp_4.status_code == 403
    r = client.patch(
        f"/api/v1/connections/{conn.id}", headers=admin_headers, json={"tags": ["Prod", "pg"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["pg", "prod"]
    listed = client.get("/api/v1/connections", headers=member_headers).json()
    assert [c["tags"] for c in listed if c["id"] == str(conn.id)] == [["pg", "prod"]]
    resp_3 = client.get(f"/api/v1/connections/{conn.id}", headers=member_headers)
    assert resp_3.json()["tags"] == [
        "pg",
        "prod",
    ]


# ───────────────────────── assets ─────────────────────────


def test_asset_tags_admin_patch_list_filter_and_total_count(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    _admin, admin_headers = as_role("admin")
    _member, member_headers = as_role("member")
    a1, a2 = _asset(db_session), _asset(db_session)
    resp_2 = client.patch(f"/api/v1/assets/{a1.id}", headers=member_headers, json={"tags": ["x"]})
    assert resp_2.status_code == 403
    r = client.patch(
        f"/api/v1/assets/{a1.id}", headers=admin_headers, json={"tags": ["Gold", "finance"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["finance", "gold"]
    client.patch(f"/api/v1/assets/{a2.id}", headers=admin_headers, json={"tags": ["gold"]})

    page = client.get("/api/v1/assets", headers=member_headers, params={"tag": ["gold"]})
    assert page.headers["X-Total-Count"] == "2"
    assert {a["id"] for a in page.json()} == {str(a1.id), str(a2.id)}
    page = client.get("/api/v1/assets", headers=member_headers, params={"tag": ["gold", "FINANCE"]})
    assert page.headers["X-Total-Count"] == "1"
    assert [a["id"] for a in page.json()] == [str(a1.id)]
    resp_1 = client.get(f"/api/v1/assets/{a1.id}", headers=member_headers)
    assert resp_1.json()["summary"]["tags"] == [
        "finance",
        "gold",
    ]


# ───────────────────────── MCP ─────────────────────────


def test_mcp_list_suites_carries_tags(
    client: TestClient, db_session: Any, as_role: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.mcp import server

    member, headers = as_role("member")
    suite = _suite(db_session, member, _connection(db_session, member))
    client.patch(f"/api/v1/suites/{suite.id}", headers=headers, json={"tags": ["mcp-tag"]})
    monkeypatch.setattr(server, "get_session", lambda: db_session)
    monkeypatch.setattr(server, "resolve_current_user", lambda _session: member)
    listed = server.list_suites()
    assert [s["tags"] for s in listed if s["id"] == str(suite.id)] == [["mcp-tag"]]


def test_mcp_update_tools_set_tags_and_check_reads_carry_them(
    client: TestClient, db_session: Any, as_role: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The field `list_suites` gained must not age its neighbours (#888 review): the check reads
    # show tags and both update tools can set them, replacing the whole list.
    from backend.app.mcp import server

    member, headers = as_role("member")
    suite = _suite(db_session, member, _connection(db_session, member))
    created = client.post(
        f"/api/v1/suites/{suite.id}/checks",
        headers=headers,
        json={
            "name": "id not null",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "id"},
        },
    )
    assert created.status_code == 201, created.text
    check_id = created.json()["id"]
    monkeypatch.setattr(server, "get_session", lambda: db_session)
    # Re-read per call: each tool commits, which expires the `member` instance.
    member_id = member.id
    monkeypatch.setattr(
        server, "resolve_current_user", lambda _session: db_session.get(User, member_id)
    )

    assert server.update_suite(str(suite.id), tags=["Finance", "daily"])["tags"] == [
        "daily",
        "finance",
    ]
    assert server.update_check(str(suite.id), check_id, tags=["PII"])["tags"] == ["pii"]
    assert server.get_check(str(suite.id), check_id)["tags"] == ["pii"]
    listed = server.list_checks(str(suite.id))
    assert [c["tags"] for c in listed["checks"] if c["id"] == check_id] == [["pii"]]
    # Omitting `tags` leaves them; `[]` clears them.
    assert server.update_check(str(suite.id), check_id, name="renamed")["tags"] == ["pii"]
    assert server.update_check(str(suite.id), check_id, tags=[])["tags"] == []
