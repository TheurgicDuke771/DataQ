"""The PostgreSQL datasource (#1678), executed for real against the test Postgres.

Dogfooding: the adapter reads the same server the test suite already needs, so every value here
crosses the real psycopg2 driver boundary (#953) rather than arriving hand-built in our model's
shape. The fixture creates a mixed-case schema/table and a least-privileged LOGIN role, and every
DataQ path runs AS that role — never as the superuser the test engine connects with, which would
bypass the privilege filtering this adapter relies on.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import create_engine, make_url, select, text
from sqlalchemy.exc import DBAPIError

from backend.app.datasources import generic_sql
from backend.app.datasources.base import CheckSpec, MonitorSpec
from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
)
from backend.app.datasources.registry import build_check_runner, get_connection_adapter
from backend.app.datasources.sql_engines import SQL_ENGINES
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.lineage.warehouse import get_table_enumerator
from backend.app.services import (
    browse_service,
    profile_service,
    run_service,
    schema_drift,
)
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE
from backend.app.services.dataset_reader import DatasetSpec, read_dataset
from backend.app.services.failure_classifier import (
    FailureCategory,
    classify_failure_category,
    is_auth_failure,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")

_SPEC = SQL_ENGINES["postgres"]


@dataclass(frozen=True)
class PgTarget:
    config: dict[str, Any]
    password: str = field(repr=False)  # never in a failure report
    schema: str
    hidden_schema: str
    role: str


def _seed_sql(schema: str, hidden: str) -> list[str]:
    return [
        f'CREATE SCHEMA "{schema}"',
        f'CREATE TABLE "{schema}"."Orders" ('
        ' order_id integer PRIMARY KEY, "CustomerId" integer, status text, amount numeric(12, 2),'
        " placed_at timestamptz, shipped_local timestamp, order_date date, email text,"
        " payload jsonb, is_gift boolean)",
        f'INSERT INTO "{schema}"."Orders" VALUES'
        " (1, 10, 'new', 19.99, now() - interval '2 hours', now() - interval '3 hours',"
        " current_date, 'ann@example.com', '{\"a\": 1}', false),"
        " (2, 11, 'shipped', 5.00, now() - interval '3 hours', now() - interval '4 hours',"
        " current_date - 1, 'bob@example.com', '{\"a\": 2}', true),"
        " (3, NULL, 'shipped', 250.10, now() - interval '5 hours', NULL, current_date - 2,"
        " NULL, NULL, NULL),"
        " (4, 12, 'cancelled', -3.50, now() - interval '30 hours', now() - interval '31 hours',"
        " current_date - 3, 'cy@example.com', '[]', false),"
        " (5, 12, 'bogus', 42.00, now() - interval '1 day', now() - interval '25 hours',"
        " current_date - 1, 'dee@example.com', '{\"a\": 5}', false),"
        " (6, 13, NULL, 0.00, NULL, NULL, NULL, 'eve@example.com', '{\"a\": 6}', true)",
        f'CREATE TABLE "{schema}".orders_lc (id integer, loaded_at timestamptz)',
        f'INSERT INTO "{schema}".orders_lc VALUES (1, now()), (2, now()), (2, now())',
        f'CREATE VIEW "{schema}".big_orders AS SELECT * FROM "{schema}"."Orders" WHERE amount > 10',
        f'CREATE MATERIALIZED VIEW "{schema}".order_totals AS SELECT count(*) AS n'
        f' FROM "{schema}"."Orders"',
        # Types whose own name hides that they cannot aggregate: a domain over json, and arrays
        # whose ELEMENT has no MIN/MAX or no equality.
        f'CREATE DOMAIN "{schema}".payload_doc AS json',
        f'CREATE TABLE "{schema}"."TypeEdges" (doc "{schema}".payload_doc, docs json[],'
        " flags boolean[], tags text[])",
        f'INSERT INTO "{schema}"."TypeEdges" VALUES'
        " ('{\"a\": 1}', ARRAY['{}'::json], ARRAY[true], ARRAY['x', 'y']),"
        " (NULL, NULL, ARRAY[false], ARRAY['z'])",
        # A function with the SAME signature as a built-in, planted in the target schema by
        # whoever can CREATE there. It must never be the one DataQ's SQL resolves to.
        f'CREATE FUNCTION "{schema}".upper(text) RETURNS text'
        " LANGUAGE sql IMMUTABLE AS $$ SELECT 'HIJACKED'::text $$",
        f'CREATE SCHEMA "{hidden}"',
        f'CREATE TABLE "{hidden}".secret_stuff (x integer)',
    ]


@pytest.fixture(scope="module")
def pg() -> Iterator[PgTarget]:
    assert TEST_DATABASE_URL is not None  # narrowed by the module-level skipif
    url = make_url(TEST_DATABASE_URL)
    suffix = uuid.uuid4().hex[:8]
    schema, hidden, role = f"DqPg_{suffix}", f"dq_hidden_{suffix}", f"dq_pg_reader_{suffix}"
    # URL-hostile on purpose: the adapter must escape it into the DSN, not break on it.
    password = secrets.token_urlsafe(18) + "@:/?#%"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        for statement in _seed_sql(schema, hidden):
            conn.execute(text(statement))
        conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD :pw").bindparams(pw=password))
        database = conn.execute(text("SELECT current_database()")).scalar_one()
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {role}'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}'))
        conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO {role}'))
    config = {
        "host": url.host or "localhost",
        "port": url.port or 5432,
        "database": database,
        "user": role,
        "schema": schema,
        # The test server has no TLS; production defaults to `require`.
        "sslmode": "disable",
    }
    try:
        yield PgTarget(config, password, schema, hidden, role)
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{hidden}" CASCADE'))
            conn.execute(text(f"DROP OWNED BY {role}"))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def _connection(pg: PgTarget, **overrides: Any) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="pg",
        type="postgres",
        env="dev",
        config={**pg.config, **overrides},
        secret_ref="pg-ref",
    )


def _store(pg: PgTarget) -> FakeSecretStore:
    return FakeSecretStore({"pg-ref": pg.password})


def _runner(pg: PgTarget) -> Any:
    return build_check_runner(
        conn_type="postgres", config=pg.config, secret_ref="pg-ref", secret_store=_store(pg)
    )


# ───────────────────────────── connection ─────────────────────────────


def test_the_adapter_tests_a_real_login_and_rejects_a_wrong_password(pg: PgTarget) -> None:
    adapter = get_connection_adapter("postgres")
    adapter.test(pg.config, pg.password)

    with pytest.raises(DBAPIError) as exc:
        adapter.test(pg.config, pg.password + "x")
    # The credential-health signal (#1697) must recognise PostgreSQL's own wording.
    assert is_auth_failure(exc.value)
    assert classify_failure_category(exc.value) is FailureCategory.PERMISSION


def test_tls_required_against_a_server_without_tls_fails_as_connectivity(pg: PgTarget) -> None:
    config = {k: v for k, v in pg.config.items() if k != "sslmode"}  # the `require` default
    with pytest.raises(DBAPIError) as exc:
        get_connection_adapter("postgres").test(config, pg.password)
    assert classify_failure_category(exc.value) is FailureCategory.CONNECTIVITY
    assert not is_auth_failure(exc.value)


def test_every_session_is_read_only(pg: PgTarget) -> None:
    url, connect_args = _SPEC.engine_args(_SPEC.validate_config(pg.config), pg.password)
    engine = create_engine(url, connect_args=connect_args)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
            assert (
                conn.execute(text("SHOW search_path")).scalar()
                == f'pg_catalog,"{pg.schema}",public'
            )
        with pytest.raises(DBAPIError, match="read-only transaction"):
            with engine.begin() as conn:
                conn.execute(text("CREATE TEMP TABLE dq_write_probe (i integer)"))
    finally:
        engine.dispose()


# ───────────────────────────── expectations ─────────────────────────────

# One real invocation per allowlisted type the SQL batch can run, against the mixed-case target.
# `expected` is the verdict the seeded rows produce — asserting it (not just "no error") is what
# proves the pushdown compiled the right predicate on this dialect.
_CASES: list[tuple[str, dict[str, Any], bool]] = [
    ("expect_column_values_to_not_be_null", {"column": "email"}, False),
    ("expect_column_values_to_be_null", {"column": "payload"}, False),
    ("expect_column_values_to_be_unique", {"column": "CustomerId"}, False),
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
    ("expect_column_values_to_be_of_type", {"column": "amount", "type_": "NUMERIC(12, 2)"}, True),
    (
        "expect_column_values_to_be_in_type_list",
        {"column": "placed_at", "type_list": ["TIMESTAMP WITH TIME ZONE"]},
        True,
    ),
    ("expect_compound_columns_to_be_unique", {"column_list": ["order_id", "CustomerId"]}, True),
    (
        "expect_select_column_values_to_be_unique_within_record",
        {"column_list": ["order_id", "CustomerId"]},
        True,
    ),
    (
        "expect_column_pair_values_a_to_be_greater_than_b",
        {"column_A": "placed_at", "column_B": "shipped_local"},
        True,
    ),
    (
        "expect_column_pair_values_to_be_equal",
        {"column_A": "order_id", "column_B": "CustomerId"},
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
        {"column_list": ["order_id", "CustomerId"], "sum_total": 12},
        False,
    ),
    ("expect_table_row_count_to_be_between", {"min_value": 6, "max_value": 6}, True),
    (
        CUSTOM_SQL_EXPECTATION_TYPE,
        {"unexpected_rows_query": 'SELECT * FROM {batch} WHERE amount < 0 OR "CustomerId" IS NULL'},
        False,
    ),
]


def test_the_cases_cover_every_type_a_sql_batch_can_run() -> None:
    """A type added to the allowlist is not supported here until it is run here (#953)."""
    covered = {expectation_type for expectation_type, _, _ in _CASES}
    assert covered == (ALLOWED_EXPECTATION_TYPES - DATAFRAME_ONLY_EXPECTATION_TYPES) | {
        CUSTOM_SQL_EXPECTATION_TYPE
    }


def test_every_sql_capable_type_runs_by_pushdown_with_the_expected_verdict(pg: PgTarget) -> None:
    checks = [CheckSpec(expectation_type, kwargs) for expectation_type, kwargs, _ in _CASES]
    runner = _runner(pg)
    try:
        outcome = runner.run_checks(
            table="Orders",
            schema=pg.schema,
            checks=checks,
            index_columns=["order_id"],
            value_signal_gate=lambda column: True,
        )
    finally:
        runner.close()
    got = {
        spec.expectation_type: (result.errored, result.error_message, result.success)
        for spec, result in zip(checks, outcome.checks, strict=True)
    }
    expected = {expectation_type: (False, None, verdict) for expectation_type, _, verdict in _CASES}
    assert got == expected


def test_a_failing_check_on_a_json_column_reports_its_rows_rather_than_erroring(
    pg: PgTarget,
) -> None:
    """psycopg2 decodes jsonb into dicts/lists, and GX's result formatting reads a dict cell as a
    multi-column row — every failing column-map check on a JSON column errored until the run
    engines kept JSON cells as text (live-found on this adapter's first run).
    """
    runner = _runner(pg)
    try:
        outcome = runner.run_checks(
            table="Orders",
            schema=pg.schema,
            checks=[CheckSpec("expect_column_values_to_be_unique", {"column": "payload"})],
        )
    finally:
        runner.close()
    [result] = outcome.checks
    assert not result.errored, result.error_message
    assert result.success is True
    # And a failing one carries the cells as JSON text.
    runner = _runner(pg)
    try:
        [failed] = runner.run_checks(
            table="Orders",
            schema=pg.schema,
            checks=[CheckSpec("expect_column_values_to_be_null", {"column": "payload"})],
        ).checks
    finally:
        runner.close()
    assert not failed.errored, failed.error_message
    assert '{"a": 1}' in (failed.sample_failures or {})["partial_unexpected_list"]


def test_failing_rows_carry_the_identifier_column_as_their_locator(pg: PgTarget) -> None:
    runner = _runner(pg)
    try:
        [result] = runner.run_checks(
            table="Orders",
            schema=pg.schema,
            checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "email"})],
            index_columns=["order_id"],
        ).checks
    finally:
        runner.close()
    assert (result.sample_failures or {})["unexpected_index_list"] == [
        {"order_id": 3, "email": None}
    ]


