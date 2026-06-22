"""Check endpoint tests against a real Postgres (db_session) via TestClient.

Checks are nested under a suite. A connection + suite are created per test for
the FK chain; auth runs in dev-bypass (conftest). Skips without
TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.datasources.base import CheckOutcome, SuiteOutcome
from backend.app.db.models import Connection, Result, Run, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import dryrun_service


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _suite_id(client: TestClient, db_session: Any, conn_type: str = "snowflake") -> str:
    """Create a connection (ORM) + suite (API) and return the suite id.

    `conn_type` lets a test pick the datasource (e.g. 's3' to exercise custom-SQL
    datasource gating); defaults to Snowflake.
    """
    owner = User(aad_object_id=uuid.uuid4().hex, email="owner@example.com")
    db_session.add(owner)
    db_session.flush()
    config = {"account": "ab12345.eu-west-1"} if conn_type == "snowflake" else {}
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config=config,
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    resp = client.post(
        "/api/v1/suites",
        json={"name": "finance", "description": None, "connection_id": str(conn.id)},
    )
    return str(resp.json()["id"])


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "orders not null",
        "expectation_type": "expect_column_values_to_not_be_null",
        "config": {"column": "order_id"},
    }
    body.update(overrides)
    return body


# ───────────────────────── create ──────────────────────────────────


def test_create_returns_201_with_defaults(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["suite_id"] == sid
    assert body["kind"] == "expectation"  # default
    assert body["expectation_type"] == "expect_column_values_to_not_be_null"
    assert body["config"] == {"column": "order_id"}
    assert body["warn_threshold"] is None


def test_create_stores_thresholds_as_numbers(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(warn_threshold=0.95, fail_threshold=0.9, critical_threshold=0.5),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["warn_threshold"] == 0.95
    assert body["fail_threshold"] == 0.9
    assert body["critical_threshold"] == 0.5


def test_create_rejects_non_expectation_kind(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(kind="freshness"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_in_unknown_suite_returns_404(client: TestClient) -> None:
    resp = client.post(f"/api/v1/suites/{uuid.uuid4()}/checks", json=_payload())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "suite_not_found"


def test_create_blank_name_or_expectation_returns_422(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    blank_name = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(name=""))
    assert blank_name.status_code == 422
    blank_type = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(expectation_type=""))
    assert blank_type.status_code == 422


# ───────────────────────── custom-SQL (ADR 0019) ───────────────────


def _custom_sql_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "no negative totals",
        "expectation_type": "unexpected_rows_expectation",
        "config": {"unexpected_rows_query": "SELECT * FROM {batch} WHERE total < 0"},
    }
    body.update(overrides)
    return body


def test_create_custom_sql_on_sql_datasource_returns_201(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_custom_sql_payload())
    assert resp.status_code == 201
    assert resp.json()["expectation_type"] == "unexpected_rows_expectation"


def test_create_custom_sql_rejects_non_readonly_query(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_custom_sql_payload(config={"unexpected_rows_query": "DELETE FROM {batch}"}),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "custom_sql_invalid"


def test_create_custom_sql_on_flatfile_datasource_rejected(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session, conn_type="s3")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_custom_sql_payload())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "custom_sql_invalid"


def test_update_custom_sql_to_non_readonly_query_rejected(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_custom_sql_payload())
    check_id = created.json()["id"]
    # PATCH only the config (query) — the effective custom-SQL check must be
    # re-validated against the post-patch state.
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{check_id}",
        json={"config": {"unexpected_rows_query": "DROP TABLE orders"}},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "custom_sql_invalid"


# ───────────────────────── read / list ─────────────────────────────


def test_list_returns_suite_checks(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    client.post(f"/api/v1/suites/{sid}/checks", json=_payload(name="c1"))
    client.post(f"/api/v1/suites/{sid}/checks", json=_payload(name="c2"))
    resp = client.get(f"/api/v1/suites/{sid}/checks")
    assert resp.status_code == 200
    assert {c["name"] for c in resp.json()} == {"c1", "c2"}


def test_list_empty_suite_returns_empty(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.get(f"/api/v1/suites/{sid}/checks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_returns_check(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cid


def test_get_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.get(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "check_not_found"


def test_check_is_scoped_to_its_suite(client: TestClient, db_session: Any) -> None:
    sid_a = _suite_id(client, db_session)
    sid_b = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid_a}/checks", json=_payload()).json()["id"]
    # the check exists, but not under suite B's path
    cross = client.get(f"/api/v1/suites/{sid_b}/checks/{cid}")
    assert cross.status_code == 404
    assert client.get(f"/api/v1/suites/{sid_a}/checks/{cid}").status_code == 200


# ───────────────────────── update / delete ─────────────────────────


def test_patch_updates_fields(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={
            "name": "renamed",
            "expectation_type": "expect_column_values_to_be_unique",
            "config": {"column": "amount"},
            "warn_threshold": 1.5,
            "fail_threshold": 3,
            "critical_threshold": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "renamed"
    assert body["expectation_type"] == "expect_column_values_to_be_unique"
    assert body["config"] == {"column": "amount"}
    assert body["warn_threshold"] == 1.5
    assert body["fail_threshold"] == 3
    assert body["critical_threshold"] == 5


def test_patch_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_returns_204_then_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    deleted = client.delete(f"/api/v1/suites/{sid}/checks/{cid}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}").status_code == 404


# ───────────────────────── access enforcement (PR-E2) ──────────────


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _owner_b_e_suite(db_session: Any) -> tuple[User, User, User, str]:
    """owner + B + E and a suite owned by `owner` (checks are added per-test)."""
    owner = User(aad_object_id=uuid.uuid4().hex, email="owner@ex")
    b = User(aad_object_id=uuid.uuid4().hex, email="b@ex")
    e = User(aad_object_id=uuid.uuid4().hex, email="e@ex")
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


def _grant(client: TestClient, owner: User, sid: str, target: User, perm: str) -> None:
    _as(owner)
    granted = client.post(
        f"/api/v1/suites/{sid}/shares", json={"user_id": str(target.id), "permission": perm}
    )
    assert granted.status_code == 201


def test_viewer_reads_checks_but_cannot_write(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _as(owner)  # author the check as the owner first
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    _grant(client, owner, sid, b, "view")
    _as(b)
    assert client.get(f"/api/v1/suites/{sid}/checks").status_code == 200
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}").status_code == 200
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(name="c2"))
    assert created.status_code == 403
    patched = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"name": "x"})
    assert patched.status_code == 403
    deleted = client.delete(f"/api/v1/suites/{sid}/checks/{cid}")
    assert deleted.status_code == 403


def test_editor_can_write_checks(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _grant(client, owner, sid, b, "edit")
    _as(b)
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_payload())
    assert created.status_code == 201


def test_outsider_cannot_see_checks(client: TestClient, db_session: Any) -> None:
    _owner, _b, e, sid = _owner_b_e_suite(db_session)
    _as(e)
    assert client.get(f"/api/v1/suites/{sid}/checks").status_code == 404


# ───────────────────────── version history (#280) ──────────────────


def test_create_records_initial_version(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]

    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions")
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) == 1
    v1 = versions[0]
    assert v1["version_no"] == 1
    assert v1["expectation_type"] == "expect_column_values_to_not_be_null"
    assert v1["config"] == {"column": "order_id"}
    assert v1["changed_by_name"]  # the dev-bypass actor authored it


def test_update_appends_version_newest_first(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"config": {"column": "amount"}, "warn_threshold": 0.9},
    )

    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1]  # newest first
    # v2 is the post-update state; v1 still carries the original config (the
    # whole point — "see previous config before overwriting").
    assert versions[0]["config"] == {"column": "amount"}
    assert versions[0]["warn_threshold"] == 0.9
    assert versions[1]["config"] == {"column": "order_id"}
    assert versions[1]["warn_threshold"] is None


def test_noop_update_does_not_append_a_version(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    # A PATCH that changes nothing (resends the current name) must not mint a
    # duplicate version — history stays at v1.
    assert (
        client.patch(
            f"/api/v1/suites/{sid}/checks/{cid}", json={"name": "orders not null"}
        ).status_code
        == 200
    )
    assert client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={}).status_code == 200

    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert [v["version_no"] for v in versions] == [1]


def test_version_records_its_author(client: TestClient, db_session: Any) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email="ed@ex", display_name="Ed Editor")
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
    db_session.commit()
    _as(owner)
    sid = client.post(
        "/api/v1/suites", json={"name": "s", "description": None, "connection_id": str(conn.id)}
    ).json()["id"]
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]

    v1 = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()[0]
    assert v1["changed_by"] == str(owner.id)
    assert v1["changed_by_name"] == "Ed Editor"


# ───────────────────────── result history (trend, ADR 0022) ─────────


def _run_with_result(
    db_session: Any,
    suite_id: str,
    check_id: str,
    *,
    status: str,
    metric_value: float | None,
    age_days: float,
) -> None:
    when = datetime.now(UTC) - timedelta(days=age_days)
    run = Run(suite_id=uuid.UUID(suite_id), status="succeeded", created_at=when)
    db_session.add(run)
    db_session.flush()
    db_session.add(
        Result(
            run_id=run.id,
            check_id=uuid.UUID(check_id),
            status=status,
            metric_value=metric_value,
            created_at=when,
        )
    )
    db_session.commit()


def test_history_returns_results_oldest_first(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    _run_with_result(db_session, sid, cid, status="pass", metric_value=0.0, age_days=2)
    _run_with_result(db_session, sid, cid, status="warn", metric_value=2.5, age_days=0)

    history = client.get(f"/api/v1/suites/{sid}/checks/{cid}/history").json()
    assert [p["status"] for p in history] == ["pass", "warn"]  # chronological
    assert [p["metric_value"] for p in history] == [0.0, 2.5]


def test_history_empty_for_check_with_no_runs(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}/history").json() == []


def test_history_honours_limit_keeping_most_recent(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    for age in (3, 2, 1):
        _run_with_result(db_session, sid, cid, status="pass", metric_value=age, age_days=age)

    history = client.get(f"/api/v1/suites/{sid}/checks/{cid}/history?limit=2").json()
    # Latest 2 by run time, returned chronologically: age=2 then age=1.
    assert [p["metric_value"] for p in history] == [2.0, 1.0]
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}/history?limit=0").status_code == 422


def test_history_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    assert client.get(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}/history").status_code == 404


def test_history_outsider_cannot_read(client: TestClient, db_session: Any) -> None:
    owner, _b, e, sid = _owner_b_e_suite(db_session)
    _as(owner)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    _as(e)  # not owner, not shared
    # An outsider gets 404, not 403 — the suite's existence is hidden from
    # non-members (same as `test_outsider_cannot_see_checks`).
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}/history").status_code == 404


def test_versions_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.get(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}/versions")
    assert resp.status_code == 404


def test_viewer_reads_versions_outsider_cannot(client: TestClient, db_session: Any) -> None:
    owner, b, e, sid = _owner_b_e_suite(db_session)
    _as(owner)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    _grant(client, owner, sid, b, "view")

    _as(b)  # a viewer can read history
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").status_code == 200
    _as(e)  # an outsider sees the suite as nonexistent (404, not 403)
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").status_code == 404


def test_import_records_initial_version_per_check(client: TestClient, db_session: Any) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email="imp@ex")
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
    db_session.commit()
    _as(owner)
    resp = client.post(
        "/api/v1/suites/import",
        json={
            "connection_id": str(conn.id),
            "document": {
                "name": "imported",
                "checks": [
                    {
                        "name": "a",
                        "expectation_type": "expect_column_values_to_not_be_null",
                        "config": {"column": "x"},
                    },
                    {
                        "name": "b",
                        "expectation_type": "expect_column_values_to_be_unique",
                        "config": {"column": "y"},
                    },
                ],
            },
        },
    )
    assert resp.status_code == 201
    sid = resp.json()["id"]
    for check in client.get(f"/api/v1/suites/{sid}/checks").json():
        versions = client.get(f"/api/v1/suites/{sid}/checks/{check['id']}/versions").json()
        assert len(versions) == 1
        assert versions[0]["version_no"] == 1


# ───────────────────────── dry-run (preview, no persistence) ────────


class _FakeRunner:
    def __init__(
        self, outcome: SuiteOutcome | None = None, raises: Exception | None = None
    ) -> None:
        self._outcome = outcome
        self._raises = raises
        self.called_with: dict[str, Any] | None = None

    def run_checks(self, *, table: str, schema: str | None, checks: list[Any]) -> SuiteOutcome:
        self.called_with = {"table": table, "schema": schema, "checks": checks}
        if self._raises is not None:
            raise self._raises
        assert self._outcome is not None
        return self._outcome


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: _FakeRunner) -> None:
    monkeypatch.setattr(dryrun_service, "build_snowflake_runner", lambda **_kw: runner)


def _dryrun_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expectation_type": "expect_column_values_to_not_be_null",
        "config": {"column": "order_id"},
        "table": "ORDERS",
    }
    body.update(overrides)
    return body


def test_dryrun_returns_pass_preview(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _suite_id(client, db_session)
    _patch_runner(
        monkeypatch,
        _FakeRunner(
            SuiteOutcome(
                success=True,
                checks=[CheckOutcome("x", success=True, observed_value={"observed_value": 5})],
            )
        ),
    )
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pass"
    assert body["observed_value"] == {"observed_value": 5}


def test_dryrun_derives_tier_from_thresholds(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _suite_id(client, db_session)
    _patch_runner(
        monkeypatch,
        _FakeRunner(
            SuiteOutcome(
                success=False,
                checks=[
                    CheckOutcome("x", success=False, sample_failures={"unexpected_percent": 7.5})
                ],
            )
        ),
    )
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(warn_threshold=1, fail_threshold=5, critical_threshold=20),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fail"  # 7.5 ≥ fail(5), < critical(20)
    assert body["metric_value"] == 7.5


def test_dryrun_previews_error_for_unevaluable_check(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check GX can't evaluate previews as `error` — not a misleading `fail`
    tag — so the editor preview matches what a persisted run would record (#122)."""
    sid = _suite_id(client, db_session)
    _patch_runner(
        monkeypatch,
        _FakeRunner(
            SuiteOutcome(
                success=False,
                checks=[
                    CheckOutcome(
                        "x", success=False, errored=True, error_message="column does not exist"
                    )
                ],
            )
        ),
    )
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["metric_value"] is None
    assert body["observed_value"] == {"error": "column does not exist"}


