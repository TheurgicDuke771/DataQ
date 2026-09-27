"""The Trino spec on the generic SQL base (#1685) — the pure, server-free half.

The driver-boundary behaviour runs live in `tests/integration/test_trino_datasource.py`.
"""

from __future__ import annotations

import importlib.metadata
import ssl
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import jwt
import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from backend.app.datasources import registry
from backend.app.datasources.base import TargetShapeError
from backend.app.datasources.engines import engines_for
from backend.app.datasources.generic_sql import (
    _NO_LIMIT,
    GenericSqlCheckRunner,
    GenericSqlConnectionAdapter,
    build_generic_sql_runner,
)
from backend.app.datasources.postgres import POSTGRES
from backend.app.datasources.sql_engines import (
    GENERIC_SQL_TYPES,
    SQL_BATCH_CONNECTION_TYPES,
    SQL_ENGINES,
    authenticates_without_secret,
    default_schema,
)
from backend.app.datasources.trino import TRINO, TrinoConfig, _column_caps
from backend.app.db.models import Connection
from backend.app.services import browse_service, dataset_reader, profile_service
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.custom_sql import SQL_QUERYABLE_TYPES
from backend.app.services.failure_classifier import (
    FailureCategory,
    classify_failure_category,
    is_auth_failure,
)
from backend.app.services.inventory_service import INVENTORY_TYPES
from backend.app.services.llm_sqlgen import _DIALECT_BY_TYPE
from backend.app.services.run_admission import PUSHDOWN_TYPES
from backend.tests.support.certs import self_signed_ca_pem
from backend.tests.support.fake_secret_store import FakeSecretStore

_BASE: dict[str, Any] = {"host": "trino.internal", "catalog": "hive", "user": "dq_reader"}
_OPEN: dict[str, Any] = {**_BASE, "auth_type": "none", "sslmode": "disable"}


def _config(**overrides: Any) -> TrinoConfig:
    config = TRINO.validate_config({**_BASE, **overrides})
    assert isinstance(config, TrinoConfig)
    return config


# ───────────────────────────── driver + licence ─────────────────────────────


def test_the_driver_is_the_apache_licensed_trino_client() -> None:
    """ADR 0031: an Apache-2.0 driver (its `orjson` dependency is Apache-2.0/MIT + MPL-2.0)."""
    metadata = importlib.metadata.metadata("trino")
    assert metadata["License"] == "Apache 2.0"
    assert TRINO.drivername == "trino"


# ───────────────────────────── config ─────────────────────────────


def test_defaults_are_the_safe_ones() -> None:
    config = _config()
    assert (config.sslmode, config.auth_type, config.ca_bundle) == ("verify-full", "password", None)
    assert config.effective_port == 443
    assert config.default_schema == "default"
    assert config.url_database() == "hive/default"
    assert config.requires_secret()
    # The catalog is stored under its own name, and a round trip keeps it.
    assert config.model_dump(by_alias=True)["catalog"] == "hive"
    assert TRINO.validate_config(config.model_dump(by_alias=True)) == config


def test_plain_http_defaults_to_trinos_http_port_and_needs_no_secret() -> None:
    config = TRINO.validate_config(_OPEN)
    assert config.effective_port == 8080
    assert not config.requires_secret()
    assert TRINO.validate_config({**_OPEN, "port": 9000}).effective_port == 9000


@pytest.mark.parametrize("auth_type", ["password", "jwt"])
def test_a_credential_is_never_configured_to_travel_in_plaintext(auth_type: str) -> None:
    with pytest.raises(ValidationError, match="needs TLS"):
        _config(auth_type=auth_type, sslmode="disable")


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "prefer"])
def test_there_is_no_tls_mode_that_skips_verification(sslmode: str) -> None:
    with pytest.raises(ValidationError):
        _config(sslmode=sslmode)