def test_a_finished_run_leaves_no_server_session_open(pg: PgTarget) -> None:
    """GX builds a fresh engine per execution engine and never disposes it: each run used to
    leave one idle backend behind until garbage collection (live-found — three runs, three
    backends). An OLTP server's connection slots are small and shared."""
    assert TEST_DATABASE_URL is not None
    admin = create_engine(TEST_DATABASE_URL)
    try:
        for _ in range(3):
            runner = _runner(pg)
            try:
                runner.run_checks(
                    table="Orders",
                    schema=pg.schema,
                    checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "email"})],
                )
            finally:
                runner.close()
        with admin.connect() as conn:
            backends = conn.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE usename = :role"),
                {"role": pg.role},
            ).scalar_one()
    finally:
        admin.dispose()
    assert backends == 0


def test_a_lower_case_table_resolves_bare(pg: PgTarget) -> None:
    runner = _runner(pg)
    try:
        [result] = runner.run_checks(
            table="orders_lc",
            schema=pg.schema,
            checks=[CheckSpec("expect_column_values_to_be_unique", {"column": "id"})],
        ).checks
    finally:
        runner.close()
    assert not result.errored
    assert result.success is False  # id 2 appears twice


def test_a_missing_table_raises_a_config_classified_error(pg: PgTarget) -> None:
    runner = _runner(pg)
    try:
        with pytest.raises(Exception) as exc:
            runner.run_checks(
                table="NoSuchTable",
                schema=pg.schema,
                checks=[CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0})],
            )
    finally:
        runner.close()
    assert classify_failure_category(exc.value) is FailureCategory.CONFIG


