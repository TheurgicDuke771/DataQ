"""The Trino datasource (#1685), executed for real against live Trino clusters.

Opt-in, nothing mocked — every value crosses the real ``trino`` client boundary (#953):

* ``TRINO_TEST_URL`` — an open cluster (no authentication) with a writable ``memory`` catalog,
  e.g. ``http://127.0.0.1:8080`` for a stock ``trinodb/trino`` container. The fixture seeds a
  schema there as ``admin`` and DataQ reads it with ``auth_type: none``.
* ``TRINO_TEST_PG_CATALOG`` + ``TRINO_TEST_PG_ADMIN_URL`` (optional) — a ``postgresql``
  connector catalog on that cluster and an admin URL for the database behind it, to read a
  MIXED-CASE PostgreSQL table and a ``timestamptz`` through Trino.
* ``TRINO_TLS_TEST_URL`` + ``TRINO_TLS_TEST_CA`` + ``TRINO_TLS_TEST_SECRETS_DIR`` (optional) — a
  cluster serving HTTPS with a certificate from a private CA (the CA's PEM at ``…_CA``), the
  PASSWORD and JWT authenticators, and file-based access control that lets ``dq_*`` users only
  read ``memory.sales``. The secrets dir holds ``reader.pw`` (user ``dq_reader``), ``admin.pw``
  and ``jwt.key`` (the RSA key the cluster's ``jwt.key-file`` verifies). Nothing in it is ever
  printed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

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

_URL = os.environ.get("TRINO_TEST_URL", "").strip()
pytestmark = pytest.mark.skipif(not _URL, reason="requires TRINO_TEST_URL (a live Trino)")

_PG_CATALOG = os.environ.get("TRINO_TEST_PG_CATALOG", "").strip()
_PG_ADMIN_URL = os.environ.get("TRINO_TEST_PG_ADMIN_URL", "").strip()
_TLS_URL = os.environ.get("TRINO_TLS_TEST_URL", "").strip()
_TLS_CA = os.environ.get("TRINO_TLS_TEST_CA", "").strip()
_TLS_SECRETS = os.environ.get("TRINO_TLS_TEST_SECRETS_DIR", "").strip()

_SPEC = SQL_ENGINES["trino"]


def _host_port(url: str) -> tuple[str, int]:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    assert parts.hostname and parts.port, f"{url!r} needs an explicit host and port"
    return parts.hostname, parts.port


@dataclass(frozen=True)
class TrinoTarget:
    config: dict[str, Any]
    schema: str


def _seed(schema: str) -> list[str]:
    now_utc = "CAST(current_timestamp AT TIME ZONE 'UTC' AS timestamp(6))"
    return [
        f"CREATE SCHEMA memory.{schema}",
        f"CREATE TABLE memory.{schema}.orders (order_id bigint, customerid bigint,"
        " status varchar, amount decimal(12, 2), placed_at timestamp(6) with time zone,"
        " shipped_local timestamp(6), order_date date, email varchar, payload json,"
        " is_gift boolean)",
        f"INSERT INTO memory.{schema}.orders VALUES"
        f" (1, 10, 'new', 19.99, current_timestamp - INTERVAL '2' HOUR,"
        f" {now_utc} - INTERVAL '3' HOUR, current_date, 'ann@example.com', JSON '{{\"a\": 1}}',"
        " false),"
        f" (2, 11, 'shipped', 5.00, current_timestamp - INTERVAL '3' HOUR,"
        f" {now_utc} - INTERVAL '4' HOUR, current_date - INTERVAL '1' DAY, 'bob@example.com',"
        " JSON '{\"a\": 2}', true),"
        " (3, NULL, 'shipped', 250.10, current_timestamp - INTERVAL '5' HOUR, NULL,"
        " current_date - INTERVAL '2' DAY, NULL, NULL, NULL),"
        f" (4, 12, 'cancelled', -3.50, current_timestamp - INTERVAL '30' HOUR,"
        f" {now_utc} - INTERVAL '31' HOUR, current_date - INTERVAL '3' DAY, 'cy@example.com',"
        " JSON '[]', false),"
        f" (5, 12, 'bogus', 42.00, current_timestamp - INTERVAL '24' HOUR,"
        f" {now_utc} - INTERVAL '25' HOUR, current_date - INTERVAL '1' DAY, 'dee@example.com',"
        " JSON '{\"a\": 5}', false),"
        " (6, 13, NULL, 0.00, NULL, NULL, NULL, 'eve@example.com', JSON '{\"a\": 6}', true)",
        f"CREATE TABLE memory.{schema}.orders_lc (id bigint, label varchar)",
        f"INSERT INTO memory.{schema}.orders_lc VALUES (1, 'a'), (2, 'b'), (2, 'A')",
        f"CREATE VIEW memory.{schema}.big_orders AS"
        f" SELECT * FROM memory.{schema}.orders WHERE amount > 10",
    ]


def _admin_engine(url: str, **connect_args: Any) -> Any:
    host, port = _host_port(url)
    return create_engine(f"trino://admin@{host}:{port}/memory", connect_args=connect_args)


@pytest.fixture(scope="module")
def trino() -> Iterator[TrinoTarget]:
    host, port = _host_port(_URL)
    schema = f"dq_trino_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine(_URL)
    try:
        with admin.connect() as conn:
            for statement in _seed(schema):
                conn.execute(text(statement))
        config = {
            "host": host,
            "port": port,
            "catalog": "memory",
            "schema": schema,
            "user": "dq_reader",
            "auth_type": "none",
            "sslmode": "disable",
        }
        yield TrinoTarget(config, schema)
    finally:
        with admin.connect() as conn:
            for table in ("big_orders",):
                conn.execute(text(f"DROP VIEW IF EXISTS memory.{schema}.{table}"))
            for table in ("orders", "orders_lc"):
                conn.execute(text(f"DROP TABLE IF EXISTS memory.{schema}.{table}"))
            conn.execute(text(f"DROP SCHEMA IF EXISTS memory.{schema}"))
        admin.dispose()


_STORE = FakeSecretStore({})


def _runner(trino: TrinoTarget, **overrides: Any) -> Any:
    return build_check_runner(
        conn_type="trino",
        config={**trino.config, **overrides},
        secret_ref=None,
        secret_store=_STORE,
    )


def _connection(trino: TrinoTarget) -> Connection:
    return Connection(
        id=uuid.uuid4(), name="trino", type="trino", env="dev", config=trino.config, secret_ref=None
    )


def _run(trino: TrinoTarget, table: str, checks: list[CheckSpec], **kwargs: Any) -> Any:
    runner = _runner(trino)
    try:
        return runner.run_checks(table=table, schema=trino.schema, checks=checks, **kwargs)
    finally:
        runner.close()


# ───────────────────────────── connection ─────────────────────────────


def test_a_real_session_on_an_open_cluster(trino: TrinoTarget) -> None:
    get_connection_adapter("trino").test(trino.config, None)


def test_the_session_runs_in_utc_and_in_the_pinned_catalog_and_schema(trino: TrinoTarget) -> None:
    config = _SPEC.validate_config(trino.config)
    url, connect_args = _SPEC.engine_args(config, None)
    engine = create_engine(url, connect_args=connect_args)
    try:
        with engine.connect() as conn:
            zone, catalog, schema = conn.execute(
                text("SELECT current_timezone(), current_catalog, current_schema")
            ).one()
    finally:
        engine.dispose()
    # The client otherwise sends the WORKER's local zone, and a naive `timestamp` would come
    # back in it while the freshness math reads naive as UTC.
    assert (zone, catalog, schema) == ("UTC", "memory", trino.schema)


def test_a_missing_catalog_is_a_config_error(trino: TrinoTarget) -> None:
    with pytest.raises(Exception) as exc:
        _run(
            TrinoTarget({**trino.config, "catalog": "nope"}, trino.schema),
            "orders",
            [CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0})],
        )
    assert classify_failure_category(exc.value) is FailureCategory.CONFIG


def test_an_unreachable_cluster_is_a_connectivity_failure(trino: TrinoTarget) -> None:
    with pytest.raises(Exception) as exc:
        get_connection_adapter("trino").test({**trino.config, "port": 1}, None)
    assert classify_failure_category(exc.value) is FailureCategory.CONNECTIVITY
    assert not is_auth_failure(exc.value)


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
    ("expect_column_values_to_be_of_type", {"column": "amount", "type_": "DECIMAL(12, 2)"}, True),
    (
        "expect_column_values_to_be_in_type_list",
        {"column": "shipped_local", "type_list": ["TIMESTAMP(6)"]},
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
    trino: TrinoTarget,
) -> None:
    checks = [CheckSpec(expectation_type, kwargs) for expectation_type, kwargs, _ in _CASES]
    outcome = _run(
        trino, "orders", checks, index_columns=["order_id"], value_signal_gate=lambda column: True
    )
    got = {
        spec.expectation_type: (result.errored, result.error_message, result.success)
        for spec, result in zip(checks, outcome.checks, strict=True)
    }
    assert got == {
        expectation_type: (False, None, verdict) for expectation_type, _, verdict in _CASES
    }


def test_uniqueness_reports_its_rows_without_temporary_tables(trino: TrinoTarget) -> None:
    """GX turns temporary tables off for Trino; the check runs as one aggregate query."""
    [result] = _run(
        trino,
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


def test_uniqueness_is_case_sensitive(trino: TrinoTarget) -> None:
    """Trino compares varchar byte-wise: 'a' and 'A' are different values (unlike MySQL)."""
    [result] = _run(
        trino, "orders_lc", [CheckSpec("expect_column_values_to_be_unique", {"column": "label"})]
    ).checks
    assert not result.errored, result.error_message
    assert result.success is True


def test_a_mixed_case_column_in_a_check_resolves_to_the_folded_column(trino: TrinoTarget) -> None:
    [result] = _run(
        trino,
        "orders",
        [CheckSpec("expect_column_values_to_not_be_null", {"column": "CustomerId"})],
    ).checks
    assert not result.errored, result.error_message
    assert result.success is False  # row 3 has no customer


def test_a_mixed_case_target_is_refused_when_the_suite_is_saved() -> None:
    """Trino reports every name lower case, so `Orders` would never join its enumerated asset."""
    with pytest.raises(TargetShapeError, match="lower case"):
        registry.resolve_target_shape("trino", {"table": "Orders"})
    with pytest.raises(TargetShapeError, match="lower case"):
        registry.resolve_target_shape("trino", {"table": "orders", "schema": "Sales"})


def test_a_missing_table_is_a_config_error(trino: TrinoTarget) -> None:
    with pytest.raises(Exception) as exc:
        _run(trino, "nope", [CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0})])
    assert classify_failure_category(exc.value) is FailureCategory.CONFIG


# ───────────────────────────── monitors ─────────────────────────────


def test_freshness_over_timestamptz_timestamp_and_date_and_volume(trino: TrinoTarget) -> None:
    monitors = [
        MonitorSpec("freshness", {"column": "placed_at"}),
        MonitorSpec("freshness", {"column": "shipped_local"}),
        MonitorSpec("freshness", {"column": "order_date"}),
        MonitorSpec("freshness", {"column": "status"}),
        MonitorSpec("volume", {"min_rows": 1, "max_rows": 5}),
    ]
    runner = _runner(trino)
    try:
        placed, shipped, dated, not_a_time, volume = runner.run_monitors(
            table="orders", schema=trino.schema, monitors=monitors
        )
        [unqualified] = runner.run_monitors(
            table="orders",
            schema=None,
            monitors=[MonitorSpec("volume", {"min_rows": 0, "max_rows": 9})],
        )
    finally:
        runner.close()
    # timestamp WITH time zone comes back tz-aware; the plain `timestamp` naive, in the session's
    # UTC — both land on the seeded ages.
    assert placed.metric_value is not None and 1.9 < placed.metric_value < 2.5
    assert shipped.metric_value is not None and 2.9 < shipped.metric_value < 3.5
    assert dated.metric_value is not None and 0 <= dated.metric_value <= 48
    assert not_a_time.errored
    assert volume.observed_value == {"row_count": 6, "deviation_pct": 20.0}
    assert unqualified.observed_value == {"row_count": 6, "deviation_pct": 0.0}


def test_the_anomaly_monitor_measures_row_count_and_freshness_age(trino: TrinoTarget) -> None:
    from backend.app.datasources.monitors import anomaly_params
    from backend.app.services.anomaly import measure_metric

    connection, now = _connection(trino), datetime.now(UTC)
    rows = measure_metric(
        connection,
        table="orders",
        schema=trino.schema,
        catalog=None,
        params=anomaly_params({"target_metric": "row_count"}),
        secret_store=_STORE,
        now=now,
    )
    age = measure_metric(
        connection,
        table="orders",
        schema=None,
        catalog=None,
        params=anomaly_params({"target_metric": "freshness_age_hours", "column": "placed_at"}),
        secret_store=_STORE,
        now=now,
    )
    assert rows == 6.0
    assert 1.9 < age < 2.5


# ───────────────────────────── introspection ─────────────────────────────


def test_the_profile_survives_json_and_reports_booleans(trino: TrinoTarget) -> None:
    profile = profile_service.profile_table(
        _connection(trino),
        table="orders",
        schema=trino.schema,
        columns=["amount", "payload", "is_gift", "placed_at"],
        top_n=2,
        secret_store=_STORE,
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 6
    assert (by_column["amount"].min_value, by_column["amount"].max_value) == (-3.5, 250.1)
    # json has no MIN/MAX on Trino: unavailable, not a failed profile.
    assert by_column["payload"].min_value is None and by_column["payload"].null_count == 1
    assert (by_column["is_gift"].min_value, by_column["is_gift"].max_value) == (False, True)
    assert by_column["placed_at"].min_value is not None


def test_the_profile_folds_names_the_caller_typed_in_mixed_case(trino: TrinoTarget) -> None:
    """The profile API is not held to the save-time lower-case rule; Trino folds `ORDERS` and
    `Payload` itself, so the json column must still be recognised as unorderable."""
    profile = profile_service.profile_table(
        _connection(trino),
        table="ORDERS",
        schema=trino.schema.upper(),
        columns=["Payload", "Amount"],
        top_n=2,
        secret_store=_STORE,
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 6
    assert by_column["Payload"].min_value is None and by_column["Payload"].top_values == []
    assert by_column["Amount"].max_value == 250.1


def test_columns_schema_drift_and_comparison_reads(trino: TrinoTarget) -> None:
    connection = _connection(trino)
    columns = profile_service.list_table_columns(
        connection, table="orders", schema=None, secret_store=_STORE
    )
    assert columns[:3] == ["order_id", "customerid", "status"]
    snapshot = schema_drift.introspect_columns(
        connection, table="orders", schema=trino.schema, catalog=None, secret_store=_STORE
    )
    assert {"name": "placed_at", "type": "timestamp(6) with time zone"} in snapshot
    assert {"name": "payload", "type": "json"} in snapshot
    frame = read_dataset(
        connection,
        DatasetSpec(table="orders", schema=trino.schema),
        max_rows=10,
        secret_store=_STORE,
    )
    assert len(frame) == 6


def test_enumeration_joins_the_suite_target_identity(trino: TrinoTarget) -> None:
    with profile_service._open_connection(_connection(trino), _STORE) as conn:
        schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
        tables = generic_sql.table_rows(_SPEC, conn, schema=trino.schema, limit=None)
        identities = get_table_enumerator("trino").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=trino.config
        )
    assert trino.schema in schemas and "information_schema" not in schemas
    assert sorted(t for _, t in tables) == ["big_orders", "orders", "orders_lc"]
    target = resolve_asset_identity("trino", trino.config, {"table": "orders"})
    host, port = _host_port(_URL)
    assert target.namespace == f"trino://{host}:{port}"
    assert target.name == f"memory.{trino.schema}.orders"
    assert (target.namespace, target.name) in {(i.namespace, i.name) for i in identities}


def test_browse_walks_schemas_then_tables(db_session: Any, trino: TrinoTarget) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"tr-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"tr-{uuid.uuid4().hex[:6]}",
        type="trino",
        env="dev",
        config=trino.config,
        secret_ref=None,
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    kwargs: dict[str, Any] = {"session": db_session, "limit": 50, "secret_store": _STORE}
    top = browse_service.browse_catalog(connection, catalog=None, schema=None, **kwargs)
    tables = browse_service.browse_catalog(connection, catalog=None, schema=trino.schema, **kwargs)
    assert top.level == "schema" and trino.schema in [e.name for e in top.entries]
    assert [e.name for e in tables.entries] == ["big_orders", "orders", "orders_lc"]


# ───────────────────────────── end to end ─────────────────────────────


def test_a_suite_run_persists_results_end_to_end(db_session: Any, trino: TrinoTarget) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"tr-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"tr-{uuid.uuid4().hex[:6]}",
        type="trino",
        env="dev",
        config=trino.config,
        secret_ref=None,
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(name="trino", connection_id=connection.id, created_by=owner.id)
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

    runner = _runner(trino)
    try:
        run_service.execute_run(
            db_session, run=run, checks=checks, runner=runner, table="orders", schema=trino.schema
        )
    finally:
        runner.close()

    assert run.status == "succeeded"
    by_check = {
        r.check_id: r for r in db_session.scalars(select(Result).where(Result.run_id == run.id))
    }
    assert [by_check[c.id].status for c in checks] == ["fail", "fail", "pass", "pass"]
    assert by_check[checks[2].id].observed_value == {"row_count": 6, "deviation_pct": 0.0}


# ───────────────────────────── a PostgreSQL catalog behind Trino ─────────────────────────────

_needs_pg = pytest.mark.skipif(
    not (_PG_CATALOG and _PG_ADMIN_URL),
    reason="requires TRINO_TEST_PG_CATALOG + TRINO_TEST_PG_ADMIN_URL",
)


@pytest.fixture(scope="module")
def pg_behind_trino(trino: TrinoTarget) -> Iterator[TrinoTarget]:
    schema = f"DqTr_{uuid.uuid4().hex[:8]}"
    admin = create_engine(_PG_ADMIN_URL)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(
            text(
                f'CREATE TABLE "{schema}"."Orders" (order_id int, "CustomerId" int,'
                " placed_at timestamptz, shipped_local timestamp)"
            )
        )
        conn.execute(
            text(
                f'INSERT INTO "{schema}"."Orders" VALUES'
                " (1, 10, now() - interval '2 hours',"
                " (now() AT TIME ZONE 'UTC') - interval '3 hours'), (2, NULL, NULL, NULL)"
            )
        )
    try:
        yield TrinoTarget(
            {**trino.config, "catalog": _PG_CATALOG, "schema": schema.lower()}, schema
        )
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@_needs_pg
def test_a_mixed_case_postgresql_table_reads_through_trino_lower_cased(
    pg_behind_trino: TrinoTarget,
) -> None:
    """The store underneath is mixed case; Trino (with case-insensitive name matching on the
    catalog) presents it lower case, which is the only spelling DataQ accepts."""
    target = pg_behind_trino
    schema = target.config["schema"]
    runner = _runner(target)
    try:
        [not_null] = runner.run_checks(
            table="orders",
            schema=schema,
            checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "customerid"})],
        ).checks
        placed, shipped = runner.run_monitors(
            table="orders",
            schema=schema,
            monitors=[
                MonitorSpec("freshness", {"column": "placed_at"}),
                MonitorSpec("freshness", {"column": "shipped_local"}),
            ],
        )
    finally:
        runner.close()
    assert not not_null.errored and not_null.success is False
    assert placed.metric_value is not None and 1.9 < placed.metric_value < 2.5
    assert shipped.metric_value is not None and 2.9 < shipped.metric_value < 3.5


# ───────────────────────────── TLS + authentication ─────────────────────────────

_needs_tls = pytest.mark.skipif(
    not (_TLS_URL and _TLS_CA and _TLS_SECRETS),
    reason="requires TRINO_TLS_TEST_URL + TRINO_TLS_TEST_CA + TRINO_TLS_TEST_SECRETS_DIR",
)


@dataclass(frozen=True)
class TlsTarget:
    config: dict[str, Any]
    password: str = field(repr=False)
    jwt_key: str = field(repr=False)


def _secret(name: str) -> str:
    return (Path(_TLS_SECRETS) / name).read_text().strip()


@pytest.fixture(scope="module")
def tls() -> Iterator[TlsTarget]:
    host, port = _host_port(_TLS_URL)
    from trino.auth import BasicAuthentication

    admin = create_engine(
        f"trino://admin@{host}:{port}/memory",
        connect_args={
            "http_scheme": "https",
            "verify": _TLS_CA,
            "auth": BasicAuthentication("admin", _secret("admin.pw")),
        },
    )
    with admin.connect() as conn:
        # The memory connector cannot DELETE, so the table is recreated rather than emptied.
        for statement in (
            "CREATE SCHEMA IF NOT EXISTS memory.sales",
            "CREATE SCHEMA IF NOT EXISTS memory.hidden",
            "DROP TABLE IF EXISTS memory.sales.orders",
            "CREATE TABLE memory.sales.orders (id bigint, placed_at timestamp(3))",
            "INSERT INTO memory.sales.orders VALUES (1, localtimestamp), (2, NULL)",
            "CREATE TABLE IF NOT EXISTS memory.hidden.secret_stuff (x bigint)",
        ):
            conn.execute(text(statement))
    config = {
        "host": host,
        "port": port,
        "catalog": "memory",
        "schema": "sales",
        "user": "dq_reader",
        "auth_type": "password",
        "ca_bundle": Path(_TLS_CA).read_text(),
    }
    try:
        yield TlsTarget(config, _secret("reader.pw"), _secret("jwt.key"))
    finally:
        admin.dispose()


def _jwt(key: str, *, subject: str, expires: datetime) -> str:
    import jwt

    return jwt.encode({"sub": subject, "exp": int(expires.timestamp())}, key, algorithm="RS256")


@_needs_tls
def test_password_auth_over_tls_with_the_private_ca(tls: TlsTarget) -> None:
    adapter = get_connection_adapter("trino")
    adapter.test(tls.config, tls.password)
    with pytest.raises(Exception) as exc:
        adapter.test(tls.config, tls.password + "x")
    assert is_auth_failure(exc.value)
    assert classify_failure_category(exc.value) is FailureCategory.PERMISSION


@_needs_tls
def test_without_the_private_ca_the_certificate_is_refused(tls: TlsTarget) -> None:
    config = {k: v for k, v in tls.config.items() if k != "ca_bundle"}  # the system trust store
    with pytest.raises(Exception, match="CERTIFICATE_VERIFY_FAILED") as exc:
        get_connection_adapter("trino").test(config, tls.password)
    assert classify_failure_category(exc.value) is FailureCategory.CONNECTIVITY
    assert not is_auth_failure(exc.value)


@_needs_tls
def test_a_credential_over_plain_http_is_refused_before_anything_is_sent(tls: TlsTarget) -> None:
    with pytest.raises(ValidationError, match="needs TLS"):
        get_connection_adapter("trino").test(
            {**tls.config, "sslmode": "disable", "ca_bundle": None}, tls.password
        )


@_needs_tls
def test_jwt_auth_and_its_expiry(tls: TlsTarget) -> None:
    adapter = get_connection_adapter("trino")
    config = {**tls.config, "auth_type": "jwt", "user": "dq_jwt"}
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
    token = _jwt(tls.jwt_key, subject="dq_jwt", expires=expires)
    adapter.test(config, token)
    assert registry.credential_expiry("trino", config, token) == expires
    stale = _jwt(tls.jwt_key, subject="dq_jwt", expires=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(Exception, match="JWT expired") as exc:
        adapter.test(config, stale)
    assert is_auth_failure(exc.value)
    assert classify_failure_category(exc.value) is FailureCategory.PERMISSION


@_needs_tls
def test_checks_and_monitors_run_over_tls_with_auth(tls: TlsTarget) -> None:
    runner: Any = build_check_runner(
        conn_type="trino",
        config=tls.config,
        secret_ref="tls-ref",
        secret_store=FakeSecretStore({"tls-ref": tls.password}),
    )
    try:
        [not_null] = runner.run_checks(
            table="orders",
            schema=None,
            checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "placed_at"})],
        ).checks
        [volume] = runner.run_monitors(
            table="orders",
            schema=None,
            monitors=[MonitorSpec("volume", {"min_rows": 1, "max_rows": 10})],
        )
    finally:
        runner.close()
    assert not not_null.errored and not_null.success is False
    assert volume.observed_value is not None and volume.observed_value["row_count"] == 2


@_needs_tls
def test_the_clusters_access_control_is_the_read_only_guarantee(tls: TlsTarget) -> None:
    """Trino has no read-only session: a write is refused only because the cluster's access
    control grants this user SELECT alone — and listings show only what it may read."""
    config = _SPEC.validate_config(tls.config)
    url, connect_args = _SPEC.engine_args(config, tls.password)
    engine = create_engine(url, connect_args=connect_args)
    try:
        with engine.connect() as conn:
            with pytest.raises(DBAPIError, match="Access Denied") as exc:
                conn.execute(text("INSERT INTO memory.sales.orders VALUES (9, NULL)"))
            schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
    finally:
        engine.dispose()
    assert classify_failure_category(exc.value) is FailureCategory.PERMISSION
    assert not is_auth_failure(exc.value)
    assert "sales" in schemas and "hidden" not in schemas