@pytest.mark.parametrize("blank", ["", "  ", None])
def test_cleared_optional_fields_mean_the_defaults(blank: Any) -> None:
    config = _config(sslmode=blank, auth_type=blank, ca_bundle=blank, port="", schema="")
    assert (config.sslmode, config.auth_type, config.ca_bundle) == ("verify-full", "password", None)
    assert config.port is None and config.schema_ is None


def test_catalog_and_schema_must_be_lower_case_identifiers() -> None:
    with pytest.raises(ValidationError, match="lower case"):
        _config(catalog="Hive")
    with pytest.raises(ValidationError, match="lower case"):
        _config(schema="Sales")
    with pytest.raises(ValidationError, match="plain SQL identifier"):
        _config(catalog="hive; drop")
    assert _config(schema="s" * 128).default_schema == "s" * 128
    with pytest.raises(ValidationError, match="at most 128"):
        _config(schema="s" * 129)


def test_the_same_names_stay_valid_on_an_engine_that_keeps_their_case() -> None:
    assert POSTGRES.config_model.identifier_problem("Sales") is None
    assert TRINO.config_model.identifier_problem("Sales") is not None


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValidationError):
        _config(database_url="x")


class TestCaBundle:
    def test_a_pem_bundle_is_accepted_and_normalised(self) -> None:
        pem = self_signed_ca_pem()
        config = _config(ca_bundle="\n" + pem + "\n\n")
        assert config.ca_bundle == pem.strip() + "\n"

    @pytest.mark.parametrize(
        "bundle",
        [
            "not a certificate",
            "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----",
            "-----BEGIN CERTIFICATE-----" + "A" * (64 * 1024),
            12,
        ],
    )
    def test_anything_else_is_refused(self, bundle: Any) -> None:
        with pytest.raises(ValidationError, match="ca_bundle"):
            _config(ca_bundle=bundle)

    def test_a_bundle_without_tls_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="TLS only"):
            TRINO.validate_config({**_OPEN, "ca_bundle": self_signed_ca_pem()})


# ───────────────────────────── session + auth ─────────────────────────────


def test_every_session_is_utc_and_verifies_tls_against_the_system_store() -> None:
    args = TRINO.connect_args(_config(), 10)
    assert args == {
        "http_scheme": "https",
        "timezone": "UTC",
        "source": "dataq",
        "verify": True,
        "request_timeout": 10,
    }
    assert "request_timeout" not in TRINO.connect_args(_config(), None)
    plain = TRINO.connect_args(TRINO.validate_config(_OPEN), None)
    assert plain["http_scheme"] == "http" and "verify" not in plain


def test_a_custom_ca_is_the_only_trust_anchor_and_is_shared_by_content() -> None:
    pem = self_signed_ca_pem()
    first = TRINO.connect_args(_config(ca_bundle=pem), None)["verify"]
    again = TRINO.connect_args(_config(ca_bundle=pem), None)["verify"]
    assert isinstance(first, str) and first == again
    assert Path(first).read_text() == pem.strip() + "\n"
    ssl.create_default_context(cafile=first)  # loadable as a CA file
    other = TRINO.connect_args(_config(ca_bundle=self_signed_ca_pem()), None)["verify"]
    assert other != first


def test_a_file_planted_at_the_bundle_path_is_replaced_not_trusted() -> None:
    pem = self_signed_ca_pem()
    path = Path(TRINO.connect_args(_config(ca_bundle=pem), None)["verify"])
    path.write_text(self_signed_ca_pem())  # someone else's CA, at the predictable name
    assert TRINO.connect_args(_config(ca_bundle=pem), None)["verify"] == str(path)
    assert path.read_text() == pem.strip() + "\n"


def test_the_secret_rides_an_auth_object_never_the_url() -> None:
    from trino.auth import BasicAuthentication, JWTAuthentication

    config = _config()
    url, args = TRINO.engine_args(config, "p@ss:w/rd?#%")
    assert make_url(url).password is None and "p%40ss" not in url
    assert isinstance(args["auth"], BasicAuthentication)
    jwt_url, jwt_args = TRINO.engine_args(_config(auth_type="jwt"), "a.b.c")
    assert "a.b.c" not in jwt_url
    assert isinstance(jwt_args["auth"], JWTAuthentication)
    _, open_args = TRINO.engine_args(TRINO.validate_config(_OPEN), None)
    assert "auth" not in open_args


