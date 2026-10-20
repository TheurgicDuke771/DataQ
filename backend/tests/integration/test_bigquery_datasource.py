"""Google BigQuery (#1681) against a real ``goccy/bigquery-emulator``.

Opt-in: ``BIGQUERY_EMULATOR_URL=http://127.0.0.1:9050`` with the emulator started for the
project ``BIGQUERY_EMULATOR_PROJECT`` (default ``dataq-emu``)::

    docker run --rm -p 127.0.0.1:9050:9050 ghcr.io/goccy/bigquery-emulator:0.8.1 --project=dataq-emu

Every value crosses the real ``google-cloud-bigquery`` boundary (#953): the dialect, the DBAPI,
the REST client and google-auth's JWT-bearer flow (the key file's ``token_uri`` is a local stub
that issues a bearer). Real BigQuery is not reachable from CI (#2224). What the emulator cannot
show is named where it matters: it has no IAM (so the read-only guarantee is untestable here)
and it does not enforce ``maximum_bytes_billed`` (only that the cap reaches every job). Start it
fresh: it resolves an unqualified table name across every dataset rather than in the job's default
dataset, so a leftover dataset holding another ``orders`` would answer for this one.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from sqlalchemy import select, text

from backend.app.datasources import generic_sql, registry
from backend.app.datasources.base import CheckSpec, MonitorSpec, TargetShapeError
from backend.app.datasources.bigquery import BIGQUERY
from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
)
from backend.app.datasources.registry import build_check_runner, get_connection_adapter
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.lineage.warehouse import get_table_enumerator
from backend.app.services import browse_service, profile_service, run_service, schema_drift
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE
from backend.app.services.dataset_reader import DatasetSpec, read_dataset
from backend.tests.support.fake_secret_store import FakeSecretStore

_URL = os.environ.get("BIGQUERY_EMULATOR_URL", "").rstrip("/")
_PROJECT = os.environ.get("BIGQUERY_EMULATOR_PROJECT", "dataq-emu")
pytestmark = pytest.mark.skipif(
    not _URL, reason="requires BIGQUERY_EMULATOR_URL (goccy/bigquery-emulator)"
)

_SECRET_REF = "bigquery-key"


class _TokenStub(BaseHTTPRequestHandler):
    """The OAuth token endpoint a key file's ``token_uri`` names: hands out one bearer, or —
    under ``/revoked`` — refuses the grant the way Google does for a deleted key."""

    grants: list[bytes] = []  # noqa: RUF012 — module-scoped

    def do_POST(self) -> None:
        _TokenStub.grants.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path.startswith("/revoked"):
            status = 400
            out = json.dumps(
                {"error": "invalid_grant", "error_description": "Invalid JWT Signature."}
            )
        else:
            status = 200
            out = json.dumps({"access_token": "stub", "expires_in": 3600, "token_type": "Bearer"})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out.encode())

    def log_message(self, *_: Any) -> None:
        pass


def _key_file(token_uri: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    return json.dumps(
        {
            "type": "service_account",
            "project_id": _PROJECT,
            "private_key_id": "k1",
            "private_key": pem,
            "client_email": f"dq@{_PROJECT}.iam.gserviceaccount.com",
            "client_id": "1",
            "token_uri": token_uri,
        }
    )


def _admin() -> Any:
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project=_PROJECT,
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=_URL),
    )


def _seed(admin: Any, dataset: str) -> None:
    from google.cloud import bigquery

    admin.create_dataset(f"{_PROJECT}.{dataset}")
    orders = bigquery.Table(
        f"{_PROJECT}.{dataset}.orders",
        schema=[
            bigquery.SchemaField("order_id", "INT64"),
            bigquery.SchemaField("customerid", "INT64"),
            bigquery.SchemaField("status", "STRING"),
            bigquery.SchemaField("amount", "NUMERIC"),
            bigquery.SchemaField("placed_at", "TIMESTAMP"),
            bigquery.SchemaField("shipped_local", "DATETIME"),
            bigquery.SchemaField("order_date", "DATE"),
            bigquery.SchemaField("email", "STRING"),
            bigquery.SchemaField("is_gift", "BOOL"),
        ],
    )
    admin.create_table(orders)
    admin.query(
        f"INSERT INTO `{dataset}`.`orders` VALUES "  # nosec B608
        "(1, 10, 'new', NUMERIC '19.99', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR),"
        " DATETIME '2026-10-19 09:00:00', CURRENT_DATE(), 'ann@example.com', false),"
        "(2, 11, 'shipped', NUMERIC '5.00', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 3 HOUR),"
        " DATETIME '2026-10-19 08:00:00', DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY),"
        " 'bob@example.com', true),"
        "(3, NULL, 'shipped', NUMERIC '250.10',"
        " TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 HOUR), NULL,"
        " DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY), NULL, NULL),"
        "(4, 12, 'cancelled', NUMERIC '-3.50',"
        " TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 HOUR), DATETIME '2026-10-18 01:00:00',"
        " DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY), 'cy@example.com', false),"
        "(5, 12, 'bogus', NUMERIC '42.00', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR),"
        " DATETIME '2026-10-18 02:00:00', DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY),"
        " 'dee@example.com', false),"
        "(6, 13, NULL, NUMERIC '0.00', NULL, NULL, NULL, 'eve@example.com', true)"
    ).result()
    events = bigquery.Table(
        f"{_PROJECT}.{dataset}.CustomerEvents",
        schema=[bigquery.SchemaField("EventId", "INT64"), bigquery.SchemaField("Kind", "STRING")],
    )
    admin.create_table(events)
    admin.query(
        f"INSERT INTO `{dataset}`.`CustomerEvents` VALUES (1, 'click'), (2, 'Click')"  # nosec B608
    ).result()


@pytest.fixture(scope="module", autouse=True)
def _string_parameter_values() -> Iterator[None]:
    """The REST API documents a query parameter's ``value`` as a string; the client sends a
    FLOAT64/NUMERIC parameter as a JSON number, which BigQuery accepts and the emulator refuses
    (``cannot unmarshal number``). Sending the documented form lets the emulator evaluate the
    exact SQL GX generates; production keeps the client's own encoding."""
    from google.cloud.bigquery import _helpers

    with pytest.MonkeyPatch.context() as patch:
        for kind in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
            encode = _helpers._SCALAR_VALUE_TO_JSON_PARAM[kind]
            patch.setitem(
                _helpers._SCALAR_VALUE_TO_JSON_PARAM,
                kind,
                lambda value, encode=encode: None if value is None else str(encode(value)),
            )
        yield


