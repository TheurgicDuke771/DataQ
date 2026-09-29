"""The Amazon Redshift datasource (#1682), executed for real against a live Redshift.

Opt-in, nothing mocked — every value crosses the real psycopg2 / ``sqlalchemy-redshift`` boundary
(#953). A provisioned cluster or a Serverless workgroup both work:

* ``REDSHIFT_TEST_HOST`` — the endpoint host (``REDSHIFT_TEST_DATABASE``, default ``dev``).
* ``REDSHIFT_TEST_ADMIN_SECRET`` — the Secrets Manager id of JSON ``{"username", "password"}`` for
  a user that can create a schema and grant on it (Redshift's managed admin secret has this shape).
  The fixture seeds a throwaway schema and drops it after.
* ``REDSHIFT_TEST_READER_SECRET`` — the Secrets Manager id of the password (a plain string) of
  ``REDSHIFT_TEST_READER`` (default ``dq_reader``): a user with no privileges of its own. The
  fixture grants it USAGE + SELECT on the seeded schema only; DataQ runs as it, never the admin.

Secrets are read with boto3's default chain (``REDSHIFT_TEST_REGION``, default ``us-east-2``) and
never printed.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text

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

_HOST = os.environ.get("REDSHIFT_TEST_HOST", "").strip()
_DATABASE = os.environ.get("REDSHIFT_TEST_DATABASE", "dev").strip()
_ADMIN_SECRET = os.environ.get("REDSHIFT_TEST_ADMIN_SECRET", "").strip()
_READER_SECRET = os.environ.get("REDSHIFT_TEST_READER_SECRET", "").strip()
_READER = os.environ.get("REDSHIFT_TEST_READER", "dq_reader").strip()
_REGION = os.environ.get("REDSHIFT_TEST_REGION", "us-east-2").strip()
pytestmark = pytest.mark.skipif(
    not (_HOST and _ADMIN_SECRET and _READER_SECRET),
    reason="requires REDSHIFT_TEST_HOST + REDSHIFT_TEST_ADMIN_SECRET + REDSHIFT_TEST_READER_SECRET",
)

_SPEC = SQL_ENGINES["redshift"]
_SECRET_REF = "redshift-reader"


@dataclass(frozen=True)
class RedshiftTarget:
    config: dict[str, Any]
    schema: str
    store: FakeSecretStore


def _seed(schema: str) -> list[str]:
    now = "GETDATE()"
    rows = [
        f"(1, 10, 'new', 19.99, {now} - INTERVAL '2 hours', {now} - INTERVAL '3 hours',"
        " CURRENT_DATE, 'ann@example.com', JSON_PARSE('{\"a\": 1}'), false,"
        f" {now} - INTERVAL '2 hours', ST_GeomFromText('POINT(1 2)'))",
        f"(2, 11, 'shipped', 5.00, {now} - INTERVAL '3 hours', {now} - INTERVAL '4 hours',"
        " CURRENT_DATE - 1, 'bob@example.com', JSON_PARSE('{\"a\": 2}'), true,"
        f" {now} - INTERVAL '3 hours', ST_GeomFromText('POINT(3 4)'))",
        f"(3, NULL, 'shipped', 250.10, {now} - INTERVAL '5 hours', NULL, CURRENT_DATE - 2, NULL,"
        " NULL, NULL, NULL, NULL)",
        f"(4, 12, 'cancelled', -3.50, {now} - INTERVAL '30 hours', {now} - INTERVAL '31 hours',"
        " CURRENT_DATE - 3, 'cy@example.com', JSON_PARSE('[]'), false,"
        f" {now} - INTERVAL '30 hours', ST_GeomFromText('POINT(5 6)'))",
        f"(5, 12, 'bogus', 42.00, {now} - INTERVAL '24 hours', {now} - INTERVAL '25 hours',"
        " CURRENT_DATE - 1, 'dee@example.com', JSON_PARSE('{\"a\": 5}'), false,"
        f" {now} - INTERVAL '24 hours', ST_GeomFromText('POINT(1 2)'))",
        "(6, 13, NULL, 0.00, NULL, NULL, NULL, 'eve@example.com', JSON_PARSE('{\"a\": 6}'),"
        " true, NULL, NULL)",
    ]
    return [
        f"CREATE SCHEMA {schema}",
        f"CREATE TABLE {schema}.orders (order_id BIGINT NOT NULL, customerid BIGINT,"
        " status VARCHAR(16), amount DECIMAL(12, 2), placed_at TIMESTAMP, shipped_local TIMESTAMP,"
        " order_date DATE, email VARCHAR(64), payload SUPER, is_gift BOOLEAN,"
        " placed_tz TIMESTAMPTZ, shape GEOMETRY)",
        f"INSERT INTO {schema}.orders VALUES {', '.join(rows)}",
        f"CREATE TABLE {schema}.orders_lc (id BIGINT, label VARCHAR(4))",
        f"INSERT INTO {schema}.orders_lc VALUES (1, 'a'), (2, 'b'), (2, 'A')",
        f"CREATE VIEW {schema}.big_orders AS SELECT * FROM {schema}.orders WHERE amount > 10",
        # A late-binding view has no pg_attribute rows; its column types come from svv_columns.
        f"CREATE VIEW {schema}.lb_orders AS SELECT order_id, payload, is_gift, shape"
        f" FROM {schema}.orders WITH NO SCHEMA BINDING",
        f"CREATE MATERIALIZED VIEW {schema}.orders_by_status AS"
        f" SELECT status, COUNT(*) AS n FROM {schema}.orders GROUP BY status",
        f"GRANT USAGE ON SCHEMA {schema} TO {_READER}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {_READER}",
    ]


def _secret_value(secret_id: str) -> str:
    import boto3

    client = boto3.client("secretsmanager", region_name=_REGION)
    return str(client.get_secret_value(SecretId=secret_id)["SecretString"])


def _admin(statements: list[str]) -> None:
    admin = json.loads(_secret_value(_ADMIN_SECRET))
    config = _SPEC.validate_config(
        {"host": _HOST, "database": _DATABASE, "user": admin["username"], "sslmode": "verify-full"}
    )
    engine = _SPEC.create_engine(config, admin["password"], read_only=False)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def redshift() -> Iterator[RedshiftTarget]:
    schema = f"dq_rs_{uuid.uuid4().hex[:8]}"
    try:
        _admin(_seed(schema))
        config = {
            "host": _HOST,
            "database": _DATABASE,
            "user": _READER,
            "schema": schema,
            "sslmode": "verify-full",
        }
        yield RedshiftTarget(
            config, schema, FakeSecretStore({_SECRET_REF: _secret_value(_READER_SECRET)})
        )
    finally:
        _admin([f"DROP SCHEMA IF EXISTS {schema} CASCADE"])


def _secret(redshift: RedshiftTarget) -> str:
    return redshift.store.get(_SECRET_REF)


def _runner(redshift: RedshiftTarget, **overrides: Any) -> Any:
    return build_check_runner(
        conn_type="redshift",
        config={**redshift.config, **overrides},
        secret_ref=_SECRET_REF,
        secret_store=redshift.store,
    )


def _connection(redshift: RedshiftTarget) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="redshift",
        type="redshift",
        env="dev",
        config=redshift.config,
        secret_ref=_SECRET_REF,
    )


def _run(redshift: RedshiftTarget, table: str, checks: list[CheckSpec], **kwargs: Any) -> Any:
    runner = _runner(redshift)
    try:
        return runner.run_checks(table=table, schema=redshift.schema, checks=checks, **kwargs)
    finally:
        runner.close()


# ───────────────────────────── connection ─────────────────────────────


def test_a_real_session_as_the_least_privileged_reader(redshift: RedshiftTarget) -> None:
    get_connection_adapter("redshift").test(redshift.config, _secret(redshift))


def test_a_wrong_password_is_an_auth_failure(redshift: RedshiftTarget) -> None:
    with pytest.raises(Exception) as exc:
        get_connection_adapter("redshift").test(redshift.config, "not-the-password")
    assert is_auth_failure(exc.value)


def test_the_session_is_read_only_and_scoped_to_the_schema(redshift: RedshiftTarget) -> None:
    config = _SPEC.validate_config(redshift.config)
    engine = _SPEC.create_engine(config, _secret(redshift))
    try:
        with engine.connect() as conn:
            read_only = conn.execute(text("SHOW default_transaction_read_only")).scalar()
            path = conn.execute(text("SHOW search_path")).scalar()
            rows = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()
        with pytest.raises(Exception) as exc, engine.connect() as conn:
            conn.execute(text("CREATE TEMP TABLE written (x INT)"))
    finally:
        engine.dispose()
    assert (read_only, path, rows) == ("on", f"pg_catalog,{redshift.schema},public", 6)
    assert "read-only" in str(exc.value)


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
    redshift: RedshiftTarget,
) -> None:
    checks = [CheckSpec(expectation_type, kwargs) for expectation_type, kwargs, _ in _CASES]
    outcome = _run(
        redshift,
        "orders",
        checks,
        index_columns=["order_id"],
        value_signal_gate=lambda column: True,
    )
    got = {
        spec.expectation_type: (result.errored, result.error_message, result.success)
        for spec, result in zip(checks, outcome.checks, strict=True)
    }
    assert got == {
        expectation_type: (False, None, verdict) for expectation_type, _, verdict in _CASES
    }


def test_uniqueness_reports_its_rows(redshift: RedshiftTarget) -> None:
    [result] = _run(
        redshift,
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


def test_uniqueness_is_case_sensitive(redshift: RedshiftTarget) -> None:
    [result] = _run(
        redshift,
        "orders_lc",
        [CheckSpec("expect_column_values_to_be_unique", {"column": "label"})],
    ).checks
    assert not result.errored, result.error_message
    assert result.success is True


def test_checks_over_super_and_geometry_columns(redshift: RedshiftTarget) -> None:
    checks = [
        CheckSpec("expect_column_values_to_not_be_null", {"column": "payload"}),
        CheckSpec("expect_column_values_to_not_be_null", {"column": "shape"}),
        CheckSpec(
            CUSTOM_SQL_EXPECTATION_TYPE,
            {"unexpected_rows_query": "SELECT * FROM {batch} WHERE payload.a::INT > 4"},
        ),
    ]
    outcome = _run(redshift, "orders", checks, index_columns=["order_id"])
    assert [(r.errored, r.error_message, r.success) for r in outcome.checks] == [
        (False, None, False),
        (False, None, False),
        (False, None, False),
    ]


def test_a_mixed_case_target_is_refused_when_the_suite_is_saved() -> None:
    """Redshift folds names to lower case; `Orders` would never join its asset."""
    with pytest.raises(TargetShapeError, match="lower case"):
        registry.resolve_target_shape("redshift", {"table": "Orders"})


def test_a_missing_table_is_a_config_error(redshift: RedshiftTarget) -> None:
    with pytest.raises(Exception) as exc:
        _run(
            redshift, "nope", [CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0})]
        )
    assert classify_failure_category(exc.value) is FailureCategory.CONFIG


# ───────────────────────────── monitors ─────────────────────────────


def test_freshness_over_timestamp_timestamptz_and_date_and_volume(
    redshift: RedshiftTarget,
) -> None:
    monitors = [
        MonitorSpec("freshness", {"column": "placed_at"}),
        MonitorSpec("freshness", {"column": "placed_tz"}),
        MonitorSpec("freshness", {"column": "order_date"}),
        MonitorSpec("freshness", {"column": "status"}),
        MonitorSpec("volume", {"min_rows": 1, "max_rows": 5}),
    ]
    runner = _runner(redshift)
    try:
        placed, placed_tz, dated, not_a_time, volume = runner.run_monitors(
            table="orders", schema=redshift.schema, monitors=monitors
        )
    finally:
        runner.close()
    # GETDATE() is UTC; a TIMESTAMP comes back naive in it.
    assert placed.metric_value is not None and 1.9 < placed.metric_value < 2.5
    assert placed_tz.metric_value is not None and 1.9 < placed_tz.metric_value < 2.5
    assert dated.metric_value is not None and 0 <= dated.metric_value <= 48
    assert not_a_time.errored
    assert volume.observed_value == {"row_count": 6, "deviation_pct": 20.0}


def test_the_anomaly_monitor_measures_row_count_and_freshness_age(
    redshift: RedshiftTarget,
) -> None:
    from backend.app.datasources.monitors import anomaly_params
    from backend.app.services.anomaly import measure_metric

    connection, now = _connection(redshift), datetime.now(UTC)
    rows = measure_metric(
        connection,
        table="orders",
        schema=redshift.schema,
        catalog=None,
        params=anomaly_params({"target_metric": "row_count"}),
        secret_store=redshift.store,
        now=now,
    )
    age = measure_metric(
        connection,
        table="orders",
        schema=None,
        catalog=None,
        params=anomaly_params({"target_metric": "freshness_age_hours", "column": "placed_at"}),
        secret_store=redshift.store,
        now=now,
    )
    assert rows == 6.0
    assert age is not None and 1.9 < age < 2.5


# ───────────────────────────── introspection ─────────────────────────────


@pytest.mark.parametrize("table", ["orders", "lb_orders"])
def test_the_profile_skips_what_redshift_cannot_aggregate(
    redshift: RedshiftTarget, table: str
) -> None:
    """MIN(boolean), MIN(geometry) and COUNT(DISTINCT geometry) do not exist on Redshift, and
    MIN(super) answers NULL; a late-binding view's types come from svv_columns."""
    profile = profile_service.profile_table(
        _connection(redshift),
        table=table,
        schema=redshift.schema,
        columns=["order_id", "payload", "is_gift", "shape"],
        top_n=2,
        secret_store=redshift.store,
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 6
    assert (by_column["order_id"].min_value, by_column["order_id"].max_value) == (1, 6)
    assert by_column["payload"].null_count == 1
    assert by_column["shape"].null_count == 2
    for column in ("payload", "is_gift", "shape"):
        assert (by_column[column].min_value, by_column[column].max_value) == (None, None)


def test_columns_schema_drift_and_comparison_reads(redshift: RedshiftTarget) -> None:
    connection = _connection(redshift)
    columns = profile_service.list_table_columns(
        connection, table="orders", schema=None, secret_store=redshift.store
    )
    assert columns[:3] == ["order_id", "customerid", "status"]
    snapshot = schema_drift.introspect_columns(
        connection,
        table="orders",
        schema=redshift.schema,
        catalog=None,
        secret_store=redshift.store,
    )
    assert {"name": "amount", "type": "numeric"} in snapshot
    late_bound = schema_drift.introspect_columns(
        connection,
        table="lb_orders",
        schema=redshift.schema,
        catalog=None,
        secret_store=redshift.store,
    )
    assert [column["name"] for column in late_bound] == ["order_id", "payload", "is_gift", "shape"]
    frame = read_dataset(
        connection,
        DatasetSpec(table="orders", schema=redshift.schema),
        max_rows=10,
        secret_store=redshift.store,
    )
    assert len(frame) == 6


_TABLES = [
    ("big_orders", "view"),
    ("lb_orders", "view"),
    ("orders", "table"),
    ("orders_by_status", "view"),
    ("orders_lc", "table"),
]


def test_enumeration_joins_the_suite_target_identity(redshift: RedshiftTarget) -> None:
    """A materialized view lists as a view, and its internal `mv_tbl__` table not at all."""
    with profile_service._open_connection(_connection(redshift), redshift.store) as conn:
        schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
        tables = generic_sql.typed_table_rows(_SPEC, conn, schema=redshift.schema, limit=None)
        identities = get_table_enumerator("redshift").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=redshift.config
        )
    assert redshift.schema in schemas
    assert not {"information_schema", "pg_catalog", "catalog_history"} & set(schemas)
    assert sorted((t, kind) for _, t, kind in tables) == _TABLES
    target = resolve_asset_identity("redshift", redshift.config, {"table": "orders"})
    assert target.name == f"{_DATABASE}.{redshift.schema}.orders"
    assert (target.namespace, target.name) in {(i.namespace, i.name) for i in identities}


