"""The MySQL / MariaDB spec on the generic SQL base (#1684) — the pure, server-free half.

The driver-boundary behaviour runs live in `tests/integration/test_mysql_datasource.py`.
"""

from __future__ import annotations

import importlib.metadata
import ssl
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from backend.app.datasources import registry
from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome, TargetShapeError
from backend.app.datasources.engines import engines_for
from backend.app.datasources.generic_sql import GenericSqlCheckRunner, _dispose_gx_engine
from backend.app.datasources.mysql import MYSQL, MySqlConfig
from backend.app.datasources.sql_engines import (
    GENERIC_SQL_TYPES,
    SQL_BATCH_CONNECTION_TYPES,
    default_schema,
)
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE, SQL_QUERYABLE_TYPES
from backend.app.services.failure_classifier import (
    FailureCategory,
    classify_failure_category,
    is_auth_failure,
)
from backend.app.services.run_admission import PUSHDOWN_TYPES

_BASE: dict[str, Any] = {"host": "db.internal", "database": "Shop", "user": "dq_reader"}


def _config(**overrides: Any) -> MySqlConfig:
    config = MYSQL.validate_config({**_BASE, **overrides})
    assert isinstance(config, MySqlConfig)
    return config


def test_the_driver_is_the_mit_licensed_one() -> None:
    """GPL drivers (mysqlclient, mysql-connector-python) are excluded by ADR 0031."""
    assert MYSQL.drivername == "mysql+pymysql"
    assert importlib.metadata.metadata("PyMySQL")["License-Expression"] == "MIT"


def test_defaults_are_the_safe_ones_and_a_schema_is_a_database() -> None:
    config = _config()
    assert config.sslmode == "require"
    assert config.effective_port == 3306
    assert config.default_schema == "Shop"
    assert config.url_database() == "Shop"
    # An alternative default schema IS the database the session connects to.
    assert _config(schema="Other").url_database() == "Other"


def test_the_identifier_limit_is_mysqls() -> None:
    assert _config(schema="s" * 64).default_schema == "s" * 64
    with pytest.raises(ValidationError, match="at most 64"):
        _config(schema="s" * 65)
    with pytest.raises(TargetShapeError):
        registry.resolve_target_shape("mysql", {"table": "t" * 65})
    assert registry.resolve_target_shape("mysql", {"table": "t" * 64}).table == "t" * 64


def test_the_url_escapes_the_password_and_connects_to_the_scoped_database() -> None:
    password = "p@ss:w/rd?#%&"
    parsed = make_url(MYSQL.url_string(MYSQL.scoped_config(_config(), "Other"), password))
    assert parsed.password == password
    assert (parsed.database, parsed.port, parsed.drivername) == ("Other", 3306, "mysql+pymysql")


def test_the_session_is_read_only_and_utc_unless_a_temp_table_type_asks_otherwise() -> None:
    args = MYSQL.connect_args(_config(), 10)
    assert args["init_command"] == (
        "SET SESSION time_zone = '+00:00', SESSION transaction_read_only = 1"
    )
    assert args["connect_timeout"] == 10 and args["charset"] == "utf8mb4"
    writable = MYSQL.connect_args(_config(), None, read_only=False)
    assert writable["init_command"] == "SET SESSION time_zone = '+00:00'"
    assert "connect_timeout" not in writable
    assert MYSQL.temp_table_types == {"expect_column_values_to_be_unique"}


def test_tls_is_required_unless_explicitly_disabled() -> None:
    assert MYSQL.connect_args(_config(sslmode="disable"), None)["ssl_disabled"] is True
    require = MYSQL.connect_args(_config(), None)["ssl"]
    # An explicit context, never "no options" — PyMySQL would then fall back to plaintext.
    assert isinstance(require, ssl.SSLContext)
    assert require.verify_mode is ssl.CERT_NONE and not require.check_hostname
    verify_ca = MYSQL.connect_args(_config(sslmode="verify-ca"), None)["ssl"]
    assert verify_ca.verify_mode is ssl.CERT_REQUIRED and not verify_ca.check_hostname
    verify_full = MYSQL.connect_args(_config(sslmode="verify-full"), None)["ssl"]
    assert verify_full.verify_mode is ssl.CERT_REQUIRED and verify_full.check_hostname


def test_the_asset_identity_is_the_openlineage_mysql_shape() -> None:
    identity = resolve_asset_identity("mysql", {**_BASE, "port": 3310}, {"table": "Orders"})
    assert identity.namespace == "mysql://db.internal:3310"
    assert identity.name == "Shop.Orders"
    other = resolve_asset_identity("mysql", _BASE, {"table": "t", "schema": "Archive"})
    assert other.name == "Archive.t"