@dataclass(frozen=True)
class BigQueryTarget:
    config: dict[str, Any]
    dataset: str
    store: FakeSecretStore


@pytest.fixture(scope="module")
def bq() -> Iterator[BigQueryTarget]:
    server = HTTPServer(("127.0.0.1", 0), _TokenStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    admin = _admin()
    dataset = f"dq_bq_{uuid.uuid4().hex[:8]}"
    try:
        _seed(admin, dataset)
        token_uri = f"http://127.0.0.1:{server.server_address[1]}"
        config = {"project_id": _PROJECT, "schema": dataset, "endpoint_url": _URL}
        store = FakeSecretStore(
            {
                _SECRET_REF: _key_file(f"{token_uri}/token"),
                "revoked": _key_file(f"{token_uri}/revoked"),
            }
        )
        yield BigQueryTarget(config, dataset, store)
    finally:
        admin.delete_dataset(f"{_PROJECT}.{dataset}", delete_contents=True, not_found_ok=True)
        admin.close()
        server.shutdown()


def _secret(bq: BigQueryTarget) -> str:
    return bq.store.get(_SECRET_REF)


def _runner(bq: BigQueryTarget, **overrides: Any) -> Any:
    return build_check_runner(
        conn_type="bigquery",
        config={**bq.config, **overrides},
        secret_ref=_SECRET_REF,
        secret_store=bq.store,
    )


def _connection(bq: BigQueryTarget) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="bq",
        type="bigquery",
        env="dev",
        config=bq.config,
        secret_ref=_SECRET_REF,
    )