def _stored_connection(db_session: Any, redshift: RedshiftTarget) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"rs-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"rs-{uuid.uuid4().hex[:6]}",
        type="redshift",
        env="dev",
        config=redshift.config,
        secret_ref=_SECRET_REF,
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def test_browse_walks_schemas_then_tables(db_session: Any, redshift: RedshiftTarget) -> None:
    connection = _stored_connection(db_session, redshift)
    kwargs: dict[str, Any] = {"session": db_session, "limit": 500, "secret_store": redshift.store}
    top = browse_service.browse_catalog(connection, catalog=None, schema=None, **kwargs)
    tables = browse_service.browse_catalog(
        connection, catalog=None, schema=redshift.schema, **kwargs
    )
    assert top.level == "schema" and redshift.schema in [e.name for e in top.entries]
    assert [(e.name, e.object_type) for e in tables.entries] == _TABLES


# ───────────────────────────── end to end ─────────────────────────────


def test_a_suite_run_persists_results_end_to_end(db_session: Any, redshift: RedshiftTarget) -> None:
    connection = _stored_connection(db_session, redshift)
    suite = Suite(name="redshift", connection_id=connection.id, created_by=connection.created_by)
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

    runner = _runner(redshift)
    try:
        run_service.execute_run(
            db_session,
            run=run,
            checks=checks,
            runner=runner,
            table="orders",
            schema=redshift.schema,
        )
    finally:
        runner.close()

    assert run.status == "succeeded"
    by_check = {
        r.check_id: r for r in db_session.scalars(select(Result).where(Result.run_id == run.id))
    }
    assert [by_check[c.id].status for c in checks] == ["fail", "fail", "pass", "pass"]
    assert by_check[checks[2].id].observed_value == {"row_count": 6, "deviation_pct": 0.0}


