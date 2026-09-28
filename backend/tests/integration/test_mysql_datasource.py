"""The MySQL / MariaDB datasource (#1684), executed for real against live servers.

Opt-in: set ``MYSQL_TEST_URLS`` to comma-separated admin URLs, one per server, e.g.
``mysql+pymysql://root@127.0.0.1:3307,mysql+pymysql://root@127.0.0.1:3308`` (a ``mysql:8`` and a
``mariadb:11`` container). A server without TLS (the ``mariadb:10.6`` image) takes
``?dq_sslmode=disable``; the TLS tests then skip for it. Every test runs once per server.
Nothing here is mocked: values cross the real PyMySQL driver boundary (#953), and every DataQ
path runs as a freshly created least-privileged user, never the admin.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError

from backend.app.datasources import generic_sql
from backend.app.datasources.base import CheckSpec, MonitorSpec
from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
)
from backend.app.datasources.registry import build_check_runner, get_connection_adapter
from backend.app.datasources.sql_engines import SQL_ENGINES
from backend.app.db.models import Connection
from backend.app.lineage.warehouse import get_table_enumerator
from backend.app.services import profile_service, schema_drift
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE
from backend.app.services.dataset_reader import DatasetSpec, read_dataset
from backend.app.services.failure_classifier import (
    FailureCategory,
    classify_failure_category,
    is_auth_failure,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

_URLS = [u.strip() for u in os.environ.get("MYSQL_TEST_URLS", "").split(",") if u.strip()]
pytestmark = pytest.mark.skipif(not _URLS, reason="requires MYSQL_TEST_URLS (live MySQL/MariaDB)")

_SPEC = SQL_ENGINES["mysql"]


@dataclass(frozen=True)
class MyTarget:
    config: dict[str, Any]
    password: str = field(repr=False)  # never in a failure report
    database: str
    hidden: str
    no_temp_user: str
    is_mariadb: bool
    admin_url: Any


def _seed(db: str, hidden: str) -> list[str]:
    return [
        f"CREATE DATABASE `{db}`",
        f"CREATE DATABASE `{hidden}`",
        f"CREATE TABLE `{hidden}`.secret_stuff (x INT)",
        # Fixed UTC instants, so TIMESTAMP (stored UTC, returned in the session zone) and DATETIME
        # (stored literally) are comparable to the freshness math's own UTC clock.
        "SET time_zone = '+00:00'",
        f"CREATE TABLE `{db}`.`Orders` (order_id INT PRIMARY KEY, `CustomerId` INT,"
        " status VARCHAR(20), amount DECIMAL(12,2), placed_at TIMESTAMP NULL,"
        " shipped_local DATETIME NULL, order_date DATE, email VARCHAR(100), payload JSON,"
        " is_gift BOOLEAN)",
        f"INSERT INTO `{db}`.`Orders` VALUES"
        " (1, 10, 'new', 19.99, UTC_TIMESTAMP() - INTERVAL 2 HOUR,"
        " UTC_TIMESTAMP() - INTERVAL 3 HOUR, UTC_DATE(), 'ann@example.com', '{\"a\": 1}', 0),"
        " (2, 11, 'shipped', 5.00, UTC_TIMESTAMP() - INTERVAL 3 HOUR,"
        " UTC_TIMESTAMP() - INTERVAL 4 HOUR, UTC_DATE() - INTERVAL 1 DAY, 'bob@example.com',"
        " '{\"a\": 2}', 1),"
        " (3, NULL, 'shipped', 250.10, UTC_TIMESTAMP() - INTERVAL 5 HOUR, NULL,"
        " UTC_DATE() - INTERVAL 2 DAY, NULL, NULL, NULL),"
        " (4, 12, 'cancelled', -3.50, UTC_TIMESTAMP() - INTERVAL 30 HOUR,"
        " UTC_TIMESTAMP() - INTERVAL 31 HOUR, UTC_DATE() - INTERVAL 3 DAY, 'cy@example.com',"
        " '[]', 0),"
        " (5, 12, 'bogus', 42.00, UTC_TIMESTAMP() - INTERVAL 24 HOUR,"
        " UTC_TIMESTAMP() - INTERVAL 25 HOUR, UTC_DATE() - INTERVAL 1 DAY, 'dee@example.com',"
        " '{\"a\": 5}', 0),"
        " (6, 13, NULL, 0.00, NULL, NULL, NULL, 'eve@example.com', '{\"a\": 6}', 1)",
        f"CREATE TABLE `{db}`.orders_lc (id INT, label VARCHAR(5))",
        f"INSERT INTO `{db}`.orders_lc VALUES (1, 'a'), (2, 'b'), (2, 'A')",
        f"CREATE VIEW `{db}`.big_orders AS SELECT * FROM `{db}`.`Orders` WHERE amount > 10",
    ]


@pytest.fixture(scope="module", params=_URLS or ["unset"])
def my(request: pytest.FixtureRequest) -> Iterator[MyTarget]:
    raw_url = make_url(request.param)
    sslmode = raw_url.query.get("dq_sslmode", "require")
    admin_url = raw_url.difference_update_query(["dq_sslmode"])
    suffix = uuid.uuid4().hex[:8]
    db, hidden = f"DqMy_{suffix}", f"dq_hidden_{suffix}"
    user, no_temp_user = f"dq_my_reader_{suffix}", f"dq_my_notemp_{suffix}"
    # URL-hostile on purpose: the adapter must escape it into the DSN, not break on it.
    password = secrets.token_urlsafe(18) + "@:/?#%"
    admin = create_engine(admin_url)
    try:
        with admin.begin() as conn:
            # A server zone that is NOT UTC: without the session's own `time_zone = '+00:00'` a
            # TIMESTAMP would come back five hours off and every freshness age would be wrong.
            conn.execute(text("SET GLOBAL time_zone = '+05:00'"))
            for statement in _seed(db, hidden):
                conn.execute(text(statement))
            for name, grants in (
                (user, "SELECT, SHOW VIEW, CREATE TEMPORARY TABLES"),
                (no_temp_user, "SELECT, SHOW VIEW"),
            ):
                conn.execute(
                    text(f"CREATE USER '{name}'@'%' IDENTIFIED BY :pw").bindparams(pw=password)
                )
                conn.execute(text(f"GRANT {grants} ON `{db}`.* TO '{name}'@'%'"))
            version = conn.execute(text("SELECT VERSION()")).scalar_one()
        config = {
            "host": admin_url.host or "127.0.0.1",
            "port": admin_url.port or 3306,
            "database": db,
            "user": user,
            # The containers serve self-signed certificates: `require` encrypts, verify-* fail.
            "sslmode": sslmode,
        }
        yield MyTarget(
            config, password, db, hidden, no_temp_user, "mariadb" in version.lower(), admin_url
        )
    finally:
        with admin.begin() as conn:
            conn.execute(text("SET GLOBAL time_zone = 'SYSTEM'"))
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db}`"))
            conn.execute(text(f"DROP DATABASE IF EXISTS `{hidden}`"))
            conn.execute(text(f"DROP USER IF EXISTS '{user}'@'%'"))
            conn.execute(text(f"DROP USER IF EXISTS '{no_temp_user}'@'%'"))
        admin.dispose()


def _store(my: MyTarget) -> FakeSecretStore:
    return FakeSecretStore({"my-ref": my.password})


def _runner(my: MyTarget, **overrides: Any) -> Any:
    return build_check_runner(
        conn_type="mysql",
        config={**my.config, **overrides},
        secret_ref="my-ref",
        secret_store=_store(my),
    )


def _connection(my: MyTarget) -> Connection:
    return Connection(
        id=uuid.uuid4(), name="my", type="mysql", env="dev", config=my.config, secret_ref="my-ref"
    )


def _run(my: MyTarget, table: str, checks: list[CheckSpec], **kwargs: Any) -> Any:
    runner = _runner(my, **kwargs.pop("config", {}))
    try:
        return runner.run_checks(table=table, schema=my.database, checks=checks, **kwargs)
    finally:
        runner.close()


# ───────────────────────────── connection ─────────────────────────────


def test_a_real_login_over_tls_and_a_wrong_password(my: MyTarget) -> None:
    adapter = get_connection_adapter("mysql")
    adapter.test(my.config, my.password)
    with pytest.raises(DBAPIError) as exc:
        adapter.test(my.config, my.password + "x")
    assert is_auth_failure(exc.value)
    assert classify_failure_category(exc.value) is FailureCategory.PERMISSION


def test_verified_tls_refuses_a_self_signed_server(my: MyTarget) -> None:
    if my.config["sslmode"] == "disable":
        pytest.skip("this server has no TLS")
    for mode in ("verify-ca", "verify-full"):
        with pytest.raises(DBAPIError, match="CERTIFICATE_VERIFY_FAILED") as exc:
            get_connection_adapter("mysql").test({**my.config, "sslmode": mode}, my.password)
        assert classify_failure_category(exc.value) is FailureCategory.CONNECTIVITY


def test_a_database_the_user_has_no_grant_on_is_a_permission_failure_not_a_dead_login(
    my: MyTarget,
) -> None:
    with pytest.raises(DBAPIError) as exc:
        get_connection_adapter("mysql").test({**my.config, "database": my.hidden}, my.password)
    # 1044 is a missing grant — the credential itself is fine, so credential health must not flip.
    assert classify_failure_category(exc.value) is FailureCategory.PERMISSION
    assert not is_auth_failure(exc.value)


def test_every_session_is_read_only_utc_and_encrypted(my: MyTarget) -> None:
    engine = _SPEC.create_engine(_SPEC.validate_config(my.config), my.password)
    try:
        with engine.connect() as conn:
            # Read-only-ness is proven by the refused writes below, not a variable: its name
            # differs across versions (`tx_read_only` / `transaction_read_only`).
            zone, database = conn.execute(text("SELECT @@session.time_zone, DATABASE()")).one()
            cipher = conn.execute(text("SHOW SESSION STATUS LIKE 'Ssl_cipher'")).one()[1]
        assert (zone, database) == ("+00:00", my.database)
        # `require` negotiated TLS; `disable` (a server without TLS) did not.
        assert bool(cipher) is (my.config["sslmode"] != "disable")
        for statement in (
            "CREATE TEMPORARY TABLE dq_probe (i INT)",
            "CREATE TABLE dq_probe (i INT)",
        ):
            with pytest.raises(DBAPIError, match="READ ONLY"):
                with engine.begin() as conn:
                    conn.execute(text(statement))
    finally:
        engine.dispose()


# ───────────────────────────── expectations ─────────────────────────────

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
    # A different type vocabulary from PostgreSQL's: GX matches the SQLAlchemy type CLASS here.
    ("expect_column_values_to_be_of_type", {"column": "amount", "type_": "DECIMAL"}, True),
    (
        "expect_column_values_to_be_in_type_list",
        {"column": "placed_at", "type_list": ["TIMESTAMP"]},
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
            # BOOLEAN is TINYINT(1): the stored values are 0/1.
            "value_pairs_set": [["new", 0], ["shipped", 1]],
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
        {"unexpected_rows_query": "SELECT * FROM {batch} WHERE amount < 0 OR CustomerId IS NULL"},
        False,
    ),
]


def test_the_cases_cover_every_type_a_sql_batch_can_run() -> None:
    covered = {expectation_type for expectation_type, _, _ in _CASES}
    assert covered == (ALLOWED_EXPECTATION_TYPES - DATAFRAME_ONLY_EXPECTATION_TYPES) | {
        CUSTOM_SQL_EXPECTATION_TYPE
    }


def test_every_sql_capable_type_runs_with_the_expected_verdict(my: MyTarget) -> None:
    checks = [CheckSpec(expectation_type, kwargs) for expectation_type, kwargs, _ in _CASES]
    outcome = _run(
        my, "Orders", checks, index_columns=["order_id"], value_signal_gate=lambda column: True
    )
    got = {
        spec.expectation_type: (result.errored, result.error_message, result.success)
        for spec, result in zip(checks, outcome.checks, strict=True)
    }
    assert got == {
        expectation_type: (False, None, verdict) for expectation_type, _, verdict in _CASES
    }


def test_uniqueness_runs_off_the_read_only_session_and_reports_its_rows(my: MyTarget) -> None:
    """GX's MySQL uniqueness check builds session TEMPORARY tables, which a read-only transaction
    refuses — it errored on every run until it got its own session (live-found)."""
    [result] = _run(
        my,
        "Orders",
        [CheckSpec("expect_column_values_to_be_unique", {"column": "CustomerId"})],
        index_columns=["order_id"],
    ).checks
    assert not result.errored, result.error_message
    assert (result.sample_failures or {})["unexpected_percent"] == 40.0
    assert (result.sample_failures or {})["unexpected_index_list"] == [
        {"order_id": 4, "CustomerId": 12},
        {"order_id": 5, "CustomerId": 12},
    ]


def test_uniqueness_follows_the_column_collation(my: MyTarget) -> None:
    """A case-insensitive collation makes 'a' and 'A' duplicates — MySQL's semantics, stated in
    the docs rather than second-guessed."""
    [result] = _run(
        my, "orders_lc", [CheckSpec("expect_column_values_to_be_unique", {"column": "label"})]
    ).checks
    assert not result.errored, result.error_message
    assert result.success is False


def test_without_the_temporary_table_grant_only_uniqueness_errors(my: MyTarget) -> None:
    checks = [
        CheckSpec("expect_column_values_to_be_unique", {"column": "CustomerId"}),
        CheckSpec("expect_column_values_to_not_be_null", {"column": "email"}),
    ]
    unique, not_null = _run(my, "Orders", checks, config={"user": my.no_temp_user}).checks
    assert unique.errored
    assert not not_null.errored and not_null.success is False


def test_the_session_statements_reach_every_session_dataq_opens(my: MyTarget) -> None:
    """GX builds its own engine and the profiler its own — both must carry the session statements.
    The server's zone is +05:00, so a session that missed them shows it."""
    [gx_session] = _run(
        my,
        "Orders",
        [
            CheckSpec(
                CUSTOM_SQL_EXPECTATION_TYPE,
                {
                    "unexpected_rows_query": (
                        "SELECT * FROM {batch} WHERE @@session.time_zone <> '+00:00'"
                    )
                },
            )
        ],
    ).checks
    assert not gx_session.errored, gx_session.error_message
    assert gx_session.success is True
    with profile_service._open_connection(_connection(my), _store(my)) as conn:
        assert conn.execute(text("SELECT @@session.time_zone")).scalar() == "+00:00"
        with pytest.raises(DBAPIError, match="READ ONLY"):
            conn.execute(text("CREATE TEMPORARY TABLE dq_probe (i INT)"))