def _run(bq: BigQueryTarget, table: str, checks: list[CheckSpec], **kwargs: Any) -> Any:
    runner = _runner(bq)
    try:
        return runner.run_checks(table=table, schema=bq.dataset, checks=checks, **kwargs)
    finally:
        runner.close()


# ───────────────────────────── connection ─────────────────────────────


def test_the_connection_test_runs_the_service_account_flow(bq: BigQueryTarget) -> None:
    before = len(_TokenStub.grants)
    get_connection_adapter("bigquery").test(bq.config, _secret(bq))
    assert len(_TokenStub.grants) > before
    assert b"jwt-bearer" in _TokenStub.grants[-1]


def test_a_revoked_key_is_an_auth_failure(bq: BigQueryTarget) -> None:
    from backend.app.services.failure_classifier import is_auth_failure

    with pytest.raises(Exception) as exc:
        get_connection_adapter("bigquery").test(bq.config, bq.store.get("revoked"))
    assert is_auth_failure(exc.value), repr(exc.value)


def test_a_malformed_key_file_is_refused_before_any_request(bq: BigQueryTarget) -> None:
    from backend.app.datasources.gcs import GcsKeyInvalidError

    with pytest.raises(GcsKeyInvalidError):
        get_connection_adapter("bigquery").test(bq.config, '{"type": "authorized_user"}')


def test_every_query_job_carries_the_byte_cap_and_default_dataset(bq: BigQueryTarget) -> None:
    """The cap is a default job config on the ONE client, so it reaches every query."""
    config = BIGQUERY.validate_config({**bq.config, "maximum_bytes_billed": 52_428_800})
    client = BIGQUERY.session_connect_args(config, _secret(bq), None)["client"]
    try:
        job = client.query("SELECT COUNT(*) FROM orders")
        assert [tuple(row) for row in job.result()] == [(6,)]
        assert job.maximum_bytes_billed == 52_428_800
        assert job.default_dataset.dataset_id == bq.dataset
    finally:
        client.close()


# ───────────────────────────── expectations ─────────────────────────────

_CASES: list[tuple[str, dict[str, Any], bool]] = [
    ("expect_column_values_to_not_be_null", {"column": "email"}, False),
    ("expect_column_values_to_be_null", {"column": "shipped_local"}, False),
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
    ("expect_column_values_to_be_of_type", {"column": "amount", "type_": "NUMERIC"}, True),
    (
        "expect_column_values_to_be_in_type_list",
        {"column": "placed_at", "type_list": ["TIMESTAMP"]},
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
    bq: BigQueryTarget,
) -> None:
    checks = [CheckSpec(expectation_type, kwargs) for expectation_type, kwargs, _ in _CASES]
    outcome = _run(
        bq, "orders", checks, index_columns=["order_id"], value_signal_gate=lambda column: True
    )
    got = {
        spec.expectation_type: (result.errored, result.error_message, result.success)
        for spec, result in zip(checks, outcome.checks, strict=True)
    }
    assert got == {
        expectation_type: (False, None, verdict) for expectation_type, _, verdict in _CASES
    }


def test_uniqueness_reports_its_rows(bq: BigQueryTarget) -> None:
    [result] = _run(
        bq,
        "orders",
        [CheckSpec("expect_column_values_to_be_unique", {"column": "customerid"})],
        index_columns=["order_id"],
    ).checks
    assert not result.errored, result.error_message
    assert sorted(
        (row["order_id"], row["customerid"])
        for row in (result.sample_failures or {})["unexpected_index_list"]
    ) == [(4, 12), (5, 12)]


def test_a_mixed_case_table_resolves_exactly_as_spelled(bq: BigQueryTarget) -> None:
    [count, unique] = _run(
        bq,
        "CustomerEvents",
        [
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 2, "max_value": 2}),
            CheckSpec("expect_column_values_to_be_unique", {"column": "Kind"}),
        ],
    ).checks
    assert (count.errored, count.success) == (False, True), count.error_message
    # 'click' and 'Click' are different values: GoogleSQL compares strings case-sensitively.
    assert (unique.errored, unique.success) == (False, True), unique.error_message