# ───────────────────────── aggregate monitor (#1602) ─────────────────────────


@pytest.fixture
def redshift_amounts(redshift: RedshiftTarget) -> Iterator[None]:
    """`AMOUNTS` in a DECIMAL column, an all-NULL column and an empty table in the module's
    schema, readable by the reader and dropped afterwards."""
    from backend.tests.support.aggregate_lane import AMOUNTS

    table, empty = f"{redshift.schema}.amounts", f"{redshift.schema}.amounts_empty"
    rows = ", ".join(f"({'NULL' if v is None else v}, NULL)" for v in AMOUNTS)
    try:
        _admin(
            [
                f"CREATE TABLE {table} (amount DECIMAL(9, 2), blank DOUBLE PRECISION)",
                f"INSERT INTO {table} VALUES {rows}",
                f"CREATE TABLE {empty} (amount DECIMAL(9, 2))",
                f"GRANT SELECT ON {table}, {empty} TO {_READER}",
            ]
        )
        yield
    finally:
        _admin([f"DROP TABLE IF EXISTS {table}", f"DROP TABLE IF EXISTS {empty}"])


def test_every_aggregate_is_exact_over_psycopg2_on_redshift(
    redshift: RedshiftTarget, redshift_amounts: None
) -> None:
    """Redshift keeps AVG and MEDIAN of a DECIMAL at the column's scale; the double cast is what
    makes them exact."""
    from backend.tests.support.aggregate_lane import assert_aggregates

    runner = _runner(redshift)
    try:
        assert_aggregates(
            runner,
            table="amounts",
            schema=redshift.schema,
            column="amount",
            null_column="blank",
            empty_table="amounts_empty",
        )
    finally:
        runner.close()


def test_the_aggregate_monitor_runs_and_previews_end_to_end(
    db_session: Any, redshift: RedshiftTarget, redshift_amounts: None
) -> None:
    from backend.tests.support.aggregate_lane import assert_run_path_and_dry_run

    assert_run_path_and_dry_run(
        db_session,
        conn_type="redshift",
        config=redshift.config,
        secret_ref=_SECRET_REF,
        secret_store=redshift.store,
        target={"table": "amounts", "schema": redshift.schema},
        column="amount",
    )
