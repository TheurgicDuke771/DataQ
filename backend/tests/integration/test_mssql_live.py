"""The SQL Server datasource (#1679, ADR 0044) against a REAL server — an opt-in live lane.

SQL Server's own images are proprietary, so nothing in CI can stand one up (the harness rule: no
commercial licence, even in a test harness). This lane runs against a hosted database the
operator points it at — the ADR's Azure SQL free offer — and is skipped unless
``DATAQ_MSSQL_LIVE_HOST`` is set. Every statement it issues is a read. It expects this seed,
read by a ``db_datareader``-only SQL login — note the three different datetimeoffset offsets::

    CREATE TABLE dbo.Orders (
        OrderId int NOT NULL PRIMARY KEY, CustomerEmail nvarchar(200), Channel varchar(20),
        Amount decimal(12, 2), OrderTs datetime2, OrderTsTz datetimeoffset, OrderDate date,
        IsGift bit, Notes nvarchar(max));
    INSERT dbo.Orders VALUES
        (1, 'a@x.io', 'web',   10.50,    '2026-09-20T10:00', '2026-09-20T10:00+05:30',
            '2026-09-20', 0, N'first'),
        (2, 'b@x.io', 'store', -3.00,    '2026-09-21T11:00', '2026-09-21T11:00+00:00',
            '2026-09-21', 1, N'ünïcode'),
        (3, NULL,     'web',   NULL, NULL, NULL, NULL, NULL, NULL),
        (4, 'b@x.io', 'phone', 99999.99, '2026-09-27T08:00', '2026-09-27T08:00-07:00',
            '2026-09-27', 0, N'');

Environment: ``DATAQ_MSSQL_LIVE_HOST`` / ``_DATABASE`` / ``_USER`` / ``_PASSWORD`` (SQL login);
optionally ``_TENANT_ID`` / ``_CLIENT_ID`` / ``_CLIENT_SECRET`` (a service principal that is a
``db_datareader`` contained user) and ``_FABRIC_HOST`` / ``_FABRIC_DATABASE`` (a Fabric
warehouse the same principal can reach). ``DATAQ_MSSQL_LIVE_DRIVER=odbc`` runs the whole lane on
the user-installed ODBC lane instead (Microsoft ODBC Driver 18 + ``pyodbc`` on the path) and adds
the Fabric Warehouse / Lakehouse SQL endpoint batteries (``_FABRIC_HOST`` with
``_FABRIC_DATABASE`` for a warehouse holding the seed above minus ``OrderTsTz`` / ``Notes``, and
``_FABRIC_LAKEHOUSE`` for a Lakehouse whose ``dbo.orders`` holds ``order_id, email, amount,
order_ts`` with the same four rows). An auto-paused serverless database resumes on the first
login, which the fixture waits out.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from backend.app.datasources import generic_sql
from backend.app.datasources.base import CheckSpec, MonitorSpec
from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
)
from backend.app.datasources.generic_sql import KnownDatasourceLimitationError
from backend.app.datasources.mssql import FABRIC_PYTDS_LIMITATION
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

_ENV = "DATAQ_MSSQL_LIVE_"
HOST = os.environ.get(f"{_ENV}HOST")
pytestmark = pytest.mark.skipif(not HOST, reason=f"requires {_ENV}HOST (opt-in live lane)")

_SPEC = SQL_ENGINES["mssql"]


def _env(name: str) -> str | None:
    return os.environ.get(f"{_ENV}{name}") or None


DRIVER = _env("DRIVER") or "python-tds"
ODBC = DRIVER == "odbc"
_pytds_only = pytest.mark.skipif(ODBC, reason="python-tds lane behaviour")
_odbc_only = pytest.mark.skipif(not ODBC, reason="ODBC lane behaviour")


def _sql_config() -> dict[str, Any]:
    return {"host": HOST, "database": _env("DATABASE"), "user": _env("USER"), "driver": DRIVER}


def _sp_config() -> dict[str, Any]:
    return {
        "host": HOST,
        "database": _env("DATABASE"),
        "auth_type": "entra_service_principal",
        "tenant_id": _env("TENANT_ID"),
        "client_id": _env("CLIENT_ID"),
        "driver": DRIVER,
    }


def _password() -> str:
    value = _env("PASSWORD")
    assert value is not None
    return value


def _credentials(auth: str) -> tuple[dict[str, Any], str]:
    """``(config, secret)`` for one auth mode; skips when no service principal is configured."""
    if auth == "sql":
        return _sql_config(), _password()
    secret = _env("CLIENT_SECRET")
    if secret is None:
        pytest.skip("no service principal configured")
    return _sp_config(), secret


_needs_sp = pytest.mark.skipif(
    not (os.environ.get(f"{_ENV}CLIENT_SECRET")), reason="no service principal configured"
)


@pytest.fixture(scope="module", autouse=True)
def _awake() -> None:
    """Resume an auto-paused serverless database before any timed assertion runs."""
    if not HOST:
        return
    for attempt in range(8):
        try:
            get_connection_adapter("mssql").test(_sql_config(), _password())
            return
        except Exception:
            if attempt == 7:
                raise
            time.sleep(15)


def _connection(config: dict[str, Any] | None = None) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="mssql",
        type="mssql",
        env="dev",
        config=config or _sql_config(),
        secret_ref="mssql-ref",
    )


def _store(secret: str | None = None) -> FakeSecretStore:
    return FakeSecretStore({"mssql-ref": secret or _password()})


def _runner(config: dict[str, Any] | None = None, secret: str | None = None) -> Any:
    return build_check_runner(
        conn_type="mssql",
        config=config or _sql_config(),
        secret_ref="mssql-ref",
        secret_store=_store(secret),
    )


def _unrelated_ca() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dataq live-lane untrusted CA")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(7)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ───────────────────────────── connection + TLS ─────────────────────────────


def test_sql_login_connects_over_verified_tls_and_a_wrong_password_is_a_dead_credential() -> None:
    adapter = get_connection_adapter("mssql")
    adapter.test(_sql_config(), _password())
    with pytest.raises(Exception) as exc:
        adapter.test(_sql_config(), _password() + "x")
    assert is_auth_failure(exc.value)
    assert classify_failure_category(exc.value) is FailureCategory.PERMISSION


@_needs_sp
def test_a_service_principal_connects_with_a_token_and_a_bad_secret_is_a_dead_credential() -> None:
    adapter = get_connection_adapter("mssql")
    secret = _env("CLIENT_SECRET")
    assert secret is not None
    adapter.test(_sp_config(), secret)
    with pytest.raises(Exception) as exc:
        adapter.test(_sp_config(), secret + "x")
    assert is_auth_failure(exc.value)


def test_connecting_by_ip_is_refused_by_the_hostname_check() -> None:
    """python-tds: DataQ's own validator. ODBC lane: the driver's (Encrypt=yes,
    TrustServerCertificate=no) — both must refuse a certificate that does not name the host.
    """
    assert HOST is not None
    address = socket.gethostbyname(HOST)
    message = "subject name does not match host name" if ODBC else "does not match host name"
    with pytest.raises(Exception, match=message) as exc:
        get_connection_adapter("mssql").test({**_sql_config(), "host": address}, _password())
    assert classify_failure_category(exc.value) is FailureCategory.CONNECTIVITY


@_pytds_only  # a private CA is a python-tds option; the ODBC lane uses the OS trust store
def test_a_server_certificate_outside_the_configured_ca_is_refused() -> None:
    with pytest.raises(Exception, match="certificate verify failed") as exc:
        get_connection_adapter("mssql").test(
            {**_sql_config(), "ca_bundle": _unrelated_ca()}, _password()
        )
    assert classify_failure_category(exc.value) is FailureCategory.CONNECTIVITY


@pytest.mark.skipif(not _env("FABRIC_HOST"), reason="no Fabric endpoint configured")
@_needs_sp
@_pytds_only
def test_fabric_on_the_python_tds_lane_reports_the_known_limitation() -> None:
    """#2126: python-tds's routed login to Fabric is rejected. The user sees why and the fix
    (the ODBC lane) — never the driver's "system update" error.
    """
    secret = _env("CLIENT_SECRET")
    assert secret is not None
    config = {
        **_sp_config(),
        "host": _env("FABRIC_HOST"),
        "database": _env("FABRIC_DATABASE"),
    }
    with pytest.raises(KnownDatasourceLimitationError) as exc:
        get_connection_adapter("mssql").test(config, secret)
    assert str(exc.value) == FABRIC_PYTDS_LIMITATION


# ───────────────────────────── expectations ─────────────────────────────

# One real invocation per allowlisted type a SQL batch can run. `expected` is the verdict the
# seed produces — `None` means SQL Server cannot evaluate the type at all (GX has no T-SQL
# translation for it) and the check must come back ERRORED, never passed.
_CASES: list[tuple[str, dict[str, Any], bool | None]] = [
    ("expect_column_values_to_not_be_null", {"column": "CustomerEmail"}, False),
    ("expect_column_values_to_be_null", {"column": "Notes"}, False),
    ("expect_column_values_to_be_unique", {"column": "CustomerEmail"}, False),
    (
        "expect_column_values_to_be_between",
        {"column": "Amount", "min_value": -10, "max_value": 100000},
        True,
    ),
    ("expect_column_values_to_be_in_set", {"column": "Channel", "value_set": ["web"]}, False),
    ("expect_column_values_to_not_be_in_set", {"column": "Channel", "value_set": ["x"]}, True),
    (
        "expect_column_distinct_values_to_be_in_set",
        {"column": "Channel", "value_set": ["web", "store", "phone"]},
        True,
    ),
    (
        "expect_column_distinct_values_to_contain_set",
        {"column": "Channel", "value_set": ["web"]},
        True,
    ),
    ("expect_column_values_to_match_regex", {"column": "CustomerEmail", "regex": "x"}, None),
    ("expect_column_values_to_not_match_regex", {"column": "CustomerEmail", "regex": "^b"}, None),
    (
        "expect_column_values_to_match_regex_list",
        {"column": "CustomerEmail", "regex_list": ["^a"], "match_on": "any"},
        None,
    ),
    (
        "expect_column_values_to_not_match_regex_list",
        {"column": "CustomerEmail", "regex_list": ["^z"]},
        None,
    ),
    (
        "expect_column_value_lengths_to_be_between",
        {"column": "Channel", "min_value": 3, "max_value": 5},
        True,
    ),
    ("expect_column_value_lengths_to_equal", {"column": "Channel", "value": 3}, False),
    ("expect_column_values_to_be_of_type", {"column": "Amount", "type_": "DECIMAL"}, True),
    (
        "expect_column_values_to_be_in_type_list",
        {"column": "OrderTsTz", "type_list": ["DATETIMEOFFSET"]},
        True,
    ),
    ("expect_compound_columns_to_be_unique", {"column_list": ["OrderId", "Channel"]}, True),
    (
        "expect_select_column_values_to_be_unique_within_record",
        {"column_list": ["CustomerEmail", "Channel"]},
        True,
    ),
    (
        "expect_column_pair_values_a_to_be_greater_than_b",
        {"column_A": "OrderTs", "column_B": "OrderDate"},
        True,
    ),
    (
        "expect_column_pair_values_to_be_equal",
        {"column_A": "OrderId", "column_B": "OrderId"},
        True,
    ),
    (
        "expect_column_pair_values_to_be_in_set",
        {
            "column_A": "Channel",
            "column_B": "IsGift",
            "value_pairs_set": [["web", False], ["store", True]],
        },
        False,
    ),
    (
        "expect_multicolumn_sum_to_equal",
        {"column_list": ["OrderId", "Amount"], "sum_total": 0},
        False,
    ),
    ("expect_table_row_count_to_be_between", {"min_value": 4, "max_value": 4}, True),
    (
        CUSTOM_SQL_EXPECTATION_TYPE,
        {"unexpected_rows_query": "SELECT TOP 10 * FROM {batch} WHERE [Amount] < 0"},
        False,
    ),
]


def test_the_cases_cover_every_type_a_sql_batch_can_run() -> None:
    covered = {expectation_type for expectation_type, _, _ in _CASES}
    assert covered == (ALLOWED_EXPECTATION_TYPES - DATAFRAME_ONLY_EXPECTATION_TYPES) | {
        CUSTOM_SQL_EXPECTATION_TYPE
    }


@pytest.mark.parametrize("auth", ["sql", "entra_service_principal"])
def test_every_sql_capable_type_runs_by_pushdown_with_the_expected_verdict(auth: str) -> None:
    config, secret = _credentials(auth)
    checks = [CheckSpec(expectation_type, kwargs) for expectation_type, kwargs, _ in _CASES]
    runner = _runner(config, secret)
    try:
        outcome = runner.run_checks(
            table="Orders",
            schema="dbo",
            checks=checks,
            index_columns=["OrderId"],
            value_signal_gate=lambda column: True,
        )
    finally:
        runner.close()
    got = {}
    for spec, result in zip(checks, outcome.checks, strict=True):
        got[spec.expectation_type] = "errored" if result.errored else result.success
    expected = {
        expectation_type: "errored" if verdict is None else verdict
        for expectation_type, _, verdict in _CASES
    }
    assert got == expected


def test_failing_rows_carry_the_identifier_column_and_driver_types_survive() -> None:
    runner = _runner()
    try:
        not_null, lengths = runner.run_checks(
            table="Orders",
            schema=None,  # the connection's schema: dbo
            checks=[
                CheckSpec("expect_column_values_to_not_be_null", {"column": "CustomerEmail"}),
                CheckSpec(
                    "expect_column_values_to_be_between",
                    {"column": "Amount", "min_value": 0, "max_value": 100},
                ),
            ],
            index_columns=["OrderId"],
        ).checks
    finally:
        runner.close()
    # GX hands a case-insensitive dialect's column names back as its own `CaseInsensitiveString`
    # (hashed case-folded); the persisted JSON is plain strings, which is what is compared.
    rows = (not_null.sample_failures or {})["unexpected_index_list"]
    assert [{str(k): v for k, v in row.items()} for row in rows] == [
        {"OrderId": 3, "CustomerEmail": None}
    ]
    assert not lengths.errored, lengths.error_message
    unexpected = (lengths.sample_failures or {})["partial_unexpected_list"]
    assert sorted(unexpected) == [Decimal("-3.00"), Decimal("99999.99")]


# ───────────────────────────── monitors ─────────────────────────────


@pytest.mark.parametrize("auth", ["sql", "entra_service_principal"])
def test_freshness_reads_datetimeoffset_datetime2_and_date_and_volume_counts(auth: str) -> None:
    config, secret = _credentials(auth)
    now = dt.datetime.now(dt.UTC)

    def _age(latest: dt.datetime) -> float:
        return max((now - latest).total_seconds() / 3600, 0.0)

    runner = _runner(config, secret)
    try:
        tz, naive, dated, not_a_time, volume = runner.run_monitors(
            table="Orders",
            schema="dbo",
            monitors=[
                MonitorSpec("freshness", {"column": "OrderTsTz"}),
                MonitorSpec("freshness", {"column": "OrderTs"}),
                MonitorSpec("freshness", {"column": "OrderDate"}),
                MonitorSpec("freshness", {"column": "OrderId"}),
                MonitorSpec("volume", {"min_rows": 1, "max_rows": 3}),
            ],
        )
    finally:
        runner.close()
    # MAX(OrderTsTz) is 08:00-07:00 = 15:00 UTC: the OFFSET must be honoured, so this reading
    # differs from the naive datetime2 column's 08:00 (read as UTC) by seven hours.
    assert tz.metric_value == pytest.approx(
        _age(dt.datetime(2026, 9, 27, 15, tzinfo=dt.UTC)), abs=0.1
    )
    assert naive.metric_value == pytest.approx(
        _age(dt.datetime(2026, 9, 27, 8, tzinfo=dt.UTC)), abs=0.1
    )
    assert dated.metric_value == pytest.approx(
        _age(dt.datetime(2026, 9, 27, tzinfo=dt.UTC)), abs=0.1
    )
    assert not_a_time.errored and "not a date/timestamp" in (not_a_time.error_message or "")
    assert volume.observed_value == {"row_count": 4, "deviation_pct": 33.333}


def test_the_anomaly_monitor_measures_row_count_and_freshness_age() -> None:
    from backend.app.datasources.monitors import anomaly_params
    from backend.app.services.anomaly import measure_metric

    connection, store, now = _connection(), _store(), dt.datetime.now(dt.UTC)
    rows = measure_metric(
        connection,
        table="Orders",
        schema="dbo",
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
        params=anomaly_params({"target_metric": "freshness_age_hours", "column": "OrderTs"}),
        secret_store=store,
        now=now,
    )
    assert rows == 4.0
    latest = dt.datetime(2026, 9, 27, 8, tzinfo=dt.UTC)
    assert age == pytest.approx(max((now - latest).total_seconds() / 3600, 0.0), abs=0.1)


# ───────────────────────────── introspection ─────────────────────────────


def test_the_profile_survives_a_bit_column_and_reads_decimals() -> None:
    """SQL Server has no MIN/MAX over bit (live-verified): that column reports min/max
    unavailable, and every other statistic stands.
    """
    profile = profile_service.profile_table(
        _connection(),
        table="Orders",
        schema="dbo",
        columns=["Amount", "IsGift", "Channel", "OrderTsTz", "Notes"],
        top_n=2,
        secret_store=_store(),
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 4
    assert by_column["Amount"].min_value == -3.0 and by_column["Amount"].max_value == 99999.99
    assert by_column["IsGift"].min_value is None and by_column["IsGift"].max_value is None
    assert by_column["IsGift"].distinct_count == 2 and by_column["IsGift"].null_count == 1
    assert by_column["Channel"].top_values[0] == {"value": "web", "count": 2}
    assert by_column["OrderTsTz"].max_value is not None
    assert by_column["Notes"].distinct_count == 3


def test_columns_schema_drift_and_comparison_reads() -> None:
    connection, store = _connection(), _store()
    columns = profile_service.list_table_columns(
        connection, table="Orders", schema=None, secret_store=store
    )
    assert columns[:3] == ["OrderId", "CustomerEmail", "Channel"]
    snapshot = schema_drift.introspect_columns(
        connection, table="Orders", schema="dbo", catalog=None, secret_store=store
    )
    assert {"name": "OrderTsTz", "type": "datetimeoffset"} in snapshot
    assert {"name": "IsGift", "type": "bit"} in snapshot
    by_table = read_dataset(
        connection, DatasetSpec(table="Orders", schema="dbo"), max_rows=10, secret_store=store
    )
    by_query = read_dataset(
        connection,
        DatasetSpec(query="SELECT [OrderId], [Channel] FROM dbo.Orders WHERE [OrderId] > 1"),
        max_rows=10,
        secret_store=store,
    )
    assert len(by_table) == 4 and len(by_query) == 3


def test_enumeration_and_browse_list_only_readable_tables() -> None:
    connection, store = _connection(), _store()
    with profile_service._open_connection(connection, store) as conn:
        schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
        identities = get_table_enumerator("mssql").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=_sql_config()
        )
    # The fixed-role schemas every database carries (db_datareader, …) are never offered.
    assert schemas == ["dbo"]
    names = {identity.name for identity in identities}
    database = _env("DATABASE")
    assert f"{database}.dbo.Orders" in names
    assert not any(".sys." in name for name in names)
    target = resolve_asset_identity("mssql", _sql_config(), {"table": "Orders"})
    assert (target.namespace, target.name) in {(i.namespace, i.name) for i in identities}


def test_browse_walks_schemas_then_tables(db_session: Any) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"ms-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"ms-{uuid.uuid4().hex[:6]}",
        type="mssql",
        env="dev",
        config=_sql_config(),
        secret_ref="mssql-ref",
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    kwargs: dict[str, Any] = {"session": db_session, "limit": 50, "secret_store": _store()}
    top = browse_service.browse_catalog(connection, catalog=None, schema=None, **kwargs)
    tables = browse_service.browse_catalog(connection, catalog=None, schema="dbo", **kwargs)
    assert top.level == "schema" and [e.name for e in top.entries] == ["dbo"]
    assert tables.level == "table" and [e.name for e in tables.entries] == ["Orders"]


# ───────────────────────────── end to end ─────────────────────────────


def test_a_suite_run_persists_results_end_to_end(db_session: Any) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"ms-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"ms-{uuid.uuid4().hex[:6]}",
        type="mssql",
        env="dev",
        config=_sql_config(),
        secret_ref="mssql-ref",
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(name="mssql", connection_id=connection.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    checks = [
        Check(
            suite_id=suite.id,
            name="email_notnull",
            kind="expectation",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "CustomerEmail"},
        ),
        Check(
            suite_id=suite.id,
            name="amount_range",
            kind="expectation",
            expectation_type="expect_column_values_to_be_between",
            config={"column": "Amount", "min_value": 0, "max_value": 100},
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
            config={"column": "OrderTsTz", "max_age_hours": 1000000},
        ),
    ]
    db_session.add_all(checks)
    db_session.flush()
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()

    runner = _runner()
    try:
        run_service.execute_run(
            db_session, run=run, checks=checks, runner=runner, table="Orders", schema="dbo"
        )
    finally:
        runner.close()

    assert run.status == "succeeded"
    by_check = {
        r.check_id: r for r in db_session.scalars(select(Result).where(Result.run_id == run.id))
    }
    assert by_check[checks[0].id].status == "fail"
    # A failing NUMERIC check's Decimal samples must reach the results JSONB (#1273 class).
    assert by_check[checks[1].id].status == "fail"
    assert by_check[checks[2].id].status == "pass"
    assert by_check[checks[2].id].observed_value == {"row_count": 4, "deviation_pct": 0.0}
    assert by_check[checks[3].id].status == "pass"
    assert by_check[checks[3].id].metric_value is not None


# ───────────────────────────── Fabric (ODBC lane) ─────────────────────────────

_FABRIC_HOST = _env("FABRIC_HOST")
_needs_fabric = pytest.mark.skipif(
    not (ODBC and _FABRIC_HOST and _env("CLIENT_SECRET")),
    reason="Fabric runs on the ODBC lane with a service principal",
)


def _fabric(database: str | None) -> tuple[dict[str, Any], str]:
    secret = _env("CLIENT_SECRET")
    assert secret is not None and database is not None
    return {**_sp_config(), "host": _FABRIC_HOST, "database": database}, secret


# (database env var, table, the columns the battery reads) — the Warehouse keeps the Azure SQL
# names; the Lakehouse table was loaded from a CSV, so it is lower case with `float` amounts.
_FABRIC_ITEMS = {
    "warehouse": ("FABRIC_DATABASE", "Orders", "OrderId", "CustomerEmail", "Amount", "OrderTs"),
    "lakehouse": ("FABRIC_LAKEHOUSE", "orders", "order_id", "email", "amount", "order_ts"),
}


@_needs_fabric
@pytest.mark.parametrize("item", sorted(_FABRIC_ITEMS))
def test_fabric_item_battery(item: str, db_session: Any) -> None:
    """Test Connection, the SQL batch (incl. custom SQL), freshness/volume, the profiler,
    inventory/browse and a persisted run — against a Fabric SQL endpoint, on the ODBC lane.
    """
    env_db, table, key, email, amount, ts = _FABRIC_ITEMS[item]
    database = _env(env_db)
    if database is None:
        pytest.skip(f"{_ENV}{env_db} not set")
    config, secret = _fabric(database)
    for attempt in range(4):  # a Fabric endpoint's first login after idle can be slow
        try:
            get_connection_adapter("mssql").test(config, secret)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(15)

    store = FakeSecretStore({"mssql-ref": secret})
    runner: Any = build_check_runner(
        conn_type="mssql", config=config, secret_ref="mssql-ref", secret_store=store
    )
    try:
        checks = [
            CheckSpec("expect_column_values_to_not_be_null", {"column": email}),
            # Not uniqueness: GX builds a #temp table for it, which Fabric refuses (refused at
            # author time on a Fabric host — see FABRIC_TEMP_TABLE_TYPES).
            CheckSpec(
                "expect_column_values_to_be_in_set", {"column": key, "value_set": [1, 2, 3, 4]}
            ),
            CheckSpec(
                "expect_column_values_to_be_between",
                {"column": amount, "min_value": -10, "max_value": 100000},
            ),
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 4, "max_value": 4}),
            CheckSpec(
                CUSTOM_SQL_EXPECTATION_TYPE,
                {"unexpected_rows_query": f"SELECT * FROM {{batch}} WHERE [{amount}] < 0"},
            ),
        ]
        outcome = runner.run_checks(table=table, schema="dbo", checks=checks, index_columns=[key])
        got = [("errored" if r.errored else r.success) for r in outcome.checks]
        assert got == [False, True, True, True, False], [r.error_message for r in outcome.checks]
        fresh, volume = runner.run_monitors(
            table=table,
            schema="dbo",
            monitors=[
                MonitorSpec("freshness", {"column": ts}),
                MonitorSpec("volume", {"min_rows": 1, "max_rows": 10}),
            ],
        )
    finally:
        runner.close()
    now = dt.datetime.now(dt.UTC)
    latest = dt.datetime(2026, 9, 27, 8, tzinfo=dt.UTC)
    assert fresh.metric_value == pytest.approx((now - latest).total_seconds() / 3600, abs=0.2)
    assert volume.observed_value == {"row_count": 4, "deviation_pct": 0.0}

    connection = Connection(
        id=uuid.uuid4(),
        name="fabric",
        type="mssql",
        env="dev",
        config=config,
        secret_ref="mssql-ref",
    )
    profile = profile_service.profile_table(
        connection,
        table=table,
        schema="dbo",
        columns=[amount, email],
        top_n=2,
        secret_store=store,
    )
    by_column = {c.column: c for c in profile.columns}
    assert profile.row_count == 4
    assert float(by_column[amount].min_value) == -3.0
    assert by_column[email].distinct_count == 2
    with profile_service._open_connection(connection, store) as conn:
        schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
        identities = get_table_enumerator("mssql").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=config
        )
    # Fabric's own `queryinsights` / `sys` views are never offered.
    assert schemas == ["dbo"]
    assert {i.name for i in identities} == {f"{database}.dbo.{table}"}

    owner = User(aad_object_id=uuid.uuid4().hex, email=f"fb-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    stored = Connection(
        name=f"fb-{uuid.uuid4().hex[:6]}",
        type="mssql",
        env="dev",
        config=config,
        secret_ref="mssql-ref",
        created_by=owner.id,
    )
    db_session.add(stored)
    db_session.flush()
    suite = Suite(name="fabric", connection_id=stored.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    persisted = [
        Check(
            suite_id=suite.id,
            name="email",
            kind="expectation",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": email},
        ),
        Check(
            suite_id=suite.id,
            name="rows",
            kind="volume",
            expectation_type="monitor:volume",
            config={"min_rows": 1, "max_rows": 100},
        ),
    ]
    db_session.add_all(persisted)
    db_session.flush()
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()
    runner = build_check_runner(
        conn_type="mssql", config=config, secret_ref="mssql-ref", secret_store=store
    )
    try:
        run_service.execute_run(
            db_session, run=run, checks=persisted, runner=runner, table=table, schema="dbo"
        )
    finally:
        runner.close()
    assert run.status == "succeeded"
    statuses = {
        r.check_id: r.status
        for r in db_session.scalars(select(Result).where(Result.run_id == run.id))
    }
    assert statuses == {persisted[0].id: "fail", persisted[1].id: "pass"}
