"""`GET /search` — the command palette's read, against a real Postgres (db_session)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import Check, Connection, Run, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import search_service

Principal = tuple[User, dict[str, str]]


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _connection(db: Any, owner: User, name: str | None = None) -> Connection:
    conn = Connection(
        name=name or f"conn-{uuid.uuid4().hex[:8]}",
        type="postgres",
        env="dev",
        config={"host": "db"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    return conn


def _suite(db: Any, owner: User, conn: Connection, name: str) -> Suite:
    suite = Suite(name=name, connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.commit()
    return suite


def _check(db: Any, suite: Suite, name: str) -> Check:
    check = Check(
        suite_id=suite.id,
        name=name,
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.commit()
    return check


def _run(db: Any, suite: Suite) -> Run:
    run = Run(suite_id=suite.id, status="succeeded")
    db.add(run)
    db.commit()
    return run


def _search(client: TestClient, headers: dict[str, str], q: str, **params: Any) -> Any:
    return client.get("/api/v1/search", params={"q": q, **params}, headers=headers)


# --- authz: the 404-no-leak discipline, applied to a list -------------------------------------


def test_an_unshared_suite_leaks_nothing_through_any_group(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, _ = as_role("member")
    _outsider, headers = as_role("member")
    conn = _connection(db_session, owner)
    secret = _suite(db_session, owner, conn, "Payroll secret-suite")
    _check(db_session, secret, "secret-suite salary not null")
    _run(db_session, secret)

    for q in ("secret-suite", "Payroll", "salary"):
        resp = _search(client, headers, q)
        assert resp.json()["groups"] == {
            "suites": [],
            "checks": [],
            "connections": [],
            "runs": [],
        }
        # The only place the term appears is the echoed query — never a name.
        assert resp.text.count(q) == 1, resp.text


def test_a_view_share_exposes_the_suite_its_checks_and_runs(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, _ = as_role("member")
    viewer, headers = as_role("member")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn, "Orders quality")
    check = _check(db_session, suite, "orders id not null")
    run = _run(db_session, suite)
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()

    body = _search(client, headers, "orders").json()

    assert [h["id"] for h in body["groups"]["suites"]] == [str(suite.id)]
    assert body["groups"]["suites"][0]["href"] == f"/suites/{suite.id}"
    assert [h["id"] for h in body["groups"]["checks"]] == [str(check.id)]
    assert body["groups"]["checks"][0]["sublabel"] == "Orders quality"
    # view-only → the check opens on its suite, not on an editor the caller cannot save.
    assert body["groups"]["checks"][0]["href"] == f"/suites/{suite.id}"
    assert [h["id"] for h in body["groups"]["runs"]] == [str(run.id)]
    assert body["groups"]["runs"][0]["label"] == "Orders quality"
    assert body["groups"]["runs"][0]["href"] == f"/results/{run.id}"
    assert body["groups"]["runs"][0]["sublabel"] == "succeeded"
    assert body["groups"]["runs"][0]["when"] is not None
    assert body["groups"]["suites"][0]["when"] is None


def test_an_edit_share_opens_the_check_editor(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, _ = as_role("member")
    editor, headers = as_role("member")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn, "Orders quality")
    check = _check(db_session, suite, "orders id not null")
    db_session.add(Share(suite_id=suite.id, user_id=editor.id, permission="edit"))
    db_session.commit()

    body = _search(client, headers, "orders id").json()

    assert body["groups"]["checks"][0]["href"] == f"/suites/{suite.id}/checks/{check.id}/edit"


def test_a_workspace_admin_sees_every_suite_and_a_connection_page(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, _ = as_role("member")
    _, admin_headers = as_role("admin")
    _, member_headers = as_role("member")
    conn = _connection(db_session, owner, name="warehouse-dev")
    suite = _suite(db_session, owner, conn, "warehouse nightly")

    admin = _search(client, admin_headers, "warehouse").json()
    member = _search(client, member_headers, "warehouse").json()

    assert [h["id"] for h in admin["groups"]["suites"]] == [str(suite.id)]
    assert admin["groups"]["connections"][0]["href"] == f"/connections/{conn.id}/edit"
    assert admin["groups"]["connections"][0]["sublabel"] == "postgres · dev"
    # Connections list like GET /connections (unscoped); suites still follow grants.
    assert member["groups"]["suites"] == []
    assert [h["id"] for h in member["groups"]["connections"]] == [str(conn.id)]
    assert member["groups"]["connections"][0]["href"] == "/connections"


# --- matching, ranking, truncation -------------------------------------------------------------


def test_prefix_matches_rank_before_substring_matches_case_insensitively(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, headers = as_role("member")
    conn = _connection(db_session, owner)
    _suite(db_session, owner, conn, "Daily ORDERS reconciliation")
    _suite(db_session, owner, conn, "orders volume")
    _suite(db_session, owner, conn, "Orders quality")

    labels = [h["label"] for h in _search(client, headers, "orders").json()["groups"]["suites"]]

    assert labels == ["Orders quality", "orders volume", "Daily ORDERS reconciliation"]


def test_like_wildcards_in_the_query_are_literal(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, headers = as_role("member")
    conn = _connection(db_session, owner)
    _suite(db_session, owner, conn, "100% complete")
    _suite(db_session, owner, conn, "100 percent")

    labels = [h["label"] for h in _search(client, headers, "100%").json()["groups"]["suites"]]

    assert labels == ["100% complete"]


def test_a_run_matches_on_its_id_prefix(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, headers = as_role("member")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn, "Orders quality")
    run = _run(db_session, suite)
    other_owner, _ = as_role("member")
    hidden = _suite(db_session, other_owner, _connection(db_session, other_owner), "x")
    _run(db_session, hidden)

    body = _search(client, headers, str(run.id)[:8]).json()

    assert [h["id"] for h in body["groups"]["runs"]] == [str(run.id)]


def test_truncation_is_reported_from_the_real_count_not_the_page_length(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, headers = as_role("member")
    conn = _connection(db_session, owner)
    for i in range(3):
        _suite(db_session, owner, conn, f"boundary suite {i}")

    exact = _search(client, headers, "boundary", limit=3).json()
    over = _search(client, headers, "boundary", limit=2).json()

    assert len(exact["groups"]["suites"]) == 3
    assert exact["truncated_groups"] == []
    assert len(over["groups"]["suites"]) == 2
    assert over["truncated_groups"] == ["suites"]


def test_truncation_counts_only_what_the_caller_can_see(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, headers = as_role("member")
    other, _ = as_role("member")
    conn = _connection(db_session, owner)
    _suite(db_session, owner, conn, "shared-name a")
    for i in range(3):
        _suite(db_session, other, conn, f"shared-name hidden {i}")

    body = _search(client, headers, "shared-name", limit=1).json()

    assert len(body["groups"]["suites"]) == 1
    # Three invisible suites must not make the visible group look truncated.
    assert body["truncated_groups"] == []


# --- input validation ---------------------------------------------------------------------------


@pytest.mark.parametrize("q", ["", "   ", "x" * (search_service.MAX_QUERY_LEN + 1)])
def test_blank_and_over_long_queries_are_422(
    client: TestClient, as_role: Callable[..., Principal], q: str
) -> None:
    _, headers = as_role("member")
    assert _search(client, headers, q).status_code == 422


def test_nul_in_the_query_is_422(client: TestClient, as_role: Callable[..., Principal]) -> None:
    _, headers = as_role("member")
    resp = client.get("/api/v1/search?q=a%00b", headers=headers)
    assert resp.status_code == 422


@pytest.mark.parametrize("limit", [0, 21])
def test_limit_outside_1_to_20_is_422(
    client: TestClient, as_role: Callable[..., Principal], limit: int
) -> None:
    _, headers = as_role("member")
    assert _search(client, headers, "a", limit=limit).status_code == 422


def test_the_query_is_echoed_stripped(
    client: TestClient, as_role: Callable[..., Principal]
) -> None:
    _, headers = as_role("member")
    body = _search(client, headers, "  orders  ").json()
    assert body["query"] == "orders"
    assert body["truncated_groups"] == []


def test_a_missing_query_is_422(client: TestClient, as_role: Callable[..., Principal]) -> None:
    _, headers = as_role("member")
    resp = client.get("/api/v1/search", headers=headers)
    assert resp.status_code == 422


def test_unrelated_names_do_not_match(
    client: TestClient, db_session: Any, as_role: Callable[..., Principal]
) -> None:
    owner, headers = as_role("member")
    conn = _connection(db_session, owner)
    _suite(db_session, owner, conn, "Orders quality")
    body = _search(client, headers, uuid.uuid4().hex).json()
    assert body["groups"]["suites"] == []
