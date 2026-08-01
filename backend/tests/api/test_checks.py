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
from backend.app.db.models import Check, CheckVersion, Connection, Result, Run, Suite, User
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


def _suite_id(
    client: TestClient,
    db_session: Any,
    conn_type: str = "snowflake",
    target: dict[str, Any] | None = None,
) -> str:
    """Create a connection (ORM) + suite (API) and return the suite id.

    `conn_type` lets a test pick the datasource (e.g. 's3' to exercise custom-SQL
    datasource gating); defaults to Snowflake. `target` sets the suite's run
    target (needed by dry-run, which resolves the target server-side).
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
    body: dict[str, Any] = {"name": "finance", "description": None, "connection_id": str(conn.id)}
    if target is not None:
        body["target"] = target
    resp = client.post("/api/v1/suites", json=body)
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
        json=_payload(warn_threshold=0.5, fail_threshold=0.9, critical_threshold=0.95),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["warn_threshold"] == 0.5
    assert body["fail_threshold"] == 0.9
    assert body["critical_threshold"] == 0.95


def test_create_rejects_an_unknown_kind(client: TestClient, db_session: Any) -> None:
    # Every kind in the schema CHECK is now authorable — #593 shipped the last
    # reserved one — so the gate is pinned with a kind that exists nowhere. It must
    # 422 at the service layer, not reach the DB and surface as a constraint 500.
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(kind="telepathy"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_rejects_a_monitor_kind_with_a_mismatched_expectation_type(
    client: TestClient, db_session: Any
) -> None:
    # The kind↔type pairing gate: the run path keys off `kind`, so a junk type
    # would still execute but mislabel every result row.
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(kind="schema_drift"))
    assert resp.status_code == 422
    assert "monitor:schema_drift" in resp.json()["error"]["message"]


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


# ───────────────────────── threshold ordering (#568) ───────────────


def test_create_rejects_inverted_thresholds(client: TestClient, db_session: Any) -> None:
    # derive_status assumes warn <= fail <= critical; a fully inverted set
    # (90/50/10) must 422 at author time, not silently persist as 201.
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(warn_threshold=90, fail_threshold=50, critical_threshold=10),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_rejects_fail_greater_than_critical(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(fail_threshold=10, critical_threshold=5),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_rejects_negative_threshold(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(warn_threshold=-1))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_accepts_equal_thresholds(client: TestClient, db_session: Any) -> None:
    # Equal is a valid (if unusual) boundary, not "inverted".
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(warn_threshold=5, fail_threshold=5, critical_threshold=5),
    )
    assert resp.status_code == 201


def test_create_accepts_partially_set_ascending_thresholds(
    client: TestClient, db_session: Any
) -> None:
    # warn unset — only fail <= critical needs to hold, and does.
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(fail_threshold=5, critical_threshold=20),
    )
    assert resp.status_code == 201


def test_update_rejects_inverted_thresholds(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(warn_threshold=1, fail_threshold=5, critical_threshold=20),
    ).json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"warn_threshold": 25},  # merged: 25 > fail(5) and 25 > critical(20)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"
    # The rejected PATCH must not have persisted the bad value.
    unchanged = client.get(f"/api/v1/suites/{sid}/checks/{cid}")
    assert unchanged.json()["warn_threshold"] == 1


def test_update_rejects_when_only_touching_one_field_breaks_the_merged_state(
    client: TestClient, db_session: Any
) -> None:
    # A PATCH that never mentions warn_threshold can still violate ordering once
    # merged with the check's EXISTING warn_threshold — the effective post-patch
    # state is what's validated, same pattern as the monitor guard's new_fail/
    # new_critical merge.
    sid = _suite_id(client, db_session)
    cid = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(warn_threshold=10, fail_threshold=20),
    ).json()["id"]
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"fail_threshold": 5})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_update_accepts_valid_ordering(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"warn_threshold": 1, "fail_threshold": 5, "critical_threshold": 20},
    )
    assert resp.status_code == 200


def test_create_freshness_monitor_rejects_inverted_thresholds(
    client: TestClient, db_session: Any
) -> None:
    # The generic ordering guard is kind-agnostic — it applies to freshness/
    # volume monitor checks too, not just plain expectations.
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_freshness_payload(fail_threshold=50, critical_threshold=10),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


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


def test_deeply_nested_oversized_config_echo_is_bounded(
    client: TestClient, db_session: Any
) -> None:
    # Per-segment truncation alone would still let the ACCUMULATED path grow
    # ~200 chars per nesting level — 50 levels of 1k keys would echo a ~10KB
    # path. The whole reported path is bounded, not just each segment.
    sid = _suite_id(client, db_session)
    nested: Any = "v" * 100_000
    for i in range(50):
        nested = {f"level_{i}_{'k' * 1_000}": nested}
    resp = client.post(
        f"/api/v1/suites/{sid}/checks", json=_payload(config={"column": "ok", "deep": nested})
    )
    assert resp.status_code == 422
    assert len(resp.text) < 5_000


def test_oversized_walk_tolerates_non_string_dict_keys() -> None:
    # JSON transports only produce string keys, but the MCP tools / direct
    # callers can hand the service arbitrary dicts — an int key must yield the
    # normal 422, not a TypeError→500 from slicing the key.
    from backend.app.services.check_service import (
        CheckConfigInvalidError,
        validate_expectation_check,
    )

    with pytest.raises(CheckConfigInvalidError) as exc_info:
        validate_expectation_check(
            "expect_column_values_to_be_in_set",
            {"column": "x", "value_set": {1: "y" * 20_000}},
        )
    assert "exceeds" in str(exc_info.value)


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


def test_import_suite_service_rejects_oversized_name(db_session: Any) -> None:
    # The REST import route's `CheckDocument` Pydantic model already caps
    # name/expectation_type at 256/128, but `suite_io_service.import_suite`
    # builds `Check(...)` ORM objects directly with no Pydantic layer of its
    # own — a direct caller must still get a clean 422, not a raw Postgres
    # `StringDataRightTruncation` on the INSERT (#813 follow-up).
    from backend.app.services import suite_io_service
    from backend.app.services.check_service import CheckConfigInvalidError

    owner = User(aad_object_id=uuid.uuid4().hex, email="importer@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"snowflake-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()

    with pytest.raises(CheckConfigInvalidError, match="name"):
        suite_io_service.import_suite(
            db_session,
            version=1,
            name="smuggled",
            description=None,
            checks=[
                {
                    "name": "x" * 257,
                    "kind": "expectation",
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "config": {"column": "email"},
                    "warn_threshold": None,
                    "fail_threshold": None,
                    "critical_threshold": None,
                }
            ],
            connection_id=conn.id,
            created_by=owner.id,
        )
    names = [s.name for s in db_session.query(Suite).all()]
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


def test_create_monitor_on_flatfile_datasource_returns_201(
    client: TestClient, db_session: Any
) -> None:
    # #520: flat files ARE monitor-capable now — the runner computes volume as the
    # resolved batch's row count. (This test asserted the opposite before #520.)
    sid = _suite_id(client, db_session, conn_type="s3")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_volume_payload())
    assert resp.status_code == 201
    assert resp.json()["kind"] == "volume"


def test_create_freshness_without_a_column_on_flatfile_returns_201(
    client: TestClient, db_session: Any
) -> None:
    """#520's headline: a flat file can measure freshness from ARRIVAL time, which
    no SQL datasource can express. This is the case that catches "the producer
    stopped sending files" — invisible to an in-file MAX, since the newest file is
    old but its rows look fresh."""
    sid = _suite_id(client, db_session, conn_type="s3")
    payload = {**_freshness_payload(), "config": {}}
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=payload)
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("conn_type", ["snowflake", "unity_catalog", "iceberg"])
def test_create_freshness_without_a_column_on_non_file_datasource_rejected(
    client: TestClient, db_session: Any, conn_type: str
) -> None:
    """A table has no arrival time, so a column-less freshness monitor there can
    never be evaluated. It must fail at AUTHOR time — the shared config validator
    allows the omission (for flat files), so without this gate the check would save
    clean and then error on every run."""
    sid = _suite_id(client, db_session, conn_type=conn_type)
    payload = {**_freshness_payload(), "config": {}}
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=payload)
    assert resp.status_code == 422
    assert "arrival time" in resp.json()["error"]["message"]


def _anomaly_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "orders volume anomaly",
        "kind": "anomaly",
        "expectation_type": "monitor:anomaly",
        "config": {"target_metric": "row_count"},
        "fail_threshold": 3,  # z-score — required so it can actually fail
    }
    body.update(overrides)
    return body


def test_create_anomaly_monitor_on_sql_datasource_returns_201(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_anomaly_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "anomaly"
    assert body["expectation_type"] == "monitor:anomaly"
    # ADR 0038: anomaly has no derivable dimension (it depends on the target
    # metric, which derivation cannot see) — NULL is the honest answer and renders
    # as a coverage gap rather than a confident guess.
    assert body["dimension"] is None


def test_create_anomaly_accepts_an_explicit_dimension(client: TestClient, db_session: Any) -> None:
    """Underivable is not unclassifiable: the author can still say what it measures."""
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks", json=_anomaly_payload(dimension="completeness")
    )
    assert resp.status_code == 201
    assert resp.json()["dimension"] == "completeness"


def test_create_anomaly_without_threshold_rejected(client: TestClient, db_session: Any) -> None:
    # Same silent-green guard as freshness (#426): the z-score has no in-config
    # bound, so with no fail/critical threshold the check can never fail.
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_anomaly_payload(fail_threshold=None, critical_threshold=None),
    )
    assert resp.status_code == 422
    assert "z-score" in resp.json()["error"]["message"]


def test_create_anomaly_with_zero_threshold_rejected(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_anomaly_payload(fail_threshold=0, critical_threshold=None),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"target_metric": "rowcount"},
        {"target_metric": "freshness_age_hours"},  # needs a column
        {"target_metric": "row_count", "column": "loaded_at"},  # column inapplicable
        {"target_metric": "row_count", "window": 200},
        {"target_metric": "row_count", "window": 5, "min_points": 6},
    ],
)
def test_create_anomaly_with_malformed_config_rejected(
    client: TestClient, db_session: Any, config: dict[str, Any]
) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_anomaly_payload(config=config))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


@pytest.mark.parametrize("conn_type", ["iceberg", "s3", "adls_gen2"])
def test_create_anomaly_on_a_non_sql_datasource_rejected(
    client: TestClient, db_session: Any, conn_type: str
) -> None:
    """Iceberg and flat files ARE monitor-capable for freshness/volume — they
    compute those scalars natively inside their runners. A stateful kind never
    reaches a runner, and the anomaly executor measures over a live SQL
    connection, so the pairing is refused at author time rather than saved and
    then erroring on every scheduled run."""
    sid = _suite_id(client, db_session, conn_type=conn_type)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_anomaly_payload())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_update_anomaly_to_a_malformed_config_rejected(client: TestClient, db_session: Any) -> None:
    """The update path re-validates the POST-patch config, so a check cannot be
    edited into a shape that create would have refused."""
    sid = _suite_id(client, db_session, conn_type="snowflake")
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_anomaly_payload())
    check_id = created.json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{check_id}",
        json={"config": {"target_metric": "row_count", "window": 1}},
    )
    assert resp.status_code == 422


def test_rebaseline_works_for_an_anomaly_check(client: TestClient, db_session: Any) -> None:
    """The rebaseline endpoint gates on STATEFUL_MONITOR_KINDS (derived from the
    registry), so registering `anomaly` widened it with no endpoint change. 204
    whether or not a baseline exists."""
    sid = _suite_id(client, db_session, conn_type="snowflake")
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_anomaly_payload())
    check_id = created.json()["id"]
    resp = client.post(f"/api/v1/suites/{sid}/checks/{check_id}/rebaseline")
    assert resp.status_code == 204


def test_rebaseline_still_rejects_a_non_stateful_kind(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session, conn_type="snowflake")
    created = client.post(f"/api/v1/suites/{sid}/checks", json=_volume_payload())
    check_id = created.json()["id"]
    resp = client.post(f"/api/v1/suites/{sid}/checks/{check_id}/rebaseline")
    assert resp.status_code == 422


def test_create_monitor_on_iceberg_datasource_returns_201(
    client: TestClient, db_session: Any
) -> None:
    # Iceberg computes freshness/volume natively (ADR 0030) — monitor-capable even
    # though it is NOT SQL-queryable (no custom-SQL). #716 review finding.
    sid = _suite_id(client, db_session, conn_type="iceberg")
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_volume_payload())
    assert resp.status_code == 201
    assert resp.json()["kind"] == "volume"


def test_create_custom_sql_on_iceberg_datasource_rejected(
    client: TestClient, db_session: Any
) -> None:
    # The distinction: Iceberg supports monitors but is a native DataFrame read, not
    # SQL-queryable — a custom-SQL check must still 422.
    sid = _suite_id(client, db_session, conn_type="iceberg")
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "iceberg custom sql",
            "kind": "expectation",
            "expectation_type": "unexpected_rows_expectation",
            "config": {"query": "SELECT * FROM t WHERE x IS NULL"},
        },
    )
    assert resp.status_code == 422


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
    resp = client.get(f"/api/v1/suites/{sid_a}/checks/{cid}")
    assert resp.status_code == 200


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
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}")
    assert resp.status_code == 404


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
    resp = client.get(f"/api/v1/suites/{sid}/checks")
    assert resp.status_code == 200
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}")
    assert resp.status_code == 200
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
    resp = client.get(f"/api/v1/suites/{sid}/checks")
    assert resp.status_code == 404


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
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"name": "orders not null"})
    assert resp.status_code == 200
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={})
    assert resp.status_code == 200

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


# ───────────────────────── restore a version (#283) ─────────────────


def test_restore_creates_a_new_version_with_the_snapshotted_config(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"config": {"column": "amount"}, "warn_threshold": 0.9},
    )  # -> v2: current config is now {"column": "amount"}, warn 0.9

    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/1/restore")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config"] == {"column": "order_id"}
    assert body["warn_threshold"] is None

    # Restore is additive: v1 and v2 both survive, and a NEW v3 (identical to
    # v1's content) is appended — never a renumber/overwrite of history.
    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert [v["version_no"] for v in versions] == [3, 2, 1]
    assert versions[0]["config"] == {"column": "order_id"}
    assert versions[0]["warn_threshold"] is None
    assert versions[1]["config"] == {"column": "amount"}
    assert versions[2]["config"] == {"column": "order_id"}


def test_restore_of_the_current_version_is_a_noop(client: TestClient, db_session: Any) -> None:
    """Restoring the already-current version mints no duplicate — the same
    `session.is_modified` no-op-PATCH gating `update_check` already applies to a
    manual no-op edit (see `test_noop_update_does_not_append_a_version`)."""
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]

    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/1/restore")
    assert resp.status_code == 200

    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert [v["version_no"] for v in versions] == [1]


def test_restore_unknown_version_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/99/restore")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "check_version_not_found"


def test_restore_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}/versions/1/restore")
    assert resp.status_code == 404


def test_restore_requires_edit_permission(client: TestClient, db_session: Any) -> None:
    owner, b, e, sid = _owner_b_e_suite(db_session)
    _as(owner)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"config": {"column": "amount"}})
    _grant(client, owner, sid, b, "view")
    _as(b)
    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/1/restore")
    assert resp.status_code == 403

    _as(e)  # not owner, not shared — existence is hidden
    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/1/restore")
    assert resp.status_code == 404


def test_editor_can_restore(client: TestClient, db_session: Any) -> None:
    owner, b, _e, sid = _owner_b_e_suite(db_session)
    _as(owner)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"config": {"column": "amount"}})
    _grant(client, owner, sid, b, "edit")
    _as(b)
    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/1/restore")
    assert resp.status_code == 200
    assert resp.json()["config"] == {"column": "order_id"}


def test_restore_rejects_a_snapshot_invalid_under_current_threshold_ordering(
    client: TestClient, db_session: Any
) -> None:
    """A version recorded before #568's ordering gate existed could hold reversed
    thresholds. Restoring it must re-validate against TODAY's rules and 422,
    leaving the live check exactly as it was — not silently reinstate a
    configuration today's authoring path would refuse to create."""
    sid = _suite_id(client, db_session)
    cid = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(warn_threshold=1, fail_threshold=5, critical_threshold=10),
    ).json()["id"]
    db_session.add(
        CheckVersion(
            check_id=uuid.UUID(cid),
            version_no=2,
            name="orders not null",
            kind="expectation",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "order_id"},
            warn_threshold=90,
            fail_threshold=50,
            critical_threshold=10,
        )
    )
    db_session.commit()

    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/2/restore")
    assert resp.status_code == 422

    check = client.get(f"/api/v1/suites/{sid}/checks/{cid}").json()
    assert check["warn_threshold"] == 1.0
    assert check["fail_threshold"] == 5.0
    assert check["critical_threshold"] == 10.0
    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1]  # no v3 minted