def test_an_authenticated_mode_without_its_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="needs its secret"):
        TRINO.engine_args(_config(), None)


def test_the_driver_would_refuse_a_credential_over_http_too() -> None:
    """Defence in depth under the config rule: the client itself refuses auth over http."""
    from trino.auth import BasicAuthentication
    from trino.dbapi import connect
    from trino.exceptions import TrinoAuthError

    with pytest.raises(TrinoAuthError, match="TLS/SSL is required"):
        connect(
            host="trino.internal",
            port=8080,
            user="u",
            http_scheme="http",
            auth=BasicAuthentication("u", "pw"),
        )


def test_the_session_is_scoped_to_the_target_schema_in_the_url() -> None:
    scoped = TRINO.scoped_config(_config(), "sales")
    parsed = make_url(TRINO.url_string(scoped, "pw"))
    assert (parsed.drivername, parsed.database, parsed.port) == ("trino", "hive/sales", 443)
    with pytest.raises(ValidationError, match="lower case"):
        TRINO.scoped_config(_config(), "Sales")


class TestCredentialExpiry:
    def _token(self, **claims: Any) -> str:
        return jwt.encode(claims, "k" * 32, algorithm="HS256")

    def test_a_jwt_states_its_own_expiry(self) -> None:
        exp = datetime(2031, 1, 2, 3, 4, 5, tzinfo=UTC)
        token = self._token(sub="dq", exp=int(exp.timestamp()))
        config = {**_BASE, "auth_type": "jwt"}
        assert registry.credential_expiry("trino", config, token) == exp

    def test_no_exp_a_password_or_an_unreadable_token_is_unknown(self) -> None:
        config = {**_BASE, "auth_type": "jwt"}
        assert registry.credential_expiry("trino", config, self._token(sub="dq")) is None
        assert registry.credential_expiry("trino", _BASE, "a password") is None
        # Garbage is logged and read as unknown, never raised into the save path.
        assert registry.credential_expiry("trino", config, "not-a-jwt") is None
        assert registry.credential_expiry("postgres", {**_BASE, "database": "d"}, "x") is None


# ───────────────────────────── identity + registry ─────────────────────────────


def test_the_asset_identity_is_the_openlineage_trino_shape() -> None:
    identity = resolve_asset_identity("trino", {**_BASE, "schema": "sales"}, {"table": "orders"})
    assert identity.namespace == "trino://trino.internal:443"
    assert identity.name == "hive.sales.orders"
    other = resolve_asset_identity("trino", _OPEN, {"table": "t", "schema": "raw"})
    assert (other.namespace, other.name) == ("trino://trino.internal:8080", "hive.raw.t")


def test_trino_is_registered_everywhere_a_sql_datasource_must_be() -> None:
    assert "trino" in GENERIC_SQL_TYPES
    for capability in (
        SQL_QUERYABLE_TYPES,
        SQL_BATCH_CONNECTION_TYPES,
        PUSHDOWN_TYPES,
        INVENTORY_TYPES,
    ):
        assert "trino" in capability
    assert _DIALECT_BY_TYPE["trino"] == "Trino SQL"
    assert engines_for("trino") == {"gx"}
    assert default_schema("trino", _BASE) == "default"
    # #1401: moving the host or port, dropping TLS, trusting another CA or switching the auth
    # mode all change who receives the secret, or how.
    assert registry.destination_fields("trino") == {
        "secret": ("host", "port", "sslmode", "ca_bundle", "auth_type")
    }
    trino, postgres = (registry.get_connection_adapter(t) for t in ("trino", "postgres"))
    assert isinstance(trino, GenericSqlConnectionAdapter) and trino.secret_optional is True
    assert isinstance(postgres, GenericSqlConnectionAdapter) and postgres.secret_optional is False


