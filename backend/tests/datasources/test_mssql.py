"""The SQL Server / T-SQL spec (#1679, ADR 0044) — the pure, server-free half.

What crosses the TDS driver boundary is exercised against a real Azure SQL database in the opt-in
live lane (`tests/integration/test_mssql_live.py`); this file pins config validation, the URL and
connect-args of both driver lanes and both auth modes, the honest-failure hooks, the registry
wiring, and the driver-boundary TYPES python-tds hands back (built as the driver builds them).
"""

from __future__ import annotations

import datetime as dt
import sys
import types
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import ValidationError
from sqlalchemy import make_url

from backend.app.core.errors import SafeMonitorError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.datasources import generic_sql, mssql, mssql_tds, registry
from backend.app.datasources.engines import engines_for
from backend.app.datasources.generic_sql import (
    GenericSqlCheckRunner,
    KnownDatasourceLimitationError,
)
from backend.app.datasources.monitors import freshness_age_hours
from backend.app.datasources.mssql import (
    FABRIC_LOGIN_PREREQUISITES,
    FABRIC_PYTDS_LIMITATION,
    MSSQL,
    MssqlConfig,
    is_fabric_host,
)
from backend.app.datasources.postgres import POSTGRES
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
    safe_failure_reason,
)
from backend.app.services.run_admission import PUSHDOWN_TYPES

_TENANT = "11111111-2222-3333-4444-555555555555"
_CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_SQL: dict[str, Any] = {"host": "srv.database.windows.net", "database": "dq", "user": "reader"}
_SP: dict[str, Any] = {
    "host": "srv.database.windows.net",
    "database": "dq",
    "auth_type": "entra_service_principal",
    "tenant_id": _TENANT,
    "client_id": _CLIENT,
}
_FABRIC_HOST = "abc-def.datawarehouse.fabric.microsoft.com"


def _config(base: dict[str, Any] = _SQL, **overrides: Any) -> MssqlConfig:
    return MssqlConfig.model_validate({**base, **overrides})


def _pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "private test CA")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ───────────────────────────── config ─────────────────────────────


def test_defaults_are_the_safe_ones() -> None:
    config = _config()
    assert config.auth_type == "sql"
    assert config.driver == "python-tds"  # the shipped, MIT lane — never the ODBC one by default
    assert config.effective_port == 1433
    assert config.default_schema == "dbo"
    assert config.ca_bundle is None
    assert config.inventory_sync is True


def test_blank_optionals_mean_the_default() -> None:
    config = _config(port="", schema="", driver="", auth_type="", odbc_driver=" ")
    assert (config.port, config.schema_, config.driver, config.auth_type) == (
        None,
        None,
        "python-tds",
        "sql",
    )
    assert config.odbc_driver == mssql.DEFAULT_ODBC_DRIVER


def test_sql_auth_needs_a_user() -> None:
    with pytest.raises(ValidationError, match="needs a user"):
        MssqlConfig.model_validate({"host": "h", "database": "d"})


def test_sql_auth_refuses_service_principal_fields() -> None:
    with pytest.raises(ValidationError, match="entra_service_principal"):
        _config(tenant_id=_TENANT)


def test_service_principal_needs_tenant_and_client() -> None:
    for missing in ("tenant_id", "client_id"):
        raw = {k: v for k, v in _SP.items() if k != missing}
        with pytest.raises(ValidationError, match="needs tenant_id and client_id"):
            MssqlConfig.model_validate(raw)


def test_service_principal_refuses_a_user() -> None:
    with pytest.raises(ValidationError, match="leave user empty"):
        _config(_SP, user="someone")


@pytest.mark.parametrize(
    "client", ["not-a-guid", _CLIENT + "0", "aaaaaaaa-bbbb-cccc-dddd", "' OR 1=1"]
)
def test_client_id_must_be_a_guid(client: str) -> None:
    with pytest.raises(ValidationError, match="client_id"):
        _config(_SP, client_id=client)


@pytest.mark.parametrize("tenant", [_TENANT, "contoso.onmicrosoft.com", "contoso.com"])
def test_tenant_may_be_a_guid_or_a_domain(tenant: str) -> None:
    assert _config(_SP, tenant_id=tenant).tenant_id == tenant