def test_a_finished_run_leaves_no_server_session_open(my: MyTarget) -> None:
    for _ in range(3):
        _run(
            my,
            "Orders",
            [
                CheckSpec("expect_column_values_to_not_be_null", {"column": "email"}),
                CheckSpec("expect_column_values_to_be_unique", {"column": "CustomerId"}),
            ],
        )
    engine = create_engine(my.admin_url)
    try:
        with engine.connect() as conn:
            sessions = conn.execute(
                text("SELECT count(*) FROM information_schema.processlist WHERE user = :user"),
                {"user": my.config["user"]},
            ).scalar_one()
    finally:
        engine.dispose()
    assert sessions == 0


def test_a_lower_case_table_and_a_case_insensitive_column(my: MyTarget) -> None:
    [result] = _run(
        my,
        "Orders",
        # MySQL column names are case-insensitive, so this spelling finds `CustomerId`.
        [CheckSpec("expect_column_values_to_not_be_null", {"column": "customerid"})],
    ).checks
    assert not result.errored, result.error_message
    assert result.success is False  # row 3 has no customer


def test_a_table_in_the_wrong_case_is_a_config_error(my: MyTarget) -> None:
    """Table names ARE case-sensitive on a Linux server (lower_case_table_names=0)."""
    with pytest.raises(Exception) as exc:
        _run(my, "ORDERS", [CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0})])
    assert classify_failure_category(exc.value) is FailureCategory.CONFIG