def test_only_an_unauthenticated_config_goes_without_a_secret() -> None:
    assert authenticates_without_secret("trino", _OPEN)
    assert not authenticates_without_secret("trino", _BASE)
    assert not authenticates_without_secret("postgres", {"host": "h", "database": "d", "user": "u"})
    assert not authenticates_without_secret("snowflake", {})


def test_the_runner_builder_needs_a_secret_only_when_the_config_authenticates() -> None:
    runner = build_generic_sql_runner(
        TRINO, config=_OPEN, secret_ref=None, secret_store=FakeSecretStore()
    )
    assert isinstance(runner, GenericSqlCheckRunner)
    runner.close()
    with pytest.raises(ValueError, match="requires secret_ref"):
        build_generic_sql_runner(
            TRINO, config=_BASE, secret_ref=None, secret_store=FakeSecretStore()
        )


def test_the_adapter_refuses_to_test_an_authenticated_config_without_its_secret() -> None:
    with pytest.raises(ValueError, match="a password is required"):
        registry.get_connection_adapter("trino").test(_BASE, None)


class TestTargetShape:
    def test_a_lower_case_target_resolves(self) -> None:
        resolved = registry.resolve_target_shape("trino", {"table": "orders", "schema": "sales"})
        assert (resolved.table, resolved.schema, resolved.catalog) == ("orders", "sales", None)

    @pytest.mark.parametrize(
        "target",
        [{"table": "Orders"}, {"table": "orders", "schema": "Sales"}, {"table": "t" * 129}],
    )
    def test_a_name_trino_would_never_report_is_refused_at_save_time(
        self, target: dict[str, Any]
    ) -> None:
        with pytest.raises(TargetShapeError):
            registry.resolve_target_shape("trino", target)


def test_no_limit_is_a_value_trino_accepts_under_order_by() -> None:
    assert _NO_LIMIT == 2**31 - 1


# ───────────────────────────── profiler caps ─────────────────────────────


class _Rows:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, str]]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows
        self.params: dict[str, Any] = {}

    def execute(self, _statement: Any, params: dict[str, Any]) -> _Rows:
        self.params = params
        return _Rows(self.rows)


def test_types_trino_cannot_order_are_profiled_without_min_max_or_top_values() -> None:
    conn = _Conn(
        [
            ("amount", "decimal(12,2)"),
            ("flag", "boolean"),
            ("tags", "array(varchar)"),
            ("payload", "json"),
            ("attrs", "map(varchar, bigint)"),
            ("nested", "array(json)"),
            ("rec", "row(a bigint, m map(varchar, varchar))"),
            ("sketch", "HyperLogLog"),
        ]
    )
    caps = _column_caps(conn, "sales", "orders")
    assert conn.params == {"schema": "sales", "table": "orders"}
    aggregates = {name: cap.orderable and cap.groupable for name, cap in caps.items()}
    assert aggregates == {
        "amount": True,
        "flag": True,
        "tags": True,
        "payload": False,
        "attrs": False,
        "nested": False,
        "rec": False,
        "sketch": False,
    }
    assert all(cap.orderable == cap.groupable for cap in caps.values())


def test_the_profiler_looks_caps_up_folded_whatever_the_caller_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from backend.app.datasources.generic_sql import ColumnCaps

    seen: list[tuple[str, str]] = []

    def caps(_conn: Any, schema: str, table: str) -> dict[str, ColumnCaps]:
        seen.append((schema, table))
        return {"payload": ColumnCaps(False, False), "amount": ColumnCaps()}

    monkeypatch.setitem(SQL_ENGINES, "trino", replace(TRINO, column_caps=caps))
    row = _open_connection_row()
    got = profile_service._column_caps(
        row, None, schema="Sales", table="Events", columns=["Payload", "amount", "Missing"]
    )
    assert seen == [("sales", "events")]
    assert got == {"Payload": ColumnCaps(False, False), "amount": ColumnCaps()}