def test_restore_rejects_a_snapshot_invalid_under_current_custom_sql_gating(
    client: TestClient, db_session: Any
) -> None:
    """A legacy snapshot with a non-read-only query must not be reinstated —
    ADR 0019 gating is re-applied by the same `update_check` path a manual PATCH
    goes through (see `test_update_custom_sql_to_non_readonly_query_rejected`)."""
    sid = _suite_id(client, db_session, conn_type="snowflake")
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_custom_sql_payload()).json()["id"]
    db_session.add(
        CheckVersion(
            check_id=uuid.UUID(cid),
            version_no=2,
            name="no negative totals",
            kind="expectation",
            expectation_type="unexpected_rows_expectation",
            config={"unexpected_rows_query": "DELETE FROM {batch}"},
        )
    )
    db_session.commit()

    resp = client.post(f"/api/v1/suites/{sid}/checks/{cid}/versions/2/restore")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "custom_sql_invalid"

    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1]  # untouched, no v3 minted


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
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}/history")
    assert resp.json() == []


def test_history_honours_limit_keeping_most_recent(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    for age in (3, 2, 1):
        _run_with_result(db_session, sid, cid, status="pass", metric_value=age, age_days=age)

    history = client.get(f"/api/v1/suites/{sid}/checks/{cid}/history?limit=2").json()
    # Latest 2 by run time, returned chronologically: age=2 then age=1.
    assert [p["metric_value"] for p in history] == [2.0, 1.0]
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}/history?limit=0")
    assert resp.status_code == 422