# ───────────────────────────── monitors ─────────────────────────────


def test_freshness_reads_timestamptz_timestamp_and_date_and_volume_counts(pg: PgTarget) -> None:
    monitors = [
        MonitorSpec("freshness", {"column": "placed_at"}),
        MonitorSpec("freshness", {"column": "shipped_local"}),
        MonitorSpec("freshness", {"column": "order_date"}),
        MonitorSpec("freshness", {"column": "CustomerId"}),
        MonitorSpec("volume", {"min_rows": 1, "max_rows": 5}),
    ]
    runner = _runner(pg)
    try:
        placed, shipped, dated, not_a_time, volume = runner.run_monitors(
            table="Orders", schema=pg.schema, monitors=monitors
        )
        unqualified = runner.run_monitors(
            table="Orders",
            schema=None,
            monitors=[MonitorSpec("volume", {"min_rows": 0, "max_rows": 9})],
        )
    finally:
        runner.close()
    assert placed.metric_value is not None and 1.9 < placed.metric_value < 2.5
    # A naive `timestamp` is read as UTC — the server session here runs in UTC, as CI's does.
    assert shipped.metric_value is not None and shipped.metric_value >= 0
    assert dated.metric_value is not None and 0 <= dated.metric_value <= 24
    assert not_a_time.errored and "not a date/timestamp" in (not_a_time.error_message or "")
    assert volume.observed_value == {"row_count": 6, "deviation_pct": 20.0}
    # No schema → the connection's schema, via the session's pinned search_path.
    assert unqualified[0].observed_value == {"row_count": 6, "deviation_pct": 0.0}