@pytest.mark.parametrize(
    "tenant",
    ["contoso", "evil.com/../x", "a b.com", "evil.com?x=1", "evil.com#", "-bad.com", "x" * 300],
)
def test_tenant_cannot_leave_the_token_endpoint_path(tenant: str) -> None:
    with pytest.raises(ValidationError, match="tenant_id"):
        _config(_SP, tenant_id=tenant)


@pytest.mark.parametrize(
    "host",
    ["srv\\instance", "srv:1433", "tcp:srv", "user:pw@srv", "srv/db", "", "srv.example ;"],
)
def test_host_is_a_bare_hostname(host: str) -> None:
    """A named instance (`host\\instance`) is not a host: DataQ connects by host + port."""
    with pytest.raises(ValidationError):
        _config(host=host)


def test_ca_bundle_must_be_pem_certificates() -> None:
    assert _config(ca_bundle=_pem()).ca_bundle is not None
    for bad in ("not a cert", "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"):
        with pytest.raises(ValidationError, match="PEM"):
            _config(ca_bundle=bad)
    with pytest.raises(ValidationError, match="too large"):
        _config(ca_bundle="x" * (65 * 1024))


def test_a_private_ca_is_for_the_python_tds_lane_only() -> None:
    with pytest.raises(ValidationError, match="python-tds driver only"):
        _config(driver="odbc", ca_bundle=_pem())


def test_unknown_lane_and_auth_are_refused() -> None:
    with pytest.raises(ValidationError):
        _config(driver="pymssql")
    with pytest.raises(ValidationError):
        _config(auth_type="managed_identity")


def test_odbc_driver_name_is_bounded() -> None:
    with pytest.raises(ValidationError, match="odbc_driver"):
        _config(odbc_driver="ODBC Driver 18;Encrypt=no")


def test_identifier_limit_is_sysname() -> None:
    assert _config(schema="s" * 128).schema_ == "s" * 128
    with pytest.raises(ValidationError):
        _config(schema="s" * 129)


# ───────────────────────────── URLs + connect args ─────────────────────────────


def test_sql_auth_url_is_pytds_with_the_password() -> None:
    url = make_url(MSSQL.url_string(_config(), "p@ss/w:rd"))
    assert url.drivername == "mssql+pytds"
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "reader",
        "p@ss/w:rd",
        "srv.database.windows.net",
        1433,
        "dq",
    )


def test_service_principal_url_carries_no_credential() -> None:
    """The client secret never enters the URL GX keeps: python-tds gets a token instead."""
    rendered = MSSQL.url_string(_config(_SP), "the-client-secret")
    assert "the-client-secret" not in rendered
    url = make_url(rendered)
    assert url.username is None and url.password is None
    # Not merely hidden by rendering (a URL with no user drops its password from the string):
    # the URL object itself carries none, so the DBAPI never sees user/password beside a token.
    assert MSSQL.url(_config(_SP), "the-client-secret").password is None


def test_tls_is_always_on_with_hostname_verification() -> None:
    import certifi
    import pytds.tls

    args = MSSQL.connect_args(_config(), 10)
    assert args["cafile"] == certifi.where()
    assert args["validate_host"] is True
    assert args["login_timeout"] == 10
    # The run path waits out an Azure SQL serverless resume.
    assert MSSQL.connect_args(_config(), None)["login_timeout"] == 60
    # Connecting at all installs DataQ's validator in place of pytds's broken one.
    assert pytds.tls.validate_host is mssql_tds.validate_host


def test_a_private_ca_is_written_once_and_used_as_the_only_trust() -> None:
    pem = _pem()
    first = MSSQL.connect_args(_config(ca_bundle=pem), 10)["cafile"]
    second = MSSQL.connect_args(_config(ca_bundle=pem), 10)["cafile"]
    assert first == second
    assert Path(first).read_text() == pem
    other = MSSQL.connect_args(_config(ca_bundle=_pem()), 10)["cafile"]
    assert other != first