# ───────────────────────────── the credential-free paths ─────────────────────────────


def _open_connection_row(**config: Any) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="t",
        type="trino",
        env="dev",
        config={**_OPEN, "port": 1, **config},
        secret_ref=None,
    )


def test_an_unauthenticated_connection_reaches_the_server_not_a_missing_credential_error() -> None:
    """With no secret stored, the profiler, dataset reader and browser must go on to connect
    (here: to a closed port) rather than stop at "no stored credential"."""
    row = _open_connection_row()
    from sqlalchemy import text

    with pytest.raises(Exception) as exc:
        with profile_service._open_connection(row, FakeSecretStore()) as conn:
            conn.execute(text("SELECT 1"))  # HTTP is stateless: nothing is sent until a query
    assert "secret_ref" not in str(exc.value)
    assert classify_failure_category(exc.value) is FailureCategory.CONNECTIVITY
    with pytest.raises(Exception) as read_exc:
        dataset_reader.read_dataset(
            row,
            dataset_reader.DatasetSpec(table="orders", schema="sales"),
            max_rows=1,
            secret_store=FakeSecretStore(),
        )
    assert not isinstance(read_exc.value, dataset_reader.DatasetReadUnsupportedError)


def test_an_authenticated_connection_without_its_secret_still_stops_early() -> None:
    row = _open_connection_row(auth_type="password", sslmode="verify-full")
    with pytest.raises(ValueError, match="secret_ref"):
        with profile_service._open_connection(row, FakeSecretStore()):
            pass
    with pytest.raises(dataset_reader.DatasetReadUnsupportedError):
        dataset_reader.read_dataset(
            row,
            dataset_reader.DatasetSpec(table="orders", schema="sales"),
            max_rows=1,
            secret_store=FakeSecretStore(),
        )


def test_browse_skips_the_credential_check_only_for_an_unauthenticated_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _require(connection: Connection) -> str:
        calls.append(connection.config["auth_type"])
        return "ref"

    monkeypatch.setattr(browse_service, "_require_credential", _require)
    row = _open_connection_row()
    with pytest.raises(browse_service.BrowseFailedError):
        browse_service.browse_catalog(
            row,
            catalog=None,
            schema=None,
            session=cast(Any, None),  # no credential → credential health never touches it
            limit=5,
            secret_store=FakeSecretStore(),
        )
    assert calls == []


@pytest.mark.parametrize(
    ("message", "category", "auth"),
    [
        ("error 401: b'Invalid credentials'", FailureCategory.PERMISSION, True),
        (
            "error 401: b'JWT expired 5 milliseconds ago at 2026-09-27T11:27:39.000Z.'",
            FailureCategory.PERMISSION,
            True,
        ),
        (
            'TrinoUserError(type=USER_ERROR, name=PERMISSION_DENIED, message="Access Denied: '
            'Cannot insert into table memory.sales.orders")',
            FailureCategory.PERMISSION,
            False,
        ),
        (
            'TrinoUserError(type=USER_ERROR, name=TABLE_NOT_FOUND, message="line 1:15: Table '
            "'memory.sales.nope' does not exist\")",
            FailureCategory.CONFIG,
            False,
        ),
        (
            "TrinoUserError(type=USER_ERROR, name=CATALOG_NOT_FOUND, message=\"Catalog 'nope' "
            'not found")',
            FailureCategory.CONFIG,
            False,
        ),
        (
            "HTTPSConnectionPool(host='h', port=443): Max retries exceeded with url: /v1/statement"
            " (Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED]"
            " certificate verify failed')))",
            FailureCategory.CONNECTIVITY,
            False,
        ),
    ],
)
def test_trino_failures_are_classified(message: str, category: FailureCategory, auth: bool) -> None:
    exc = RuntimeError(message)
    assert classify_failure_category(exc) is category
    assert is_auth_failure(exc) is auth