def test_the_anomaly_monitor_measures_row_count_and_freshness_age(pg: PgTarget) -> None:
    from datetime import UTC, datetime

    from backend.app.datasources.monitors import anomaly_params
    from backend.app.services.anomaly import measure_metric

    connection, store, now = _connection(pg), _store(pg), datetime.now(UTC)
    rows = measure_metric(
        connection,
        table="Orders",
        schema=pg.schema,
        catalog=None,
        params=anomaly_params({"target_metric": "row_count"}),
        secret_store=store,
        now=now,
    )
    age = measure_metric(
        connection,
        table="Orders",
        schema=None,  # the connection's schema, as a suite target without one resolves
        catalog=None,
        params=anomaly_params({"target_metric": "freshness_age_hours", "column": "placed_at"}),
        secret_store=store,
        now=now,
    )
    assert rows == 6.0
    assert 1.9 < age < 2.5


# ───────────────────────────── introspection ─────────────────────────────


def test_the_profile_survives_json_and_boolean_columns(pg: PgTarget) -> None:
    """PostgreSQL has no MIN/MAX over boolean or jsonb: one such column used to fail the whole
    profile. Their min/max are reported unavailable (null); every other statistic stands.
    """
    profile = profile_service.profile_table(
        _connection(pg),
        table="Orders",
        schema=pg.schema,
        columns=["amount", "payload", "is_gift", "CustomerId"],
        top_n=2,
        secret_store=_store(pg),
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 6
    assert by_column["amount"].min_value == -3.5 and by_column["amount"].max_value == 250.1
    assert by_column["payload"].min_value is None and by_column["payload"].max_value is None
    assert by_column["payload"].null_count == 1 and by_column["payload"].distinct_count == 5
    assert by_column["is_gift"].min_value is None and by_column["is_gift"].distinct_count == 2
    assert by_column["is_gift"].top_values == [
        {"value": False, "count": 3},
        {"value": True, "count": 2},
    ]
    assert by_column["CustomerId"].top_values[0] == {"value": 12, "count": 2}


def test_a_function_planted_in_the_target_schema_never_overrides_a_builtin(
    pg: PgTarget,
) -> None:
    """pg_catalog is FIRST on the session's search_path, so `upper(text)` is the built-in even
    though the target schema defines a same-signature `upper(text)` (the CVE-2018-1058 shape:
    whoever can CREATE in a schema DataQ reads would otherwise run code under DataQ's credential).
    """
    runner = _runner(pg)
    try:
        [result] = runner.run_checks(
            table="Orders",
            schema=pg.schema,
            checks=[
                CheckSpec(
                    CUSTOM_SQL_EXPECTATION_TYPE,
                    {
                        "unexpected_rows_query": (
                            "SELECT * FROM {batch} WHERE upper(status) = 'HIJACKED'"
                        )
                    },
                )
            ],
        ).checks
    finally:
        runner.close()
    assert not result.errored, result.error_message
    assert result.success is True


def test_the_profile_survives_domains_over_json_and_arrays_of_unaggregatable_types(
    pg: PgTarget,
) -> None:
    profile = profile_service.profile_table(
        _connection(pg),
        table="TypeEdges",
        schema=pg.schema,
        columns=["doc", "docs", "flags", "tags"],
        top_n=2,
        secret_store=_store(pg),
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 2
    # A domain over json has neither MIN/MAX nor equality — nor does json[].
    for name in ("doc", "docs"):
        assert by_column[name].min_value is None and by_column[name].distinct_count is None
        assert by_column[name].top_values == []
    # boolean[] has equality (distinct works) but no MIN/MAX; text[] has both.
    assert by_column["flags"].min_value is None and by_column["flags"].distinct_count == 2
    assert by_column["tags"].min_value == ["x", "y"] and by_column["tags"].distinct_count == 2


def test_schema_drift_resolves_the_target_exactly_as_spelled(pg: PgTarget) -> None:
    """`ORDERS` is not `"Orders"` on PostgreSQL — every other path errors on it, so drift must
    not quietly baseline the case-variant table instead.
    """
    with pytest.raises(schema_drift.SchemaIntrospectionError, match="not found"):
        schema_drift.introspect_columns(
            _connection(pg), table="ORDERS", schema=pg.schema, catalog=None, secret_store=_store(pg)
        )


def test_columns_schema_drift_and_comparison_reads(pg: PgTarget) -> None:
    connection, store = _connection(pg), _store(pg)
    columns = profile_service.list_table_columns(
        connection, table="Orders", schema=None, secret_store=store
    )
    assert columns[:3] == ["order_id", "CustomerId", "status"]
    snapshot = schema_drift.introspect_columns(
        connection, table="Orders", schema=pg.schema, catalog=None, secret_store=store
    )
    assert {"name": "payload", "type": "jsonb"} in snapshot
    assert {"name": "placed_at", "type": "timestamp with time zone"} in snapshot
    frame = read_dataset(
        connection, DatasetSpec(table="Orders", schema=pg.schema), max_rows=10, secret_store=store
    )
    assert len(frame) == 6


# ───────────────────────────── inventory + browse ─────────────────────────────


def test_enumeration_is_privilege_filtered_and_joins_the_suite_target_identity(
    pg: PgTarget,
) -> None:
    connection, store = _connection(pg), _store(pg)
    with profile_service._open_connection(connection, store) as conn:
        schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
        identities = get_table_enumerator("postgres").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=pg.config
        )
    assert pg.schema in schemas
    # No USAGE on the hidden schema → neither it nor its table is offered.
    assert pg.hidden_schema not in schemas
    names = {identity.name for identity in identities}
    assert not any(pg.hidden_schema in name for name in names)
    database = pg.config["database"]
    assert {
        f"{database}.{pg.schema}.Orders",
        f"{database}.{pg.schema}.orders_lc",
        f"{database}.{pg.schema}.big_orders",
    } <= names
    target = resolve_asset_identity("postgres", pg.config, {"table": "Orders"})
    assert (target.namespace, target.name) in {(i.namespace, i.name) for i in identities}


def test_browse_walks_schemas_then_tables(db_session: Any, pg: PgTarget) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"pg-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config=pg.config,
        secret_ref="pg-ref",
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    kwargs: dict[str, Any] = {"session": db_session, "limit": 50, "secret_store": _store(pg)}
    top = browse_service.browse_catalog(connection, catalog=None, schema=None, **kwargs)
    tables = browse_service.browse_catalog(connection, catalog=None, schema=pg.schema, **kwargs)
    assert top.level == "schema" and pg.schema in [e.name for e in top.entries]
    assert tables.level == "table"
    assert [(e.name, e.object_type) for e in tables.entries] == [
        ("Orders", "table"),
        ("TypeEdges", "table"),
        ("big_orders", "view"),
        ("order_totals", "materialized_view"),
        ("orders_lc", "table"),
    ]
    with pytest.raises(browse_service.BrowseInputInvalidError):
        browse_service.browse_catalog(connection, catalog="other_db", schema=None, **kwargs)


# ───────────────────────────── end to end ─────────────────────────────


def test_a_suite_run_persists_results_end_to_end(db_session: Any, pg: PgTarget) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"pg-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config=pg.config,
        secret_ref="pg-ref",
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(name="pg", connection_id=connection.id, created_by=owner.id)
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
            name="rows",
            kind="volume",
            expectation_type="monitor:volume",
            config={"min_rows": 1, "max_rows": 100},
        ),
    ]
    db_session.add_all(checks)
    db_session.flush()
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()

    runner = _runner(pg)
    try:
        run_service.execute_run(
            db_session, run=run, checks=checks, runner=runner, table="Orders", schema=pg.schema
        )
    finally:
        runner.close()

    assert run.status == "succeeded"
    by_check = {
        r.check_id: r for r in db_session.scalars(select(Result).where(Result.run_id == run.id))
    }
    assert by_check[checks[0].id].status == "fail"
    assert by_check[checks[1].id].status == "pass"
    assert by_check[checks[1].id].observed_value == {"row_count": 6, "deviation_pct": 0.0}