def test_dryrun_sanitizes_nan_observed_value(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _suite_id(client, db_session)
    _patch_runner(
        monkeypatch,
        _FakeRunner(
            SuiteOutcome(
                success=True,
                checks=[
                    CheckOutcome("x", success=True, observed_value={"observed_value": float("nan")})
                ],
            )
        ),
    )
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 200
    assert resp.json()["observed_value"] == {"observed_value": None}


def test_dryrun_rejects_non_expectation_kind(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body(kind="freshness"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "dry_run_unsupported"


def test_dryrun_rejects_non_snowflake_connection(client: TestClient, db_session: Any) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email="o@ex")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"s3-{uuid.uuid4().hex[:8]}",
        type="s3",
        env="dev",
        config={"bucket": "b", "region": "us-east-1"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.commit()
    _as(owner)
    resp = client.post(f"/api/v1/suites/{suite.id}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "dry_run_unsupported"


def test_dryrun_rejects_non_readonly_custom_sql_before_running(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dry-run executes the query, so the custom-SQL guardrail must apply here too
    # (ADR 0019 review): a non-read-only query is a 422 and the runner is never
    # reached.
    sid = _suite_id(client, db_session)
    runner = _FakeRunner(outcome=SuiteOutcome(success=True, checks=[]))
    _patch_runner(monkeypatch, runner)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(
            expectation_type="unexpected_rows_expectation",
            config={"unexpected_rows_query": "DELETE FROM {batch}"},
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "custom_sql_invalid"
    assert runner.called_with is None  # rejected before the runner ran


def test_dryrun_runner_failure_returns_502(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _suite_id(client, db_session)
    _patch_runner(monkeypatch, _FakeRunner(raises=RuntimeError("warehouse unreachable")))
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "dry_run_failed"


def test_dryrun_requires_edit_permission(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _grant(client, owner, sid, b, "view")  # viewer cannot author/dry-run
    _as(b)
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 403
