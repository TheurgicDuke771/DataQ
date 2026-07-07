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
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
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


def test_create_rejects_still_reserved_kind(client: TestClient, db_session: Any) -> None:
    # freshness/volume are now authorable (ADR 0012 amendment); the other reserved
    # kinds still have no runner, so CRUD must keep refusing them.
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(kind="schema_drift"))
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


# ───────────────────────── expectation-kind validation (#651) ──────


def test_create_rejects_unknown_expectation_type(client: TestClient, db_session: Any) -> None:
    # Not a GX expectation → 422, never 201 (previously persisted silently).
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(expectation_type="expect_totally_made_up_thing"),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"
    assert "expect_totally_made_up_thing" in resp.json()["error"]["message"]


def test_create_rejects_missing_required_config_keys(client: TestClient, db_session: Any) -> None:
    # expect_column_values_to_be_between with an empty config lacks the
    # required `column` (and both bounds) — GX construction fails → 422.
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(expectation_type="expect_column_values_to_be_between", config={}),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_rejects_both_bounds_missing(client: TestClient, db_session: Any) -> None:
    # GX's own root validator: min_value and max_value cannot both be None.
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            expectation_type="expect_column_values_to_be_between", config={"column": "amount"}
        ),
    )
    assert resp.status_code == 422


def test_create_rejects_wrong_typed_config_values(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            expectation_type="expect_column_values_to_be_between",
            config={"column": "amount", "min_value": "not-a-number", "max_value": []},
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_rejects_unknown_config_keys(client: TestClient, db_session: Any) -> None:
    # GX expectations forbid extra kwargs — a typo'd key must not persist.
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(config={"column": "order_id", "colunm_typo": "x"}),
    )
    assert resp.status_code == 422


def test_create_rejects_oversized_config_string(client: TestClient, db_session: Any) -> None:
    # A 100KB "column name" previously persisted; the size cap 422s it —
    # including when nested inside a list (value_set-style).
    sid = _suite_id(client, db_session)
    huge = "x" * 100_000
    flat = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(config={"column": huge}))
    assert flat.status_code == 422
    nested = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            expectation_type="expect_column_values_to_be_in_set",
            config={"column": "order_id", "value_set": ["ok", huge]},
        ),
    )
    assert nested.status_code == 422


def test_update_revalidates_expectation_config(client: TestClient, db_session: Any) -> None:
    # PATCH must apply the same gate on the post-patch state: a valid check
    # cannot be edited into garbage, and a rejected PATCH persists nothing.
    sid = _suite_id(client, db_session)
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_payload())
    cid = created.json()["id"]

    bad_type = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}", json={"expectation_type": "expect_nonsense"}
    )
    assert bad_type.status_code == 422
    bad_config = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}", json={"config": {"column": "order_id", "bogus": 1}}
    )
    assert bad_config.status_code == 422
    unchanged = client.get(f"/api/v1/suites/{sid}/checks/{cid}").json()
    assert unchanged["expectation_type"] == "expect_column_values_to_not_be_null"
    assert unchanged["config"] == {"column": "order_id"}


def test_create_accepts_long_but_legitimate_config_string(
    client: TestClient, db_session: Any
) -> None:
    # A ~2k-char value-set member (or regex) runs fine on the worker, so the
    # size cap must not reject it — it exists to block junk, not real kwargs
    # (#651 follow-up: the original 1_000 cap was tighter than the runner).
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            expectation_type="expect_column_values_to_be_in_set",
            config={"column": "order_id", "value_set": ["ok", "x" * 2_000]},
        ),
    )
    assert resp.status_code == 201


def test_create_rejects_oversized_config_dict_key(client: TestClient, db_session: Any) -> None:
    # Dict KEYS are strings too — a 100KB key is the same junk class as a 100KB
    # value and must 422 (#651 follow-up: the walk originally skipped keys).
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(config={"column": "order_id", "k" * 100_000: "x"}),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"
    # The envelope must not round-trip the oversized key back to the client.
    assert len(resp.text) < 5_000


def test_oversized_string_422_does_not_echo_the_input(client: TestClient, db_session: Any) -> None:
    # The 422 envelope is returned AND logged — a 100KB offending value (or a
    # 100KB key on the path to a nested offender) must come back as a bounded
    # path, never the input itself.
    sid = _suite_id(client, db_session)
    huge = "v" * 100_000
    flat = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(config={"column": huge}))
    assert flat.status_code == 422
    assert len(flat.text) < 5_000
    nested_under_huge_key = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(config={"column": "order_id", "k" * 50_000: {"inner": huge}}),
    )
    assert nested_under_huge_key.status_code == 422
    assert len(nested_under_huge_key.text) < 5_000