def test_mysql_is_registered_everywhere_a_sql_datasource_must_be() -> None:
    assert GENERIC_SQL_TYPES == {"postgres", "mysql"}
    assert registry.destination_fields("mysql") == {"secret": ("host", "port")}
    for capability in (SQL_QUERYABLE_TYPES, SQL_BATCH_CONNECTION_TYPES, PUSHDOWN_TYPES):
        assert "mysql" in capability
    assert engines_for("mysql") == {"gx"}
    assert default_schema("mysql", _BASE) == "Shop"


@pytest.mark.parametrize(
    ("message", "category", "auth"),
    [
        (
            "(1045, \"Access denied for user 'u'@'10.0.0.1' (using password: YES)\")",
            FailureCategory.PERMISSION,
            True,
        ),
        # 1044 is a missing grant on a database, not a dead credential.
        (
            "(1044, \"Access denied for user 'u'@'%' to database 'd'\")",
            FailureCategory.PERMISSION,
            False,
        ),
        (
            "(2003, \"Can't connect to MySQL server on 'h' ([Errno 61] Connection refused)\")",
            FailureCategory.CONNECTIVITY,
            False,
        ),
        (
            '(2026, "SSL is required but the server doesn\'t support it")',
            FailureCategory.CONNECTIVITY,
            False,
        ),
        ("(1146, \"Table 'd.t' doesn't exist\")", FailureCategory.CONFIG, False),
    ],
)
def test_mysql_failures_are_classified(message: str, category: FailureCategory, auth: bool) -> None:
    exc = RuntimeError(message)
    assert classify_failure_category(exc) is category
    assert is_auth_failure(exc) is auth


class TestTempTableSplit:
    """The run path's routing, with the GX batch stubbed: which session each check lands on."""

    def _runner(self, calls: list[tuple[bool, list[str]]]) -> GenericSqlCheckRunner:
        runner = GenericSqlCheckRunner(MYSQL, _config(), "pw")

        def fake_batch(*, checks: list[CheckSpec], read_only: bool, **_: Any) -> SuiteOutcome:
            calls.append((read_only, [c.expectation_type for c in checks]))
            return SuiteOutcome(
                success=read_only,
                checks=[CheckOutcome(c.expectation_type, success=read_only) for c in checks],
            )

        runner._run_batch = fake_batch  # type: ignore[method-assign]
        return runner

    def test_only_the_temp_table_type_leaves_the_read_only_session(self) -> None:
        calls: list[tuple[bool, list[str]]] = []
        checks = [
            CheckSpec("expect_column_values_to_not_be_null", {"column": "a"}),
            CheckSpec("expect_column_values_to_be_unique", {"column": "b"}),
            CheckSpec(CUSTOM_SQL_EXPECTATION_TYPE, {"unexpected_rows_query": "SELECT 1"}),
        ]
        outcome = self._runner(calls).run_checks(table="t", schema=None, checks=checks)
        # Custom SQL — the one place user-written SQL runs — stays on the guarded session.
        assert calls == [
            (True, ["expect_column_values_to_not_be_null", CUSTOM_SQL_EXPECTATION_TYPE]),
            (False, ["expect_column_values_to_be_unique"]),
        ]
        # Outcomes come back in submission order, and one failing group fails the suite.
        assert [o.expectation_type for o in outcome.checks] == [c.expectation_type for c in checks]
        assert outcome.success is False

    def test_a_suite_without_the_type_is_one_read_only_batch(self) -> None:
        calls: list[tuple[bool, list[str]]] = []
        checks = [CheckSpec("expect_column_values_to_not_be_null", {"column": "a"})]
        self._runner(calls).run_checks(table="t", schema=None, checks=checks)
        assert calls == [(True, ["expect_column_values_to_not_be_null"])]

    def test_a_suite_of_only_the_type_never_opens_a_guarded_batch(self) -> None:
        calls: list[tuple[bool, list[str]]] = []
        checks = [CheckSpec("expect_column_values_to_be_unique", {"column": "a"})]
        outcome = self._runner(calls).run_checks(table="t", schema=None, checks=checks)
        assert calls == [(False, ["expect_column_values_to_be_unique"])]
        assert outcome.success is False


def test_a_failed_engine_dispose_is_logged_not_raised() -> None:
    class _Broken:
        def get_engine(self) -> Any:
            raise RuntimeError("already gone")

    _dispose_gx_engine(_Broken())  # must not raise