# ───────────────────────────── monitors ─────────────────────────────


def test_freshness_over_timestamp_datetime_and_date_and_volume(my: MyTarget) -> None:
    monitors = [
        MonitorSpec("freshness", {"column": "placed_at"}),
        MonitorSpec("freshness", {"column": "shipped_local"}),
        MonitorSpec("freshness", {"column": "order_date"}),
        MonitorSpec("volume", {"min_rows": 1, "max_rows": 5}),
    ]
    runner = _runner(my)
    try:
        placed, shipped, dated, volume = runner.run_monitors(
            table="Orders", schema=my.database, monitors=monitors
        )
        [unqualified] = runner.run_monitors(
            table="Orders",
            schema=None,
            monitors=[MonitorSpec("volume", {"min_rows": 0, "max_rows": 9})],
        )
    finally:
        runner.close()
    # The TIMESTAMP comes back in the session's UTC zone, so the age is the seeded 2 hours.
    assert placed.metric_value is not None and 1.9 < placed.metric_value < 2.5
    assert shipped.metric_value is not None and 2.9 < shipped.metric_value < 3.5
    assert dated.metric_value is not None and 0 <= dated.metric_value <= 24
    assert volume.observed_value == {"row_count": 6, "deviation_pct": 20.0}
    assert unqualified.observed_value == {"row_count": 6, "deviation_pct": 0.0}