def test_a_partition_decorator_is_refused_when_the_suite_is_saved() -> None:
    with pytest.raises(TargetShapeError, match="partition"):
        registry.resolve_target_shape("bigquery", {"table": "orders$20261019"})


# ───────────────────────────── monitors ─────────────────────────────


def test_freshness_over_timestamp_datetime_and_date_and_volume(bq: BigQueryTarget) -> None:
    monitors = [
        MonitorSpec("freshness", {"column": "placed_at"}),
        MonitorSpec("freshness", {"column": "order_date"}),
        MonitorSpec("freshness", {"column": "status"}),
        MonitorSpec("volume", {"min_rows": 1, "max_rows": 5}),
    ]
    runner = _runner(bq)
    try:
        placed, dated, not_a_time, volume = runner.run_monitors(
            table="orders", schema=bq.dataset, monitors=monitors
        )
    finally:
        runner.close()
    # A TIMESTAMP comes back tz-aware (UTC) from the client.
    assert placed.metric_value is not None and 1.9 < placed.metric_value < 2.5
    assert dated.metric_value is not None and 0 <= dated.metric_value <= 48
    assert not_a_time.errored
    assert volume.observed_value == {"row_count": 6, "deviation_pct": 20.0}


def test_the_anomaly_monitor_measures_row_count_and_freshness_age(bq: BigQueryTarget) -> None:
    from backend.app.datasources.monitors import anomaly_params
    from backend.app.services.anomaly import measure_metric

    connection, now = _connection(bq), datetime.now(UTC)
    rows = measure_metric(
        connection,
        table="orders",
        schema=bq.dataset,
        catalog=None,
        params=anomaly_params({"target_metric": "row_count"}),
        secret_store=bq.store,
        now=now,
    )
    age = measure_metric(
        connection,
        table="orders",
        schema=None,
        catalog=None,
        params=anomaly_params({"target_metric": "freshness_age_hours", "column": "placed_at"}),
        secret_store=bq.store,
        now=now,
    )
    assert rows == 6.0
    assert age is not None and 1.9 < age < 2.5


# ───────────────────────────── introspection ─────────────────────────────


def test_the_profile_reports_decimals_booleans_and_timestamps(bq: BigQueryTarget) -> None:
    profile = profile_service.profile_table(
        _connection(bq),
        table="orders",
        schema=bq.dataset,
        columns=["amount", "email", "is_gift", "placed_at"],
        top_n=2,
        secret_store=bq.store,
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 6
    assert (by_column["amount"].min_value, by_column["amount"].max_value) == (-3.5, 250.1)
    assert by_column["email"].null_count == 1
    assert (by_column["is_gift"].min_value, by_column["is_gift"].max_value) == (False, True)
    assert by_column["placed_at"].min_value is not None


def test_columns_schema_drift_and_comparison_reads(bq: BigQueryTarget) -> None:
    connection = _connection(bq)
    columns = profile_service.list_table_columns(
        connection, table="orders", schema=None, secret_store=bq.store
    )
    assert columns[:3] == ["order_id", "customerid", "status"]
    snapshot = schema_drift.introspect_columns(
        connection, table="orders", schema=bq.dataset, catalog=None, secret_store=bq.store
    )
    assert {"name": "amount", "type": "NUMERIC"} in snapshot
    with pytest.raises(schema_drift.SchemaIntrospectionError):
        schema_drift.introspect_columns(
            connection, table="nope", schema=bq.dataset, catalog=None, secret_store=bq.store
        )
    frame = read_dataset(
        connection,
        DatasetSpec(table="orders", schema=bq.dataset),
        max_rows=10,
        secret_store=bq.store,
    )
    assert len(frame) == 6
    assert Decimal(str(frame["amount"].dropna().max())) == Decimal("250.1")


def test_enumeration_joins_the_suite_target_identity(bq: BigQueryTarget) -> None:
    with profile_service._open_connection(_connection(bq), bq.store) as conn:
        schemas = generic_sql.schema_names(BIGQUERY, conn, limit=None)
        tables = generic_sql.typed_table_rows(BIGQUERY, conn, schema=bq.dataset, limit=None)
        identities = get_table_enumerator("bigquery").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=bq.config
        )
    assert bq.dataset in schemas
    assert sorted((t, kind) for _, t, kind in tables) == [
        ("CustomerEvents", "table"),
        ("orders", "table"),
    ]
    target = resolve_asset_identity("bigquery", bq.config, {"table": "orders"})
    assert target.namespace == f"bigquery://{_URL.split('://', 1)[1]}"
    assert target.name == f"{_PROJECT}.{bq.dataset}.orders"
    assert (target.namespace, target.name) in {(i.namespace, i.name) for i in identities}