def test_history_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.get(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}/history")
    assert resp.status_code == 404


def test_history_outsider_cannot_read(client: TestClient, db_session: Any) -> None:
    owner, _b, e, sid = _owner_b_e_suite(db_session)
    _as(owner)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    _as(e)  # not owner, not shared
    # An outsider gets 404, not 403 — the suite's existence is hidden from
    # non-members (same as `test_outsider_cannot_see_checks`).
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}/history")
    assert resp.status_code == 404


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
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions")
    assert resp.status_code == 200
    _as(e)  # an outsider sees the suite as nonexistent (404, not 403)
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions")
    assert resp.status_code == 404


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


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch, runner: _FakeRunner, calls: list[dict[str, Any]] | None = None
) -> None:
    """Patch the runner registry so dry-run gets the fake runner for any datasource.
    When ``calls`` is given, it captures the kwargs `build_check_runner` was called
    with (e.g. to assert the UC ``catalog`` is threaded through)."""

    def _fake_build(**kw: Any) -> _FakeRunner:
        if calls is not None:
            calls.append(kw)
        return runner

    monkeypatch.setattr(dryrun_service, "build_check_runner", _fake_build)


def _dryrun_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expectation_type": "expect_column_values_to_not_be_null",
        "config": {"column": "order_id"},
    }
    body.update(overrides)
    return body