def test_a_planted_file_at_a_guessable_path_is_never_trusted() -> None:
    """The bundle lives in a private, unguessable directory — not a content-hash name in the
    shared temp dir, where another local user could pre-create it holding their own CA.
    """
    import hashlib
    import os
    import stat
    import tempfile

    pem = _pem()
    digest = hashlib.sha256(pem.encode("ascii")).hexdigest()[:32]
    planted = Path(tempfile.gettempdir()) / f"dataq-mssql-ca-{digest}.pem"
    planted.write_text("attacker CA")
    try:
        path = Path(MSSQL.connect_args(_config(ca_bundle=pem), 10)["cafile"])
        assert path != planted
        assert path.read_text() == pem
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    finally:
        planted.unlink()


class _FakeCredential:
    instances: ClassVar[list[_FakeCredential]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.scopes: list[str] = []
        _FakeCredential.instances.append(self)

    def get_token(self, scope: str) -> Any:
        self.scopes.append(scope)
        return types.SimpleNamespace(token="the-access-token")


def test_service_principal_mints_a_token_per_login(monkeypatch: pytest.MonkeyPatch) -> None:
    import azure.identity

    _FakeCredential.instances = []
    monkeypatch.setattr(azure.identity, "ClientSecretCredential", _FakeCredential)
    url, args = MSSQL.engine_args(_config(_SP), "the-client-secret")
    (credential,) = _FakeCredential.instances
    assert credential.args == (_TENANT, _CLIENT, "the-client-secret")
    assert args["access_token_callable"]() == "the-access-token"
    assert credential.scopes == ["https://database.windows.net/.default"]
    assert "the-client-secret" not in url
    assert args["cafile"] and args["validate_host"] is True


def test_sql_auth_sends_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _, args = MSSQL.engine_args(_config(), "pw")
    assert "access_token_callable" not in args


# ───────────────────────────── the ODBC lane ─────────────────────────────


@pytest.fixture
def fake_pyodbc(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("pyodbc")
    module.drivers = lambda: ["ODBC Driver 18 for SQL Server"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", module)
    return module


def test_odbc_lane_without_pyodbc_says_so_and_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pyodbc", None)  # `import pyodbc` → ImportError
    with pytest.raises(KnownDatasourceLimitationError) as exc:
        MSSQL.engine_args(_config(driver="odbc"), "pw")
    message = str(exc.value)
    assert "pyodbc is not installed" in message
    assert "derived" in message and "ADR 0044" in message
    assert isinstance(exc.value, SafeMonitorError)


def test_odbc_lane_without_the_driver_names_it(fake_pyodbc: types.ModuleType) -> None:
    fake_pyodbc.drivers = lambda: ["FreeTDS"]  # type: ignore[attr-defined]
    with pytest.raises(KnownDatasourceLimitationError, match="'ODBC Driver 18 for SQL Server'"):
        MSSQL.engine_args(_config(driver="odbc"), "pw")


def test_odbc_lane_sql_auth_url_forces_verified_encryption(
    fake_pyodbc: types.ModuleType,
) -> None:
    url_string, args = MSSQL.engine_args(_config(driver="odbc"), "pw", timeout=7)
    url = make_url(url_string)
    assert url.drivername == "mssql+pyodbc"
    assert (url.username, url.password) == ("reader", "pw")
    assert url.query["Encrypt"] == "yes"
    assert url.query["TrustServerCertificate"] == "no"
    assert url.query["driver"] == "ODBC Driver 18 for SQL Server"
    assert "Authentication" not in url.query
    assert args == {"timeout": 7}
    assert MSSQL.connect_args(_config(driver="odbc"), None) == {"timeout": 60}


def test_odbc_lane_service_principal_presents_a_dataq_minted_token(
    fake_pyodbc: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the driver's own ActiveDirectoryServicePrincipal: that hangs to the login timeout on a
    wrong secret (live-found). DataQ mints the token, so a dead secret fails fast with AADSTS.
    """
    import azure.identity

    _FakeCredential.instances = []
    monkeypatch.setattr(azure.identity, "ClientSecretCredential", _FakeCredential)
    url_string, args = MSSQL.engine_args(_config(_SP, driver="odbc"), "the-client-secret")
    url = make_url(url_string)
    assert (url.username, url.password) == (None, None)
    assert "the-client-secret" not in url_string
    # A raw ODBC string, so SQLAlchemy cannot add Trusted_Connection (refused beside a token).
    odbc = str(url.query["odbc_connect"])
    assert odbc == (
        "Driver={ODBC Driver 18 for SQL Server};Server={srv.database.windows.net,1433};"
        "Database={dq};Encrypt={yes};TrustServerCertificate={no}"
    )
    from sqlalchemy.dialects.mssql.pyodbc import MSDialect_pyodbc

    (cargs,), _ = MSDialect_pyodbc().create_connect_args(url)  # type: ignore[no-untyped-call]
    assert "Trusted_Connection" not in cargs and "Authentication" not in cargs
    (credential,) = _FakeCredential.instances
    assert credential.scopes == ["https://database.windows.net/.default"]
    assert args["attrs_before"] == {
        mssql.SQL_COPT_SS_ACCESS_TOKEN: mssql.odbc_access_token("the-access-token")
    }
    assert "access_token_callable" not in args


def test_the_odbc_access_token_struct_is_length_prefixed_utf16() -> None:
    packed = mssql.odbc_access_token("ab")
    assert packed == (4).to_bytes(4, "little") + "ab".encode("utf-16-le")


def test_a_dead_secret_on_the_odbc_lane_fails_before_any_connection(
    fake_pyodbc: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    import azure.identity

    class _Refusing(_FakeCredential):
        def get_token(self, scope: str) -> Any:
            raise RuntimeError("AADSTS7000215: Invalid client secret provided.")

    monkeypatch.setattr(azure.identity, "ClientSecretCredential", _Refusing)
    with pytest.raises(RuntimeError, match="AADSTS7000215") as exc:
        MSSQL.engine_args(_config(_SP, driver="odbc"), "wrong")
    assert is_auth_failure(exc.value)


# ───────────────────────────── Fabric gaps ─────────────────────────────


@pytest.mark.parametrize("expectation_type", sorted(mssql.FABRIC_TEMP_TABLE_TYPES))
def test_temp_table_types_are_refused_for_a_fabric_host_only(expectation_type: str) -> None:
    from backend.app.services.check_service import (
        CheckConfigInvalidError,
        reject_dataframe_only_expectation,
    )

    fabric = {**_SP, "host": _FABRIC_HOST}
    with pytest.raises(CheckConfigInvalidError, match="temporary table") as exc:
        reject_dataframe_only_expectation(
            expectation_type, connection_type="mssql", connection_config=fabric
        )
    assert "custom-SQL" in exc.value.message
    # The same type on Azure SQL / SQL Server is fine, and so is a Fabric host when no config
    # is known (the engine-wide gate only).
    reject_dataframe_only_expectation(
        expectation_type, connection_type="mssql", connection_config=dict(_SP)
    )
    reject_dataframe_only_expectation(expectation_type, connection_type="mssql")


def test_ordinary_types_stay_allowed_on_fabric() -> None:
    from backend.app.services.check_service import reject_dataframe_only_expectation

    reject_dataframe_only_expectation(
        "expect_column_values_to_not_be_null",
        connection_type="mssql",
        connection_config={**_SP, "host": _FABRIC_HOST},
    )


def test_the_connection_read_model_names_what_the_editor_must_hide() -> None:
    """The check editor keys its catalog on connection TYPE; a Fabric host's refusals depend on
    the config, so the server states them per connection (#2140). Same set the API refuses."""
    import uuid

    from backend.app.api.v1.connections import ConnectionRead
    from backend.app.db.models import Connection

    def read(config: dict[str, Any], conn_type: str = "mssql") -> list[str]:
        conn = Connection(
            id=uuid.uuid4(), name="c", type=conn_type, env="dev", config=config, secret_ref=None
        )
        return ConnectionRead.from_model(conn).refused_expectation_types

    assert read({**_SP, "host": _FABRIC_HOST}) == sorted(mssql.FABRIC_TEMP_TABLE_TYPES)
    assert read(dict(_SP)) == []
    assert read({"host": "unvalidatable"}) == []
    assert read({"account_url": "https://a.blob.core.windows.net"}, "adls_gen2") == []


# ───────────────────────────── honest failures ─────────────────────────────


def test_fabric_host_detection() -> None:
    assert is_fabric_host(_FABRIC_HOST)
    assert is_fabric_host("x.database.fabric.microsoft.com.")
    assert is_fabric_host("X.DataWarehouse.Fabric.Microsoft.com")
    assert not is_fabric_host("srv.database.windows.net")
    assert not is_fabric_host("datawarehouse.fabric.microsoft.com.evil.example")


def _boom(*_: Any, **__: Any) -> Any:
    raise RuntimeError("Couldn't complete the operation due to a system update")


def test_fabric_on_python_tds_reports_the_known_limitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sqlalchemy.create_engine", _boom)
    adapter = registry.get_connection_adapter("mssql")
    with pytest.raises(KnownDatasourceLimitationError) as exc:
        adapter.test({**_SP, "host": _FABRIC_HOST}, "secret")
    assert str(exc.value) == FABRIC_PYTDS_LIMITATION
    assert "ODBC Driver 18" in str(exc.value) and "driver to odbc" in str(exc.value)
    assert isinstance(exc.value.__cause__, RuntimeError)  # the driver error stays for the log
    # A run records the SAFE message verbatim, not a generic classification.
    assert safe_failure_reason(exc.value) == FABRIC_PYTDS_LIMITATION


def test_a_non_fabric_failure_is_left_to_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sqlalchemy.create_engine", _boom)
    with pytest.raises(RuntimeError, match="system update"):
        registry.get_connection_adapter("mssql").test(dict(_SQL), "pw")


def test_fabric_login_refusal_on_the_odbc_lane_names_the_prerequisites() -> None:
    config = _config(_SP, host=_FABRIC_HOST, driver="odbc")
    refused = RuntimeError("Login failed for user '<token-identified principal>'. (18456)")
    assert mssql._explain_failure(config, refused) == FABRIC_LOGIN_PREREQUISITES
    assert mssql._explain_failure(config, RuntimeError("Invalid object name 'x'")) is None
    sql_login = _config(host=_FABRIC_HOST, driver="odbc")
    assert mssql._explain_failure(sql_login, refused) is None


def test_the_runner_explains_a_fabric_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(_SP, host=_FABRIC_HOST)
    runner = GenericSqlCheckRunner(MSSQL, config, "secret")

    def _fail(*_: Any, **__: Any) -> Any:
        raise RuntimeError("routed login rejected")

    monkeypatch.setattr(runner, "_run_checks", _fail)
    with pytest.raises(KnownDatasourceLimitationError):
        runner.run_checks(table="orders", schema=None, checks=[])
    monkeypatch.setattr(generic_sql, "run_monitors_over_engine", _fail)
    monkeypatch.setattr(runner._engine, "get", lambda: None)
    with pytest.raises(KnownDatasourceLimitationError):
        runner.run_monitors(table="orders", schema=None, monitors=[])


def test_the_runner_leaves_other_engines_failures_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = GenericSqlCheckRunner(MSSQL, _config(), "pw")

    def _fail(*_: Any, **__: Any) -> Any:
        raise RuntimeError("Invalid object name 'dbo.nope'")

    monkeypatch.setattr(runner, "_run_checks", _fail)
    with pytest.raises(RuntimeError, match="Invalid object name"):
        runner.run_checks(table="nope", schema=None, checks=[])


# ───────────────────────────── the GX schema ─────────────────────────────


class _StopAtAssetError(Exception):
    pass


def _record_asset(recorded: dict[str, Any]) -> Any:
    class _Datasource:
        def add_table_asset(self, **kwargs: Any) -> Any:
            recorded.update(kwargs)
            raise _StopAtAssetError

        def _add_asset(self, asset: Any) -> Any:
            # The exact-schema path (#2137) builds its own TableAsset.
            recorded.update(schema_name=asset.schema_name, table_name=asset.table_name, exact=True)
            raise _StopAtAssetError

        def get_engine(self) -> Any:
            return types.SimpleNamespace(dispose=lambda: None)

    def _factory(context: Any, name: str, url: str, engine_kwargs: dict[str, Any]) -> Any:
        recorded["url"] = url
        return _Datasource()

    return _factory


@pytest.mark.parametrize(
    ("schema", "expected"), [(None, "dbo"), ("Sales", "Sales"), ("dbo", "dbo")]
)
def test_sql_server_hands_gx_the_schema(schema: str | None, expected: str) -> None:
    """A SQL Server session cannot be scoped to a schema, so GX must name it."""
    recorded: dict[str, Any] = {}
    spec = replace(MSSQL, gx_datasource=_record_asset(recorded))
    runner = GenericSqlCheckRunner(spec, _config(), "pw")
    with pytest.raises(_StopAtAssetError):
        runner.run_checks(table="Orders", schema=schema, checks=[])
    assert recorded["schema_name"] == expected
    # A mixed-case schema goes in quoted, past GX's lower-casing, on its own asset (#2137) —
    # whose TableAsset holds the quoted table as a quoted name rather than a '"..."' string.
    mixed = expected != expected.lower()
    assert recorded.get("exact", False) is mixed
    if mixed:
        assert recorded["schema_name"].quote is True
        assert recorded["table_name"] == "Orders" and recorded["table_name"].quote is True
    else:
        assert recorded["table_name"] == '"Orders"'


def test_postgres_still_scopes_the_session_instead() -> None:
    recorded: dict[str, Any] = {}
    spec = replace(POSTGRES, gx_datasource=_record_asset(recorded))
    config = POSTGRES.validate_config({"host": "h", "database": "d", "user": "u"})
    runner = GenericSqlCheckRunner(spec, config, "pw")
    with pytest.raises(_StopAtAssetError):
        runner.run_checks(table="orders", schema="Sales", checks=[])
    assert recorded["schema_name"] is None


# ───────────────────────────── column capabilities ─────────────────────────────


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.params: dict[str, Any] | None = None

    def execute(self, _stmt: Any, params: dict[str, Any]) -> Any:
        self.params = params
        return types.SimpleNamespace(all=lambda: self._rows)


def test_column_caps_follow_what_sql_server_can_aggregate() -> None:
    """Live-verified on Azure SQL: no MIN/MAX over bit/xml/spatial/LOB/json; no COUNT(DISTINCT)
    over any of those but bit. An alias type is as weak as the type it is over.
    """
    conn = _Rows(
        [
            ("id", "int", "int"),
            ("flag", "bit", "bit"),
            ("doc", "xml", "xml"),
            ("shape", "geography", "varbinary"),
            ("note", "ntext", "ntext"),
            ("payload", "json", "json"),
            ("guid", "uniqueidentifier", "uniqueidentifier"),
            ("alias_flag", "YesNo", "bit"),
        ]
    )
    caps = mssql._column_caps(conn, "dbo", "T")
    assert conn.params == {"schema": "dbo", "table": "T"}
    assert caps["id"].orderable and caps["id"].groupable
    assert not caps["flag"].orderable and caps["flag"].groupable
    for name in ("doc", "shape", "note", "payload"):
        assert not caps[name].orderable and not caps[name].groupable
    assert caps["guid"].orderable and caps["guid"].groupable
    assert not caps["alias_flag"].orderable and caps["alias_flag"].groupable


# ───────────────────────────── registry wiring ─────────────────────────────


def test_mssql_is_registered_everywhere_a_sql_datasource_must_be() -> None:
    assert "mssql" in GENERIC_SQL_TYPES
    assert sql_engine("mssql") is MSSQL
    assert "mssql" in SQL_QUERYABLE_TYPES
    assert "mssql" in SQL_BATCH_CONNECTION_TYPES
    assert "mssql" in PUSHDOWN_TYPES
    assert engines_for("mssql") == {"gx"}


def test_every_field_that_moves_the_credential_is_a_destination() -> None:
    """#1401: host/port receive the password or the token; tenant_id/client_id decide where the
    client secret goes and as whom; auth_type re-purposes the secret; a private CA decides whose
    certificate counts; the lane decides which client presents it.
    """
    assert registry.destination_fields("mssql") == {
        "secret": (
            "host",
            "port",
            "auth_type",
            "tenant_id",
            "client_id",
            "ca_bundle",
            "driver",
        )
    }
    assert registry.destination_fields("postgres") == {"secret": ("host", "port")}


def test_a_missing_secret_names_both_kinds() -> None:
    with pytest.raises(ValueError, match="password or client secret is required"):
        registry.get_connection_adapter("mssql").test(dict(_SQL), None)


def test_default_schema_is_dbo() -> None:
    assert default_schema("mssql", _SQL) == "dbo"
    assert default_schema("mssql", {**_SQL, "schema": "Sales"}) == "Sales"


def test_asset_identity_is_the_openlineage_sql_server_shape() -> None:
    identity = resolve_asset_identity("mssql", dict(_SQL), {"table": "Orders", "schema": "Sales"})
    assert identity.namespace == "mssql://srv.database.windows.net:1433"
    assert identity.name == "dq.Sales.Orders"


# ───────────────────────────── driver-boundary types ─────────────────────────────


def test_datetimeoffset_freshness_with_the_drivers_own_tzinfo() -> None:
    """python-tds hands a datetimeoffset back with `pytds.tz.FixedOffsetTimezone`, not a stdlib
    tz (live-verified) — the age math must read it as the instant it is.
    """
    from pytds.tz import FixedOffsetTimezone

    ist = FixedOffsetTimezone(330)  # +05:30, as the driver builds it: offset in minutes
    value = dt.datetime(2026, 9, 20, 15, 30, tzinfo=ist)  # == 10:00 UTC
    now = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)
    assert freshness_age_hours(value, now=now, source="t") == pytest.approx(2.0)
    assert sanitize_json({"v": value}) == {"v": "2026-09-20T15:30:00+05:30"}


def test_datetime2_is_naive_and_read_as_utc() -> None:
    naive = dt.datetime(2026, 9, 20, 10, 0)
    now = dt.datetime(2026, 9, 20, 13, 0, tzinfo=dt.UTC)
    assert freshness_age_hours(naive, now=now, source="t") == pytest.approx(3.0)


def test_numeric_and_bit_survive_the_result_json() -> None:
    assert sanitize_json({"amount": Decimal("12.50"), "gift": True}) == {
        "amount": 12.5,
        "gift": True,
    }


# ───────────────────────────── failure classification ─────────────────────────────


def test_a_refused_sql_login_is_a_dead_credential() -> None:
    """The exact text python-tds raised live for a wrong password."""
    exc = RuntimeError("(pytds.tds_base.OperationalError) (\"Login failed for user 'r'.\", None)")
    assert is_auth_failure(exc)
    assert classify_failure_category(exc) is FailureCategory.PERMISSION


def test_a_refused_client_secret_is_a_dead_credential() -> None:
    exc = RuntimeError("Authentication failed: AADSTS7000215: Invalid client secret provided.")
    assert is_auth_failure(exc)


def test_a_database_the_login_cannot_open_is_not_a_dead_credential() -> None:
    """SQL Server sends 4060 AND an 18456 for it, and pytds joins the messages into one text."""
    exc = RuntimeError(
        'Cannot open database "dq" requested by the login. The login failed. '
        "Login failed for user 'reader'."
    )
    assert not is_auth_failure(exc)


def test_a_hostname_mismatch_is_connectivity() -> None:
    exc = RuntimeError("Certificate does not match host name '20.51.9.131'")
    assert classify_failure_category(exc) is FailureCategory.CONNECTIVITY


def test_tls_cannot_be_weakened_by_the_base_sslmode() -> None:
    """The generic base offers `require`/`disable`; on SQL Server TLS is always fully verified."""
    assert _config().sslmode == "verify-full"
    assert _config(sslmode="").sslmode == "verify-full"
    for weaker in ("disable", "require", "verify-ca"):
        with pytest.raises(ValidationError, match="always verifies"):
            _config(sslmode=weaker)


def test_the_catalog_never_offers_system_schemas() -> None:
    """Fabric adds `queryinsights` system views to INFORMATION_SCHEMA (live-found)."""
    for sql in (MSSQL.catalog.schemas_sql, MSSQL.catalog.tables_sql):
        assert "'sys', 'INFORMATION_SCHEMA', 'queryinsights'" in sql
        assert "HAS_PERMS_BY_NAME" in sql


def test_a_busy_validation_is_not_relabelled_as_a_fabric_driver_limitation() -> None:
    # On Fabric + python-tds the engine explains EVERY failure as the driver limitation (#2204).
    from backend.app.datasources.gx_runner import GxContextBusyError
    from backend.app.datasources.sql_engines import SQL_ENGINES

    busy = GxContextBusyError("busy")
    config = _config(host=_FABRIC_HOST)

    assert generic_sql.explained(SQL_ENGINES["mssql"], config, busy) is busy