# ───────────────────────────── introspection ─────────────────────────────


def test_profile_columns_drift_and_comparison_reads(my: MyTarget) -> None:
    connection, store = _connection(my), _store(my)
    profile = profile_service.profile_table(
        connection,
        table="Orders",
        schema=my.database,
        columns=["amount", "payload", "is_gift"],
        top_n=2,
        secret_store=store,
    )
    by_column = {column.column: column for column in profile.columns}
    assert profile.row_count == 6
    assert (by_column["amount"].min_value, by_column["amount"].max_value) == (-3.5, 250.1)
    assert by_column["payload"].distinct_count == 5
    assert (by_column["is_gift"].min_value, by_column["is_gift"].max_value) == (0, 1)
    columns = profile_service.list_table_columns(
        connection, table="Orders", schema=None, secret_store=store
    )
    assert columns[:3] == ["order_id", "CustomerId", "status"]
    snapshot = schema_drift.introspect_columns(
        connection, table="Orders", schema=my.database, catalog=None, secret_store=store
    )
    # MariaDB's JSON is an alias for LONGTEXT; MySQL has a native JSON type.
    payload = {"name": "payload", "type": "longtext" if my.is_mariadb else "json"}
    assert payload in snapshot
    with pytest.raises(schema_drift.SchemaIntrospectionError, match="not found"):
        schema_drift.introspect_columns(
            connection, table="ORDERS", schema=my.database, catalog=None, secret_store=store
        )
    frame = read_dataset(
        connection, DatasetSpec(table="Orders", schema=my.database), max_rows=10, secret_store=store
    )
    assert len(frame) == 6


def test_enumeration_is_privilege_filtered_and_joins_the_suite_target_identity(
    my: MyTarget,
) -> None:
    connection, store = _connection(my), _store(my)
    with profile_service._open_connection(connection, store) as conn:
        schemas = generic_sql.schema_names(_SPEC, conn, limit=None)
        tables = generic_sql.table_rows(_SPEC, conn, schema=my.database, limit=None)
        typed = generic_sql.typed_table_rows(_SPEC, conn, schema=my.database, limit=None)
        identities = get_table_enumerator("mysql").enumerate_tables(  # type: ignore[union-attr]
            conn, connection_config=my.config
        )
    assert my.database in schemas and my.hidden not in schemas
    assert sorted(t for _, t in tables) == ["Orders", "big_orders", "orders_lc"]
    assert sorted((t, kind) for _, t, kind in typed) == [
        ("Orders", "table"),
        ("big_orders", "view"),
        ("orders_lc", "table"),
    ]
    target = resolve_asset_identity("mysql", my.config, {"table": "Orders"})
    assert target.name == f"{my.database}.Orders"
    assert (target.namespace, target.name) in {(i.namespace, i.name) for i in identities}
