"""The engine-generic SQL base (#1678) and its PostgreSQL spec — the pure, server-free half.

What crosses the driver boundary is exercised for real in
`tests/integration/test_postgres_datasource.py`; this file pins config validation, the DSN, the
session settings and the registry wiring, including the adversarial inputs a config or run target
can carry.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from backend.app.datasources import registry
from backend.app.datasources.base import TargetShapeError
from backend.app.datasources.engines import engines_for
from backend.app.datasources.generic_sql import (
    GenericSqlCheckRunner,
    GenericSqlConnectionAdapter,
    build_generic_sql_runner,
    gx_table_name,
)
from backend.app.datasources.postgres import POSTGRES, PostgresConfig
from backend.app.datasources.sql_engines import (
    GENERIC_SQL_TYPES,
    SQL_BATCH_CONNECTION_TYPES,
    default_schema,
    sql_engine,
)
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.custom_sql import SQL_QUERYABLE_TYPES
from backend.app.services.failure_classifier import (
    FailureCategory,
    classify_failure_category,
    is_auth_failure,
)
from backend.app.services.run_admission import PUSHDOWN_TYPES
from backend.tests.support.fake_secret_store import FakeSecretStore

_BASE: dict[str, Any] = {"host": "db.internal", "database": "shop", "user": "dq_reader"}


def _config(**overrides: Any) -> PostgresConfig:
    return PostgresConfig.model_validate({**_BASE, **overrides})


# ───────────────────────────── config ─────────────────────────────


def test_defaults_are_the_safe_ones() -> None:
    config = _config()
    assert config.sslmode == "require"  # TLS on unless explicitly turned off
    assert config.effective_port == 5432
    assert config.default_schema == "public"
    assert config.inventory_sync is True


@pytest.mark.parametrize(
    "host",
    [
        "postgres://db.internal",  # a scheme
        "user:pw@db.internal",  # userinfo smuggled into the identity + DSN
        "db.internal:5432",  # a port — it has its own field
        "cafe:5432",  # a hex-only name with a port is NOT an IPv6 literal
        "[::1]",  # brackets belong to a URL authority, not to the host field
        "db.internal/other",  # a path
        "db internal",  # whitespace
        "db\x00internal",  # NUL
        "",
        "a" * 254,
    ],
)
def test_a_host_that_is_not_a_bare_name_or_address_is_refused(host: str) -> None:
    with pytest.raises(ValidationError, match="host must be a bare hostname"):
        _config(host=host)


@pytest.mark.parametrize("host", ["db.internal", "10.0.0.5", "::1", "fe80::1", "a" * 253])
def test_a_bare_name_or_address_is_accepted(host: str) -> None:
    assert _config(host=host).host == host


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_an_out_of_range_port_is_refused(port: int) -> None:
    with pytest.raises(ValidationError):
        _config(port=port)


@pytest.mark.parametrize("field", ["database", "user"])
@pytest.mark.parametrize("value", ["", "a\x00b", "line\nbreak", "x" * 129])
def test_database_and_user_refuse_control_characters_and_overlong_values(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        _config(**{field: value})


def test_the_schema_is_an_identifier_no_longer_than_postgres_resolves() -> None:
    assert _config(schema="S" * 63).default_schema == "S" * 63
    # 64 would be TRUNCATED by the server onto a different name — refused instead.
    with pytest.raises(ValidationError, match="at most 63"):
        _config(schema="S" * 64)


@pytest.mark.parametrize("schema", ['Sales"; drop', "sales schema", "9sales", "sa-les", "a.b"])
def test_a_schema_that_is_not_an_identifier_is_refused(schema: str) -> None:
    with pytest.raises(ValidationError, match="plain SQL identifier"):
        _config(schema=schema)


def test_plaintext_fallback_is_not_offered_and_unknown_keys_are_refused() -> None:
    # libpq's `prefer` silently downgrades to plaintext; DataQ offers disable or TLS, never "maybe".
    with pytest.raises(ValidationError):
        _config(sslmode="prefer")
    with pytest.raises(ValidationError):
        _config(password="in-the-config")  # the password is the connection's SECRET


# ───────────────────────────── DSN + session ─────────────────────────────


def test_the_password_is_escaped_into_the_url_and_survives_a_round_trip() -> None:
    password = "p@ss:w/rd?#%&"
    rendered = POSTGRES.url_string(_config(port=6543), password)
    parsed = make_url(rendered)
    assert parsed.password == password
    assert (parsed.host, parsed.port, parsed.database, parsed.username) == (
        "db.internal",
        6543,
        "shop",
        "dq_reader",
    )
    assert parsed.drivername == "postgresql+psycopg2"


def test_every_session_is_read_only_and_pinned_to_the_quoted_schema() -> None:
    args = POSTGRES.connect_args(_config(schema="Sales"), 10)
    assert args["options"] == (
        '-c default_transaction_read_only=on -c search_path=pg_catalog,"Sales",public'
    )
    assert args["connect_timeout"] == 10
    assert args["sslmode"] == "require"
    assert "sslrootcert" not in args


def test_public_is_not_repeated_when_it_is_the_connection_schema() -> None:
    assert POSTGRES.connect_args(_config(), None)["options"].endswith(
        'search_path=pg_catalog,"public"'
    )


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_cleared_optional_field_means_the_default_not_an_error(blank: str) -> None:
    config = _config(port=blank, schema=blank, sslmode=blank)
    assert (config.effective_port, config.default_schema, config.sslmode) == (
        5432,
        "public",
        "require",
    )


def test_a_session_asked_not_to_be_read_only_keeps_everything_else() -> None:
    options = POSTGRES.connect_args(_config(schema="Sales"), None, read_only=False)["options"]
    assert options == '-c search_path=pg_catalog,"Sales",public'


def test_the_run_path_session_has_no_login_timeout() -> None:
    assert "connect_timeout" not in POSTGRES.connect_args(_config(), None)


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_verified_tls_trusts_the_system_store(mode: str) -> None:
    args = POSTGRES.connect_args(_config(sslmode=mode), None)
    assert args["sslmode"] == mode
    assert args["sslrootcert"] == "system"


def test_scoping_a_session_to_a_target_schema_revalidates_it() -> None:
    config = _config(schema="Sales")
    assert POSTGRES.scoped_config(config, None) is config
    assert POSTGRES.scoped_config(config, "Other").default_schema == "Other"
    # The schema reaches the session's own settings: an injection must never get that far.
    with pytest.raises(ValidationError):
        POSTGRES.scoped_config(config, '"; -c default_transaction_read_only=off')


@pytest.mark.parametrize(
    ("table", "expected"),
    [("orders", "orders"), ("Orders", '"Orders"'), ("ORDERS", '"ORDERS"'), ("t$1", "t$1")],
)
def test_gx_gets_a_quoted_name_unless_the_name_is_lower_case(table: str, expected: str) -> None:
    assert gx_table_name(table) == expected


@pytest.mark.parametrize("table", ['x"y', "a.b", "", "1abc", "a b"])
def test_gx_never_gets_a_name_that_is_not_an_identifier(table: str) -> None:
    with pytest.raises(ValueError, match="invalid table identifier"):
        gx_table_name(table)


# ───────────────────────────── identity ─────────────────────────────


def test_the_asset_identity_is_the_openlineage_postgres_shape_with_parts_verbatim() -> None:
    identity = resolve_asset_identity(
        "postgres", {**_BASE, "host": "DB.Internal", "schema": "Sales"}, {"table": "Orders"}
    )
    assert identity.namespace == "postgres://db.internal:5432"
    assert identity.name == "shop.Sales.Orders"
    explicit = resolve_asset_identity("postgres", _BASE, {"table": "t", "schema": "s"})
    assert explicit.name == "shop.s.t"
    defaulted = resolve_asset_identity("postgres", _BASE, {"table": "t"})
    assert defaulted.name == "shop.public.t"


def test_an_ipv6_host_keeps_its_brackets_in_the_namespace() -> None:
    identity = resolve_asset_identity("postgres", {**_BASE, "host": "fe80::1"}, {"table": "t"})
    assert identity.namespace == "postgres://[fe80::1]:5432"


def test_an_identity_needs_a_table_and_a_valid_config() -> None:
    with pytest.raises(ValueError):
        resolve_asset_identity("postgres", _BASE, {})
    with pytest.raises(ValueError):
        resolve_asset_identity("postgres", {**_BASE, "host": "u:p@h"}, {"table": "t"})


# ───────────────────────────── registry wiring ─────────────────────────────


def test_postgres_is_registered_everywhere_a_sql_datasource_must_be() -> None:
    assert "postgres" in GENERIC_SQL_TYPES
    assert sql_engine("postgres") is POSTGRES
    assert sql_engine("snowflake") is None
    adapter = registry.get_connection_adapter("postgres")
    assert isinstance(adapter, GenericSqlConnectionAdapter)
    # #1401: the host AND port decide where the password is sent.
    assert registry.destination_fields("postgres") == {"secret": ("host", "port")}
    assert "postgres" in SQL_QUERYABLE_TYPES
    assert "postgres" in SQL_BATCH_CONNECTION_TYPES
    assert "postgres" in PUSHDOWN_TYPES
    assert engines_for("postgres") == {"gx"}  # no native DQ engine to offer (ADR 0036)


def test_default_schema_falls_back_to_the_engine_default() -> None:
    assert default_schema("postgres", _BASE) == "public"
    assert default_schema("postgres", {**_BASE, "schema": "Sales"}) == "Sales"
    assert default_schema("snowflake", {"schema": "X"}) is None


def test_the_runner_builder_requires_a_stored_password() -> None:
    with pytest.raises(ValueError, match="requires secret_ref"):
        build_generic_sql_runner(
            POSTGRES, config=_BASE, secret_ref=None, secret_store=FakeSecretStore()
        )
    runner = registry.build_check_runner(
        conn_type="postgres",
        config=_BASE,
        secret_ref="ref",
        secret_store=FakeSecretStore({"ref": "pw"}),
    )
    assert isinstance(runner, GenericSqlCheckRunner)
    runner.close()  # never used → idempotent no-op


def test_the_adapter_refuses_to_test_without_a_password() -> None:
    with pytest.raises(ValueError, match="a password is required"):
        registry.get_connection_adapter("postgres").test(_BASE, None)


class TestTargetShape:
    def test_a_table_and_optional_schema_resolve(self) -> None:
        resolved = registry.resolve_target_shape("postgres", {"table": "Orders", "schema": "Sales"})
        assert (resolved.table, resolved.schema, resolved.catalog) == ("Orders", "Sales", None)
        assert registry.resolve_target_shape("postgres", {"table": "t"}).schema is None

    @pytest.mark.parametrize(
        "target",
        [
            {"table": "x" * 64},  # PostgreSQL would truncate it onto another name
            {"table": "t", "schema": "s" * 64},
            {"table": "orders; drop table x"},
            {"table": "a.b"},  # a dotted table is two identifiers, not one
            {"table": "t", "schema": 'x"y'},
        ],
    )
    def test_a_name_the_run_path_cannot_address_is_refused_at_save_time(
        self, target: dict[str, Any]
    ) -> None:
        with pytest.raises(TargetShapeError, match="plain SQL identifier"):
            registry.resolve_target_shape("postgres", target)

    def test_the_longest_name_postgres_keeps_is_accepted(self) -> None:
        assert registry.resolve_target_shape("postgres", {"table": "t" * 63}).table == "t" * 63

    def test_a_sampling_block_is_refused_because_nothing_is_materialised(self) -> None:
        with pytest.raises(TargetShapeError, match="pushdown"):
            registry.resolve_target_shape(
                "postgres", {"table": "t", "sampling": {"strategy": "head", "rows": 10}}
            )


# ───────────────────────────── failure classification ─────────────────────────────


class _DriverError(Exception):
    pass


@pytest.mark.parametrize(
    ("message", "category", "auth"),
    [
        ('FATAL:  password authentication failed for user "u"', FailureCategory.PERMISSION, True),
        (
            'FATAL:  no pg_hba.conf entry for host "1.2.3.4", user "u", database "d"',
            FailureCategory.PERMISSION,
            False,
        ),
        (
            'could not translate host name "nope.invalid" to address: Name or service not known',
            FailureCategory.CONNECTIVITY,
            False,
        ),
        (
            'could not translate host name "nope.invalid" to address: nodename nor servname '
            "provided, or not known",
            FailureCategory.CONNECTIVITY,
            False,
        ),
        ("server does not support SSL, but SSL was required", FailureCategory.CONNECTIVITY, False),
        ('relation "sales.orders" does not exist', FailureCategory.CONFIG, False),
        ("(1146, \"Table 'shop.orders' doesn't exist\")", FailureCategory.CONFIG, False),
    ],
)
def test_postgres_failures_are_classified(
    message: str, category: FailureCategory, auth: bool
) -> None:
    exc = _DriverError(message)
    assert classify_failure_category(exc) is category
    assert is_auth_failure(exc) is auth


class _StrictConnection:
    """A DBAPI connection that, like pyodbc and PyMySQL, refuses to be used once closed."""

    def __init__(self) -> None:
        import sqlite3

        self._conn = sqlite3.connect(":memory:")
        self.closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def _check(self) -> None:
        if self.closed:
            raise RuntimeError("Attempt to use a closed connection.")

    def rollback(self) -> None:
        self._check()
        self._conn.rollback()

    def close(self) -> None:
        self._check()
        self.closed = True
        self._conn.close()


def test_closing_a_run_logs_no_false_pool_errors(caplog: pytest.LogCaptureFixture) -> None:
    """GX keeps each engine's connection checked out and leaves its engines in reference cycles.
    Once the run's connections were closed, collecting those engines reset and closed them
    again, and SQLAlchemy logged each strict-driver refusal at ERROR although nothing had
    failed (#2141)."""
    import gc
    import logging

    import sqlalchemy as sa
    from sqlalchemy.pool import StaticPool

    from backend.app.datasources.generic_sql import GxConnectionSource

    class _Proxied:
        def __init__(self) -> None:
            self.dbapi_connection = _StrictConnection()

        def detach(self) -> None:
            pass

    class _Source:
        def raw_connection(self) -> _Proxied:
            return _Proxied()

        def dispose(self) -> None:
            pass

    source = GxConnectionSource.__new__(GxConnectionSource)
    source._source = _Source()
    source._opened = []

    def gx_like_run() -> None:
        engine = sa.create_engine("sqlite://", creator=source.connect, poolclass=StaticPool)
        held = engine.connect()  # GX holds one for the engine's lifetime
        held.exec_driver_sql("select 1")
        # A reference cycle, as GX's engines are in: freed only by the collector.
        engine.cycle = engine  # type: ignore[attr-defined]

    opened = source._opened
    gx_like_run()
    connection = opened[0]
    with caplog.at_level(logging.DEBUG, logger="sqlalchemy.pool"):
        source.close()
        gc.collect()
    assert connection.closed
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_a_failed_run_also_logs_no_false_pool_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A propagating traceback keeps the failed evaluation's locals — GX's engines — alive past
    the close unless its frames are cleared (#2141)."""
    import gc
    import logging

    import sqlalchemy as sa
    from sqlalchemy.pool import StaticPool

    from backend.app.datasources import generic_sql

    class _Proxied:
        def __init__(self) -> None:
            self.dbapi_connection = _StrictConnection()

        def detach(self) -> None:
            pass

    class _Source:
        def raw_connection(self) -> _Proxied:
            return _Proxied()

        def dispose(self) -> None:
            pass

    sources: list[Any] = []

    class _Recording(generic_sql.GxConnectionSource):
        def __init__(self, *_: Any, **__: Any) -> None:
            self._source = _Source()
            self._opened = []
            sources.append(self)

    def failing_evaluate(self: Any, connections: Any, **_: Any) -> Any:
        engine = sa.create_engine("sqlite://", creator=connections.connect, poolclass=StaticPool)
        held = engine.connect()
        held.exec_driver_sql("select 1")
        engine.cycle = engine
        raise RuntimeError("the warehouse went away mid-run")

    monkeypatch.setattr(generic_sql, "GxConnectionSource", _Recording)
    monkeypatch.setattr(generic_sql.GenericSqlCheckRunner, "_evaluate", failing_evaluate)
    runner = generic_sql.GenericSqlCheckRunner.__new__(generic_sql.GenericSqlCheckRunner)
    runner._spec = POSTGRES
    runner._config = POSTGRES.validate_config(
        {"host": "db.example.com", "database": "d", "user": "u"}
    )
    runner._secret = "pw"  # nosec B105
    with caplog.at_level(logging.DEBUG, logger="sqlalchemy.pool"):
        raised = False
        try:
            runner._run_batch(
                table="t",
                schema=None,
                checks=[],
                index_columns=None,
                value_signal_gate=None,
                read_only=True,
            )
        except RuntimeError as exc:
            raised = "went away" in str(exc)
        # The exception is released (as the worker does once it has recorded the failure); only
        # now may anything still holding GX's engines let them go.
        gc.collect()
    assert raised
    assert sources[0]._opened == []
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