def test_unknown_expectation_type_echo_is_bounded() -> None:
    # REST caps expectation_type at 128 chars, but the MCP tools call the
    # service directly with no such cap — the service itself must bound what it
    # echoes into the message and detail (#651 follow-up).
    from backend.app.services.check_service import (
        CheckConfigInvalidError,
        validate_expectation_check,
    )

    with pytest.raises(CheckConfigInvalidError) as exc_info:
        validate_expectation_check("expect_" + "z" * 5_000, {})
    assert len(str(exc_info.value)) < 500
    assert len(exc_info.value.detail["expectation_type"]) <= 200


def test_patch_not_touching_expectation_skips_gx_validation(
    client: TestClient, db_session: Any
) -> None:
    # A pre-#651 row can hold a config today's pinned GX rejects (there is no
    # backfill). A rename or threshold tweak must still succeed — only a PATCH
    # touching expectation_type/config re-validates (#651 follow-up: the
    # original gate ran GX validation on every PATCH, bricking such rows).
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    check = db_session.get(Check, uuid.UUID(cid))
    check.config = {"column": "order_id", "legacy_junk_key": 1}  # bypasses the API gate
    db_session.commit()

    rename = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"name": "renamed"})
    assert rename.status_code == 200
    threshold = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"fail_threshold": 5})
    assert threshold.status_code == 200
    # Touching the expectation itself still validates the merged state: the
    # same-value expectation_type PATCH meets the stored junk config → 422.
    touched = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"expectation_type": "expect_column_values_to_not_be_null"},
    )
    assert touched.status_code == 422


def test_import_rejects_invalid_expectation_check(client: TestClient, db_session: Any) -> None:
    # The import path must not smuggle in a check a direct POST would 422 —
    # and it is atomic, so the bad document writes no suite at all.
    sid = _suite_id(client, db_session)
    suite = client.get(f"/api/v1/suites/{sid}").json()
    document = {
        "version": 1,
        "name": "smuggled",
        "description": None,
        "checks": [
            {
                "name": "junk",
                "kind": "expectation",
                "expectation_type": "expect_totally_made_up_thing",
                "config": {},
            }
        ],
    }
    resp = client.post(
        "/api/v1/suites/import",
        json={"document": document, "connection_id": suite["connection_id"]},
    )
    assert resp.status_code == 422
    names = [s["name"] for s in client.get("/api/v1/suites").json()]
    assert "smuggled" not in names


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


# ───────────────────────── monitors (freshness / volume, ADR 0012) ──


def _freshness_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "orders fresh",
        "kind": "freshness",
        "expectation_type": "monitor:freshness",
        "config": {"column": "loaded_at"},
        "fail_threshold": 48,  # hours — required so it can actually fail
    }
    body.update(overrides)
    return body


def _volume_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "orders volume",
        "kind": "volume",
        "expectation_type": "monitor:volume",
        "config": {"min_rows": 1000, "max_rows": 5000},
    }
    body.update(overrides)
    return body


def test_create_freshness_monitor_on_sql_datasource_returns_201(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_freshness_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "freshness"
    assert body["config"] == {"column": "loaded_at"}


def test_create_volume_monitor_on_sql_datasource_returns_201(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session, conn_type="unity_catalog")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_volume_payload())
    assert resp.status_code == 201
    assert resp.json()["kind"] == "volume"


def test_create_freshness_without_threshold_rejected(client: TestClient, db_session: Any) -> None:
    # The #426 silent-green guard: freshness needs a fail/critical age threshold.
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_freshness_payload(fail_threshold=None, critical_threshold=None),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_freshness_with_critical_threshold_only_returns_201(
    client: TestClient, db_session: Any
) -> None:
    # A critical (not warn/fail) threshold satisfies the "can fail" requirement.
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_freshness_payload(fail_threshold=None, critical_threshold=72),
    )
    assert resp.status_code == 201


def test_create_freshness_with_zero_threshold_rejected(client: TestClient, db_session: Any) -> None:
    # The inverse footgun: fail=0 hours bands every age as a failure (always red).
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_freshness_payload(fail_threshold=0, critical_threshold=None),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_monitor_with_mismatched_expectation_type_rejected(
    client: TestClient, db_session: Any
) -> None:
    # A monitor's expectation_type must be the canonical monitor:<kind>; a junk /
    # mismatched type would mislabel result rows and could smuggle a custom-SQL type.
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_freshness_payload(expectation_type="monitor:volume"),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_freshness_missing_column_rejected(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_freshness_payload(config={}))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_volume_with_inverted_range_rejected(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_volume_payload(config={"min_rows": 5000, "max_rows": 1000}),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_monitor_on_flatfile_datasource_rejected(
    client: TestClient, db_session: Any
) -> None:
    # Monitors run a scalar SQL aggregate → SQL datasources only, like custom-SQL.
    sid = _suite_id(client, db_session, conn_type="s3")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_volume_payload())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_update_volume_monitor_to_inverted_range_rejected(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_volume_payload())
    check_id = created.json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{check_id}",
        json={"config": {"min_rows": 9, "max_rows": 1}},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


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

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[Any],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
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


