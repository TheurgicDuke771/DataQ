"""The Amazon Athena datasource (#2131), executed for real against a live Athena workgroup.

Opt-in, nothing mocked — every value crosses the real ``pyathena`` boundary (#953):

* ``ATHENA_TEST_BUCKET`` — an S3 bucket the ADMIN credential (boto3's default chain) can write:
  the fixture seeds a Glue database by CTAS into ``athena-data/<db>/`` and removes it after.
* ``ATHENA_TEST_READER`` — JSON ``{"access_key_id", "secret_access_key"}`` of a least-privileged
  IAM user: Athena query + Glue read, S3 read on ``athena-data/*``, and write on
  ``athena-results/reader/*`` only. DataQ runs as this principal, never the admin. Nothing in it
  is ever printed.
* ``ATHENA_TEST_REGION`` (default ``us-east-2``) and ``ATHENA_TEST_WORK_GROUP`` (``primary``).

Every check is a billed Athena query (a few MB each here), and each takes seconds.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text

from backend.app.datasources import generic_sql, registry
from backend.app.datasources.base import CheckSpec, MonitorSpec, TargetShapeError
from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
)
from backend.app.datasources.registry import build_check_runner, get_connection_adapter
from backend.app.datasources.sql_engines import SQL_ENGINES
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.lineage.warehouse import get_table_enumerator
from backend.app.services import browse_service, profile_service, run_service, schema_drift
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE
from backend.app.services.dataset_reader import DatasetSpec, read_dataset
from backend.app.services.failure_classifier import (
    FailureCategory,
    classify_failure_category,
    is_auth_failure,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

_BUCKET = os.environ.get("ATHENA_TEST_BUCKET", "").strip()
_READER = os.environ.get("ATHENA_TEST_READER", "").strip()
_REGION = os.environ.get("ATHENA_TEST_REGION", "us-east-2").strip()
_WORK_GROUP = os.environ.get("ATHENA_TEST_WORK_GROUP", "primary").strip()
pytestmark = pytest.mark.skipif(
    not (_BUCKET and _READER), reason="requires ATHENA_TEST_BUCKET + ATHENA_TEST_READER"
)

_SPEC = SQL_ENGINES["athena"]
_SECRET_REF = "athena-reader"


@dataclass(frozen=True)
class AthenaTarget:
    config: dict[str, Any]
    schema: str
    store: FakeSecretStore


def _seed(db: str) -> list[str]:
    ts = "CAST(current_timestamp AS timestamp)"
    rows = [
        f"(CAST(1 AS bigint), CAST(10 AS bigint), 'new', CAST(19.99 AS decimal(12, 2)),"
        f" {ts} - INTERVAL '2' HOUR, {ts} - INTERVAL '3' HOUR, current_date, 'ann@example.com',"
        " '{\"a\": 1}', false)",
        f"(CAST(2 AS bigint), CAST(11 AS bigint), 'shipped', CAST(5.00 AS decimal(12, 2)),"
        f" {ts} - INTERVAL '3' HOUR, {ts} - INTERVAL '4' HOUR, current_date - INTERVAL '1' DAY,"
        " 'bob@example.com', '{\"a\": 2}', true)",
        "(CAST(3 AS bigint), CAST(NULL AS bigint), 'shipped', CAST(250.10 AS decimal(12, 2)),"
        f" {ts} - INTERVAL '5' HOUR, CAST(NULL AS timestamp), current_date - INTERVAL '2' DAY,"
        " CAST(NULL AS varchar), CAST(NULL AS varchar), CAST(NULL AS boolean))",
        f"(CAST(4 AS bigint), CAST(12 AS bigint), 'cancelled', CAST(-3.50 AS decimal(12, 2)),"
        f" {ts} - INTERVAL '30' HOUR, {ts} - INTERVAL '31' HOUR, current_date - INTERVAL '3' DAY,"
        " 'cy@example.com', '[]', false)",
        f"(CAST(5 AS bigint), CAST(12 AS bigint), 'bogus', CAST(42.00 AS decimal(12, 2)),"
        f" {ts} - INTERVAL '24' HOUR, {ts} - INTERVAL '25' HOUR, current_date - INTERVAL '1' DAY,"
        " 'dee@example.com', '{\"a\": 5}', false)",
        "(CAST(6 AS bigint), CAST(13 AS bigint), CAST(NULL AS varchar),"
        " CAST(0.00 AS decimal(12, 2)), CAST(NULL AS timestamp), CAST(NULL AS timestamp),"
        " CAST(NULL AS date),"
        " 'eve@example.com', '{\"a\": 6}', true)",
    ]
    location = f"s3://{_BUCKET}/athena-data/{db}"
    return [
        f"CREATE DATABASE {db}",
        f"CREATE TABLE {db}.orders WITH (format = 'PARQUET', external_location = "
        f"'{location}/orders/') AS SELECT * FROM (VALUES {', '.join(rows)}) AS t(order_id,"
        " customerid, status, amount, placed_at, shipped_local, order_date, email, payload,"
        " is_gift)",
        f"CREATE TABLE {db}.orders_lc WITH (format = 'PARQUET', external_location = "
        f"'{location}/orders_lc/') AS SELECT * FROM (VALUES (CAST(1 AS bigint), 'a'),"
        " (CAST(2 AS bigint), 'b'), (CAST(2 AS bigint), 'A')) AS t(id, label)",
        f"CREATE VIEW {db}.big_orders AS SELECT * FROM {db}.orders WHERE amount > 10",
    ]


def _admin(statement: str) -> None:
    import boto3

    athena = boto3.client("athena", region_name=_REGION)
    query = athena.start_query_execution(
        QueryString=statement,
        WorkGroup=_WORK_GROUP,
        ResultConfiguration={"OutputLocation": f"s3://{_BUCKET}/athena-results/admin/"},
    )["QueryExecutionId"]
    while True:
        status = athena.get_query_execution(QueryExecutionId=query)["QueryExecution"]["Status"]
        if status["State"] not in ("QUEUED", "RUNNING"):
            break
        time.sleep(0.5)
    if status["State"] != "SUCCEEDED":
        raise RuntimeError(f"seed failed: {statement[:60]}: {status.get('StateChangeReason')}")


@pytest.fixture(scope="module")
def athena() -> Iterator[AthenaTarget]:
    import boto3

    db = f"dq_athena_{uuid.uuid4().hex[:8]}"
    reader = json.loads(_READER)
    try:
        for statement in _seed(db):
            _admin(statement)
        config = {
            "region": _REGION,
            "work_group": _WORK_GROUP,
            "s3_staging_dir": f"s3://{_BUCKET}/athena-results/reader/",
            "access_key_id": reader["access_key_id"],
            "schema": db,
        }
        yield AthenaTarget(config, db, FakeSecretStore({_SECRET_REF: reader["secret_access_key"]}))
    finally:
        for statement in (
            f"DROP VIEW IF EXISTS {db}.big_orders",
            f"DROP TABLE IF EXISTS {db}.orders",
            f"DROP TABLE IF EXISTS {db}.orders_lc",
            f"DROP DATABASE IF EXISTS {db}",
        ):
            _admin(statement)
        s3 = boto3.client("s3", region_name=_REGION)
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=_BUCKET, Prefix=f"athena-data/{db}/"
        ):
            for item in page.get("Contents", []):
                s3.delete_object(Bucket=_BUCKET, Key=item["Key"])


def _secret(athena: AthenaTarget) -> str:
    return athena.store.get(_SECRET_REF)


def _runner(athena: AthenaTarget, **overrides: Any) -> Any:
    return build_check_runner(
        conn_type="athena",
        config={**athena.config, **overrides},
        secret_ref=_SECRET_REF,
        secret_store=athena.store,
    )


def _connection(athena: AthenaTarget) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="athena",
        type="athena",
        env="dev",
        config=athena.config,
        secret_ref=_SECRET_REF,
    )


def _run(athena: AthenaTarget, table: str, checks: list[CheckSpec], **kwargs: Any) -> Any:
    runner = _runner(athena)
    try:
        return runner.run_checks(table=table, schema=athena.schema, checks=checks, **kwargs)
    finally:
        runner.close()


# ───────────────────────────── connection ─────────────────────────────


def test_a_real_session_as_the_least_privileged_reader(athena: AthenaTarget) -> None:
    get_connection_adapter("athena").test(athena.config, _secret(athena))


def test_a_wrong_secret_is_an_auth_failure(athena: AthenaTarget) -> None:
    with pytest.raises(Exception) as exc:
        get_connection_adapter("athena").test(athena.config, "not-the-secret-key")
    assert is_auth_failure(exc.value)


def test_the_session_is_pinned_to_the_catalog_and_schema(athena: AthenaTarget) -> None:
    config = _SPEC.validate_config(athena.config)
    url, connect_args = _SPEC.engine_args(config, _secret(athena))
    engine = create_engine(url, connect_args=connect_args)
    try:
        with engine.connect() as conn:
            catalog, schema = conn.execute(text("SELECT current_catalog, current_schema")).one()
    finally:
        engine.dispose()
    assert (catalog, schema) == ("awsdatacatalog", athena.schema)


def test_the_iam_policy_is_the_read_only_guarantee(athena: AthenaTarget) -> None:
    """Athena has no read-only session: a reader principal must be refused a write by IAM."""
    config = _SPEC.validate_config(athena.config)
    engine = _SPEC.create_engine(config, _secret(athena))
    try:
        with pytest.raises(Exception) as exc, engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE TABLE {athena.schema}.written WITH (format = 'PARQUET')"
                    " AS SELECT 1 AS x"
                )
            )
    finally:
        engine.dispose()
    assert "denied" in str(exc.value).lower() or "not authorized" in str(exc.value).lower()


# ───────────────────────────── expectations ─────────────────────────────

_CASES: list[tuple[str, dict[str, Any], bool]] = [
    ("expect_column_values_to_not_be_null", {"column": "email"}, False),
    ("expect_column_values_to_be_null", {"column": "payload"}, False),
    ("expect_column_values_to_be_unique", {"column": "customerid"}, False),
    (
        "expect_column_values_to_be_between",
        {"column": "amount", "min_value": -10, "max_value": 300},
        True,
    ),
    (
        "expect_column_values_to_be_in_set",
        {"column": "status", "value_set": ["new", "shipped", "cancelled"]},
        False,
    ),
    ("expect_column_values_to_not_be_in_set", {"column": "status", "value_set": ["x"]}, True),
    (
        "expect_column_distinct_values_to_be_in_set",
        {"column": "status", "value_set": ["new", "shipped", "cancelled", "bogus"]},
        True,
    ),
    (
        "expect_column_distinct_values_to_contain_set",
        {"column": "status", "value_set": ["new"]},
        True,
    ),
    ("expect_column_values_to_match_regex", {"column": "email", "regex": "@example\\.com$"}, True),
    ("expect_column_values_to_not_match_regex", {"column": "email", "regex": "^bob"}, False),
    (
        "expect_column_values_to_match_regex_list",
        {"column": "email", "regex_list": ["^a", "^b"], "match_on": "any"},
        False,
    ),
    (
        "expect_column_values_to_not_match_regex_list",
        {"column": "email", "regex_list": ["^z"]},
        True,
    ),
    (
        "expect_column_value_lengths_to_be_between",
        {"column": "status", "min_value": 3, "max_value": 9},
        True,
    ),
    ("expect_column_value_lengths_to_equal", {"column": "status", "value": 7}, False),
    # Athena reflects bare type names (live-found).
    ("expect_column_values_to_be_of_type", {"column": "amount", "type_": "DECIMAL"}, True),
    (
        "expect_column_values_to_be_in_type_list",
        {"column": "shipped_local", "type_list": ["TIMESTAMP"]},
        True,
    ),
    ("expect_compound_columns_to_be_unique", {"column_list": ["order_id", "customerid"]}, True),
    (
        "expect_select_column_values_to_be_unique_within_record",
        {"column_list": ["order_id", "customerid"]},
        True,
    ),
    (
        "expect_column_pair_values_a_to_be_greater_than_b",
        {"column_A": "order_id", "column_B": "amount"},
        False,
    ),
    (
        "expect_column_pair_values_to_be_equal",
        {"column_A": "order_id", "column_B": "customerid"},
        False,
    ),
    (
        "expect_column_pair_values_to_be_in_set",
        {
            "column_A": "status",
            "column_B": "is_gift",
            "value_pairs_set": [["new", False], ["shipped", True]],
        },
        False,
    ),
    (
        "expect_multicolumn_sum_to_equal",
        {"column_list": ["order_id", "customerid"], "sum_total": 12},
        False,
    ),
    ("expect_table_row_count_to_be_between", {"min_value": 6, "max_value": 6}, True),
    (
        CUSTOM_SQL_EXPECTATION_TYPE,
        {"unexpected_rows_query": "SELECT * FROM {batch} WHERE amount < 0 OR customerid IS NULL"},
        False,
    ),
]


def test_the_cases_cover_every_type_a_sql_batch_can_run() -> None:
    covered = {expectation_type for expectation_type, _, _ in _CASES}
    assert covered == (ALLOWED_EXPECTATION_TYPES - DATAFRAME_ONLY_EXPECTATION_TYPES) | {
        CUSTOM_SQL_EXPECTATION_TYPE
    }


def test_every_sql_capable_type_runs_by_pushdown_with_the_expected_verdict(
    athena: AthenaTarget,
) -> None:
    checks = [CheckSpec(expectation_type, kwargs) for expectation_type, kwargs, _ in _CASES]
    outcome = _run(
        athena, "orders", checks, index_columns=["order_id"], value_signal_gate=lambda column: True
    )
    got = {
        spec.expectation_type: (result.errored, result.error_message, result.success)
        for spec, result in zip(checks, outcome.checks, strict=True)
    }
    assert got == {
        expectation_type: (False, None, verdict) for expectation_type, _, verdict in _CASES
    }


def test_uniqueness_reports_its_rows(athena: AthenaTarget) -> None:
    [result] = _run(
        athena,
        "orders",
        [CheckSpec("expect_column_values_to_be_unique", {"column": "customerid"})],
        index_columns=["order_id"],
    ).checks
    assert not result.errored, result.error_message
    assert (result.sample_failures or {})["unexpected_percent"] == 40.0
    assert sorted(
        (row["order_id"], row["customerid"])
        for row in (result.sample_failures or {})["unexpected_index_list"]
    ) == [(4, 12), (5, 12)]


def test_uniqueness_is_case_sensitive(athena: AthenaTarget) -> None:
    [result] = _run(
        athena, "orders_lc", [CheckSpec("expect_column_values_to_be_unique", {"column": "label"})]
    ).checks
    assert not result.errored, result.error_message
    assert result.success is True


def test_a_mixed_case_target_is_refused_when_the_suite_is_saved() -> None:
    """The Glue catalog reports every name lower case; `Orders` would never join its asset."""
    with pytest.raises(TargetShapeError, match="lower case"):
        registry.resolve_target_shape("athena", {"table": "Orders"})


def test_a_missing_table_is_a_config_error(athena: AthenaTarget) -> None:
    with pytest.raises(Exception) as exc:
        _run(athena, "nope", [CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0})])
    assert classify_failure_category(exc.value) is FailureCategory.CONFIG


# ───────────────────────────── monitors ─────────────────────────────


def test_freshness_over_timestamp_and_date_and_volume(athena: AthenaTarget) -> None:
    monitors = [
        MonitorSpec("freshness", {"column": "placed_at"}),
        MonitorSpec("freshness", {"column": "order_date"}),
        MonitorSpec("freshness", {"column": "status"}),
        MonitorSpec("volume", {"min_rows": 1, "max_rows": 5}),
    ]
    runner = _runner(athena)
    try:
        placed, dated, not_a_time, volume = runner.run_monitors(
            table="orders", schema=athena.schema, monitors=monitors
        )
    finally:
        runner.close()
    # A `timestamp` comes back naive, in UTC — Athena's only session zone.
    assert placed.metric_value is not None and 1.9 < placed.metric_value < 2.5
    assert dated.metric_value is not None and 0 <= dated.metric_value <= 48
    assert not_a_time.errored
    assert volume.observed_value == {"row_count": 6, "deviation_pct": 20.0}


def test_the_anomaly_monitor_measures_row_count_and_freshness_age(athena: AthenaTarget) -> None:
    from backend.app.datasources.monitors import anomaly_params
    from backend.app.services.anomaly import measure_metric

    connection, now = _connection(athena), datetime.now(UTC)
    rows = measure_metric(
        connection,
        table="orders",
        schema=athena.schema,
        catalog=None,
        params=anomaly_params({"target_metric": "row_count"}),
        secret_store=athena.store,
        now=now,
    )
    age = measure_metric(
        connection,
        table="orders",
        schema=None,
        catalog=None,
        params=anomaly_params({"target_metric": "freshness_age_hours", "column": "placed_at"}),
        secret_store=athena.store,
        now=now,
    )
    assert rows == 6.0
    assert age is not None and 1.9 < age < 2.5


# ───────────────────────────── introspection ─────────────────────────────


def test_the_profile_reports_decimals_booleans_and_timestamps(athena: AthenaTarget) -> None:
    profile = profile_service.profile_table(
        _connection(athena),
        table="orders",
        schema=athena.schema,
        columns=["amount", "payload", "is_gift", "placed_at"],
        top_n=2,
        secret_store=athena.store,
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 6
    assert (by_column["amount"].min_value, by_column["amount"].max_value) == (-3.5, 250.1)
    assert by_column["payload"].null_count == 1
    assert (by_column["is_gift"].min_value, by_column["is_gift"].max_value) == (False, True)
    assert by_column["placed_at"].min_value is not None


def test_columns_schema_drift_and_comparison_reads(athena: AthenaTarget) -> None:
    connection = _connection(athena)
    columns = profile_service.list_table_columns(
        connection, table="orders", schema=None, secret_store=athena.store
    )
    assert columns[:3] == ["order_id", "customerid", "status"]
    snapshot = schema_drift.introspect_columns(
        connection, table="orders", schema=athena.schema, catalog=None, secret_store=athena.store
    )
    assert {"name": "amount", "type": "decimal(12,2)"} in snapshot
    frame = read_dataset(
        connection,
        DatasetSpec(table="orders", schema=athena.schema),
        max_rows=10,
        secret_store=athena.store,
    )
    assert len(frame) == 6


def test_enumeration_joins_the_suite_target_identity(athena: AthenaTarget) -> None:
    with profile_service._open_connection(_connection(athena), athena.store) as conn:
        schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
        tables = generic_sql.typed_table_rows(_SPEC, conn, schema=athena.schema, limit=None)
        identities = get_table_enumerator("athena").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=athena.config
        )
    assert athena.schema in schemas and "information_schema" not in schemas
    assert sorted((t, kind) for _, t, kind in tables) == [
        ("big_orders", "view"),
        ("orders", "table"),
        ("orders_lc", "table"),
    ]
    target = resolve_asset_identity("athena", athena.config, {"table": "orders"})
    assert target.namespace == f"awsathena://athena.{_REGION}.amazonaws.com"
    assert target.name == f"awsdatacatalog.{athena.schema}.orders"
    assert (target.namespace, target.name) in {(i.namespace, i.name) for i in identities}


def _stored_connection(db_session: Any, athena: AthenaTarget) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"at-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"at-{uuid.uuid4().hex[:6]}",
        type="athena",
        env="dev",
        config=athena.config,
        secret_ref=_SECRET_REF,
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def test_browse_walks_schemas_then_tables(db_session: Any, athena: AthenaTarget) -> None:
    connection = _stored_connection(db_session, athena)
    kwargs: dict[str, Any] = {"session": db_session, "limit": 500, "secret_store": athena.store}
    top = browse_service.browse_catalog(connection, catalog=None, schema=None, **kwargs)
    tables = browse_service.browse_catalog(connection, catalog=None, schema=athena.schema, **kwargs)
    assert top.level == "schema" and athena.schema in [e.name for e in top.entries]
    assert [(e.name, e.object_type) for e in tables.entries] == [
        ("big_orders", "view"),
        ("orders", "table"),
        ("orders_lc", "table"),
    ]


# ───────────────────────────── end to end ─────────────────────────────


def test_a_suite_run_persists_results_end_to_end(db_session: Any, athena: AthenaTarget) -> None:
    connection = _stored_connection(db_session, athena)
    suite = Suite(name="athena", connection_id=connection.id, created_by=connection.created_by)
    db_session.add(suite)
    db_session.flush()
    checks = [
        Check(
            suite_id=suite.id,
            name="email_notnull",
            kind="expectation",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "email"},
        ),
        Check(
            suite_id=suite.id,
            name="negative_amounts",
            kind="expectation",
            expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
            config={"unexpected_rows_query": "SELECT * FROM {batch} WHERE amount < 0"},
        ),
        Check(
            suite_id=suite.id,
            name="rows",
            kind="volume",
            expectation_type="monitor:volume",
            config={"min_rows": 1, "max_rows": 100},
        ),
        Check(
            suite_id=suite.id,
            name="fresh",
            kind="freshness",
            expectation_type="monitor:freshness",
            config={"column": "placed_at", "max_age_hours": 24},
        ),
    ]
    db_session.add_all(checks)
    db_session.flush()
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()

    runner = _runner(athena)
    try:
        run_service.execute_run(
            db_session, run=run, checks=checks, runner=runner, table="orders", schema=athena.schema
        )
    finally:
        runner.close()

    assert run.status == "succeeded"
    by_check = {
        r.check_id: r for r in db_session.scalars(select(Result).where(Result.run_id == run.id))
    }
    assert [by_check[c.id].status for c in checks] == ["fail", "fail", "pass", "pass"]
    assert by_check[checks[2].id].observed_value == {"row_count": 6, "deviation_pct": 0.0}