def _stored_connection(db_session: Any, bq: BigQueryTarget) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"bq-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"bq-{uuid.uuid4().hex[:6]}",
        type="bigquery",
        env="dev",
        config=bq.config,
        secret_ref=_SECRET_REF,
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def test_browse_walks_datasets_then_tables(db_session: Any, bq: BigQueryTarget) -> None:
    connection = _stored_connection(db_session, bq)
    kwargs: dict[str, Any] = {"session": db_session, "limit": 500, "secret_store": bq.store}
    top = browse_service.browse_catalog(connection, catalog=None, schema=None, **kwargs)
    tables = browse_service.browse_catalog(connection, catalog=None, schema=bq.dataset, **kwargs)
    assert top.level == "schema" and bq.dataset in [e.name for e in top.entries]
    assert [(e.name, e.object_type) for e in tables.entries] == [
        ("CustomerEvents", "table"),
        ("orders", "table"),
    ]


# ───────────────────────────── end to end ─────────────────────────────


def test_a_suite_run_persists_results_end_to_end(db_session: Any, bq: BigQueryTarget) -> None:
    connection = _stored_connection(db_session, bq)
    suite = Suite(name="bigquery", connection_id=connection.id, created_by=connection.created_by)
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

    runner = _runner(bq)
    try:
        run_service.execute_run(
            db_session, run=run, checks=checks, runner=runner, table="orders", schema=bq.dataset
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
def bq_amounts(bq: BigQueryTarget) -> Iterator[None]:
    from google.cloud import bigquery

    from backend.tests.support.aggregate_lane import AMOUNTS

    admin = _admin()
    schema = [
        bigquery.SchemaField("amount", "NUMERIC"),
        bigquery.SchemaField("blank", "FLOAT64"),
    ]
    try:
        for name in ("amounts", "amounts_empty"):
            admin.create_table(bigquery.Table(f"{_PROJECT}.{bq.dataset}.{name}", schema=schema))
        values = ", ".join(
            f"({'NULL' if v is None else f'NUMERIC {str(v)!r}'}, NULL)" for v in AMOUNTS
        )
        admin.query(f"INSERT INTO `{bq.dataset}`.`amounts` VALUES {values}").result()  # nosec B608
        yield
    finally:
        for name in ("amounts", "amounts_empty"):
            admin.delete_table(f"{_PROJECT}.{bq.dataset}.{name}", not_found_ok=True)
        admin.close()


def test_every_aggregate_including_the_median_is_exact(
    bq: BigQueryTarget, bq_amounts: None
) -> None:
    from backend.tests.support.aggregate_lane import assert_aggregates

    runner = _runner(bq)
    try:
        assert_aggregates(
            runner,
            table="amounts",
            schema=bq.dataset,
            column="amount",
            null_column="blank",
            empty_table="amounts_empty",
        )
    finally:
        runner.close()


def test_a_session_runs_select_one(bq: BigQueryTarget) -> None:
    config = BIGQUERY.validate_config(bq.config)
    engine = BIGQUERY.create_engine(config, _secret(bq))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