# ───────────────────────── snooze (suppression) ────────────────────


def _make_check(client: TestClient, sid: str) -> str:
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload())
    assert resp.status_code == 201
    return str(resp.json()["id"])


def test_snooze_sets_future_until_and_clear_resets(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = _make_check(client, sid)
    # Fresh check is not snoozed.
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}").json()["alert_snoozed_until"] is None

    snoozed = client.post(f"/api/v1/suites/{sid}/checks/{cid}/snooze", json={"hours": 4})
    assert snoozed.status_code == 200
    until = snoozed.json()["alert_snoozed_until"]
    assert until is not None
    assert datetime.fromisoformat(until) > datetime.now(UTC)

    cleared = client.request("DELETE", f"/api/v1/suites/{sid}/checks/{cid}/snooze")
    assert cleared.status_code == 200
    assert cleared.json()["alert_snoozed_until"] is None


def test_snooze_does_not_create_a_version(client: TestClient, db_session: Any) -> None:
    # Snooze is operational state, not config — it must not churn version history.
    sid = _suite_id(client, db_session)
    cid = _make_check(client, sid)
    before = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    client.post(f"/api/v1/suites/{sid}/checks/{cid}/snooze", json={"hours": 1})
    after = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert len(after) == len(before) == 1  # create made v1; snooze added none


@pytest.mark.parametrize("hours", [0, -3, 721])
def test_snooze_rejects_out_of_range_hours(
    client: TestClient, db_session: Any, hours: float
) -> None:
    sid = _suite_id(client, db_session)
    cid = _make_check(client, sid)
    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/snooze", json={"hours": hours})
    assert resp.status_code == 422


def test_snooze_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}/snooze", json={"hours": 1})
    assert resp.status_code == 404


def test_snooze_requires_edit_permission(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _as(owner)
    cid = _make_check(client, sid)
    _grant(client, owner, sid, b, "view")  # viewer cannot snooze
    _as(b)
    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/snooze", json={"hours": 1})
    assert resp.status_code == 403


# ───────────────────────── concurrent-edit conflict (C3) ────────────────────


def test_concurrent_check_edit_returns_409_not_500(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    """A version-snapshot collision on a concurrent edit is a benign 409, not a 500.

    Simulate the race outcome: force the next snapshot to reuse an existing
    `version_no`, so the commit trips `uq_check_versions_check_version` exactly as
    a concurrent writer would. The handler must surface 409 `check_edit_conflict`.
    """
    from backend.app.db.models import CheckVersion
    from backend.app.services import check_service

    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]

    def _colliding_version(session: Any, check: Any, *, actor_id: Any) -> CheckVersion:
        # version_no=1 already exists (minted on create) → IntegrityError on commit
        version = CheckVersion(
            check_id=check.id,
            version_no=1,
            name=check.name,
            kind=check.kind,
            expectation_type=check.expectation_type,
            config=check.config,
            warn_threshold=check.warn_threshold,
            fail_threshold=check.fail_threshold,
            critical_threshold=check.critical_threshold,
            changed_by=actor_id,
        )
        session.add(version)
        return version

    monkeypatch.setattr(check_service, "record_check_version", _colliding_version)

    resp = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"name": "renamed"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "check_edit_conflict"


def test_update_check_other_integrity_error_not_mislabelled_409(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    """Only the version-backstop collision is a 409; a *different* IntegrityError
    raised at the same commit must re-raise (not be mislabelled 'edited
    concurrently'), exercising the narrowed `except` branch."""
    from sqlalchemy.exc import IntegrityError

    from backend.app.db.models import CheckVersion
    from backend.app.services import check_service

    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]

    def _bad_fk_version(session: Any, check: Any, *, actor_id: Any) -> CheckVersion:
        # Bogus check_id → a foreign-key IntegrityError at commit (NOT the version
        # unique backstop), so the narrowed catch must re-raise it.
        version = CheckVersion(
            check_id=uuid.uuid4(),
            version_no=1,
            name=check.name,
            kind=check.kind,
            expectation_type=check.expectation_type,
            config=check.config,
            changed_by=actor_id,
        )
        session.add(version)
        return version

    monkeypatch.setattr(check_service, "record_check_version", _bad_fk_version)

    # The non-version IntegrityError is re-raised (not mapped to a 409); FastAPI's
    # TestClient propagates an unhandled server exception to the caller.
    with pytest.raises(IntegrityError):
        client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"name": "renamed"})