_SF_TARGET = {"table": "ORDERS"}


def test_dryrun_returns_pass_preview(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _suite_id(client, db_session, target=_SF_TARGET)
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


def test_dryrun_rejects_inverted_thresholds(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #568: a preview must never accept a threshold set a save would reject.
    # Validated before the (live) datasource connect, so no runner mock is
    # needed here — a mocked runner would prove nothing about this guard.
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(warn_threshold=90, fail_threshold=50, critical_threshold=10),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_dryrun_rejects_inverted_thresholds_for_schema_drift(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #568 follow-up (code review on PR #1113): schema_drift dry-run branches to
    # `_dry_run_schema_drift` BEFORE the ordering guard used to run, so this kind
    # slipped through with a 200 "pass" preview even though create/update band
    # schema_drift's thresholds like any other kind and would 422 the save. The
    # guard now sits above that branch — no runner/introspection mock needed,
    # since a rejected threshold set never reaches the live datasource connect.
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(
            kind="schema_drift", warn_threshold=90, fail_threshold=50, critical_threshold=10
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_dryrun_derives_tier_from_thresholds(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _suite_id(client, db_session, target=_SF_TARGET)
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
    sid = _suite_id(client, db_session, target=_SF_TARGET)
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
    sid = _suite_id(client, db_session, target=_SF_TARGET)
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


def test_dryrun_rejects_a_kind_with_no_preview_shape(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body(kind="freshness"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "dry_run_unsupported"


def _patch_anomaly_scalar(monkeypatch: pytest.MonkeyPatch, scalar: Any) -> None:
    from contextlib import contextmanager

    from backend.app.services import anomaly

    class _Conn:
        @staticmethod
        def execute(statement: Any) -> Any:
            class _Res:
                @staticmethod
                def scalar() -> Any:
                    return scalar

            return _Res()

    @contextmanager
    def fake_open(connection: Any, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(anomaly, "_open_connection", fake_open)


def test_dryrun_anomaly_previews_the_cold_start_with_the_real_measurement(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry-run has no check row, so it can never have a baseline — the honest
    preview is the cold-start `skip`, carrying the value actually measured. A
    fabricated z-score off an empty history would be the exact silent-green the
    kind exists to avoid."""
    _patch_anomaly_scalar(monkeypatch, 32840)
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(
            kind="anomaly",
            expectation_type="monitor:anomaly",
            config={"target_metric": "row_count"},
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "skip"
    assert body["metric_value"] is None
    assert body["observed_value"]["value"] == 32840.0
    assert body["observed_value"]["insufficient_history"] is True
    assert body["observed_value"]["dry_run"] is True


def test_dryrun_anomaly_writes_no_baseline(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.db.models import MonitorBaseline

    _patch_anomaly_scalar(monkeypatch, 10)
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(
            kind="anomaly",
            expectation_type="monitor:anomaly",
            config={"target_metric": "row_count"},
        ),
    )
    assert db_session.query(MonitorBaseline).count() == 0


def test_dryrun_anomaly_rejects_a_malformed_config(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(
            kind="anomaly", expectation_type="monitor:anomaly", config={"target_metric": "nope"}
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "dry_run_unsupported"


def test_dryrun_anomaly_on_an_empty_table_is_a_clear_failure(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing MAX has no age; the preview must say so rather than report 0
    hours, which would read as "perfectly fresh"."""
    _patch_anomaly_scalar(monkeypatch, None)
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json=_dryrun_body(
            kind="anomaly",
            expectation_type="monitor:anomaly",
            config={"target_metric": "freshness_age_hours", "column": "loaded_at"},
        ),
    )
    assert resp.status_code == 502
    assert "unavailable" in resp.json()["error"]["message"]


def _ok_runner() -> _FakeRunner:
    return _FakeRunner(
        SuiteOutcome(
            success=True,
            checks=[CheckOutcome("x", success=True, observed_value={"observed_value": 1})],
        )
    )


def test_dryrun_supports_flatfile_suite(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #532: flat-file (S3/local) suites are now previewable via the runner registry.
    sid = _suite_id(client, db_session, conn_type="s3", target={"path": "s3://b/orders.csv"})
    runner = _ok_runner()
    _patch_runner(monkeypatch, runner)
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 200
    assert resp.json()["status"] == "pass"
    # The materialized flat-file path is handed to the runner as its `table`.
    assert runner.called_with is not None
    assert runner.called_with["table"] == "s3://b/orders.csv"


def test_dryrun_supports_unity_catalog_suite(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #532: Unity Catalog suites are previewable, and the suite's catalog is
    # threaded into the runner build (as the real run path does).
    sid = _suite_id(
        client,
        db_session,
        conn_type="unity_catalog",
        target={"table": "ORDERS", "schema": "SALES", "catalog": "MAIN"},
    )
    calls: list[dict[str, Any]] = []
    _patch_runner(monkeypatch, _ok_runner(), calls=calls)
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 200
    assert resp.json()["status"] == "pass"
    assert calls and calls[0]["catalog"] == "MAIN"


def test_dryrun_supports_iceberg_suite(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #721: Iceberg suites are previewable via the runner registry; the suite's
    # optional namespace folds into the `namespace.table` identifier the runner
    # receives as its `table` (as the real run path does — run_target.resolve_target).
    sid = _suite_id(
        client, db_session, conn_type="iceberg", target={"table": "ORDERS", "namespace": "sales"}
    )
    runner = _ok_runner()
    _patch_runner(monkeypatch, runner)
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 200
    assert resp.json()["status"] == "pass"
    assert runner.called_with is not None
    assert runner.called_with["table"] == "sales.ORDERS"


def test_dryrun_targetless_suite_returns_422(client: TestClient, db_session: Any) -> None:
    # No run target on the suite → a clean 422 (not a 500), resolved by run_target.
    sid = _suite_id(client, db_session)  # no target
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_target_invalid"


def test_dryrun_flatfile_batch_not_landed_returns_422(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A batch flat-file target whose file hasn't landed is a clean 422 (no data),
    # not a 500 or a misleading datasource failure.
    from backend.app.datasources.flatfile import BatchNotFoundError
    from backend.app.services import run_target

    sid = _suite_id(
        client,
        db_session,
        conn_type="s3",
        target={"pattern": r"orders_(\d+)\.csv", "strategy": "latest"},
    )
    _patch_runner(monkeypatch, _ok_runner())

    def _raise_not_found(*_a: Any, **_k: Any) -> str:
        raise BatchNotFoundError("no file")

    # dryrun_service calls `run_target.materialize_path` on this same module object.
    monkeypatch.setattr(run_target, "materialize_path", _raise_not_found)
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "dry_run_no_data"


def test_dryrun_rejects_non_readonly_custom_sql_before_running(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dry-run executes the query, so the custom-SQL guardrail must apply here too
    # (ADR 0019 review): a non-read-only query is a 422 and the runner is never
    # reached.
    sid = _suite_id(client, db_session, target=_SF_TARGET)
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
    sid = _suite_id(client, db_session, target=_SF_TARGET)
    _patch_runner(monkeypatch, _FakeRunner(raises=RuntimeError("warehouse unreachable")))
    resp = client.post(f"/api/v1/suites/{sid}/checks/dryrun", json=_dryrun_body())
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "dry_run_failed"


def test_dryrun_runner_build_failure_returns_502(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The runner builders resolve the secret eagerly — a missing/unreadable
    # credential fails at build time and must be a clean 502, not a 500.
    sid = _suite_id(client, db_session, target=_SF_TARGET)

    def _boom(**_kw: Any) -> Any:
        raise RuntimeError("secret not found in key vault")

    monkeypatch.setattr(dryrun_service, "build_check_runner", _boom)
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
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}")
    assert resp.json()["alert_snoozed_until"] is None

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


# ───────────────── DQ dimension (ADR 0038, #124) ────────────────────


def test_create_derives_the_dimension_when_unspecified(client: TestClient, db_session: Any) -> None:
    """The common path: nobody should hand-classify a not-null check."""
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload())
    assert resp.status_code == 201
    assert resp.json()["dimension"] == "completeness"


def test_create_honours_an_explicit_dimension_over_the_derived_one(
    client: TestClient, db_session: Any
) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(dimension="accuracy"))
    assert resp.status_code == 201
    assert resp.json()["dimension"] == "accuracy"


def test_create_leaves_an_underivable_check_unclassified(
    client: TestClient, db_session: Any
) -> None:
    """NULL is a real state (ADR 0038 §3), not a failure — a custom-SQL predicate
    genuinely cannot be classified, and the scorecard must see the gap."""
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(
            name="custom",
            expectation_type="unexpected_rows_expectation",
            config={"unexpected_rows_query": "SELECT 1 WHERE false"},
        ),
    )
    assert resp.status_code == 201
    assert resp.json()["dimension"] is None


@pytest.mark.parametrize("bad", ["Completeness", "timeliness ", "freshness", "nonsense"])
def test_create_rejects_a_non_canonical_dimension(
    client: TestClient, db_session: Any, bad: str
) -> None:
    """The vocabulary is closed so coverage reporting can be truthful — a typo'd
    'timeliness ' would make "you have no Timeliness checks" a lie. Note
    'freshness' is a KIND, not a dimension: the axes are easy to confuse."""
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(dimension=bad))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_dimension_is_reclassifiable_by_patch(client: TestClient, db_session: Any) -> None:
    """ADR 0038 §2 — derivation is a guess about intent, so the override must be
    changeable after creation, not only at authoring time."""
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"dimension": "integrity"})
    assert resp.status_code == 200
    assert resp.json()["dimension"] == "integrity"


def test_patch_rejects_a_non_canonical_dimension(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"dimension": "nope"})
    assert resp.status_code == 422
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}")
    assert resp.json()["dimension"] == (
        "completeness"
    )  # unchanged — a rejected PATCH must leave nothing dirty


def test_patch_without_a_dimension_does_not_clear_it(client: TestClient, db_session: Any) -> None:
    """PATCH convention: None = "not provided". A rename must not silently wipe
    the classification."""
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(dimension="accuracy")).json()[
        "id"
    ]
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["dimension"] == "accuracy"


def test_version_history_snapshots_the_dimension(client: TestClient, db_session: Any) -> None:
    """Without the snapshot, history would show the CURRENT classification against
    an OLD config — and a future restore would silently reclassify."""
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    client.patch(f"/api/v1/suites/{sid}/checks/{cid}", json={"dimension": "validity"})
    versions = client.get(f"/api/v1/suites/{sid}/checks/{cid}/versions").json()
    # Newest first: v2 carries the override, v1 the derived default.
    assert [v["dimension"] for v in versions] == ["validity", "completeness"]


def test_monitor_kinds_derive_their_dimension(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    fresh = client.post(f"/api/v1/suites/{sid}/checks", json=_freshness_payload())
    vol = client.post(f"/api/v1/suites/{sid}/checks", json=_volume_payload())
    assert fresh.json()["dimension"] == "timeliness"
    assert vol.json()["dimension"] == "completeness"
