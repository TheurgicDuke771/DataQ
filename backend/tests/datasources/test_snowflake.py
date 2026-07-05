"""Tests for the Snowflake GX adapter — the GX-free, deterministic parts.

`SnowflakeCheckRunner.run_checks` connects to a live warehouse at asset-build
time, so its full chain is not unit-tested here; the `add_snowflake`
construction it performs IS pinned via a fake context (#195 — key-pair uses
the GX kwargs form with a base64-DER private_key, password keeps the DSN).
Everything else — config parsing, connection-string building, expectation
translation, and the GX-result → DTO mapping — is covered. `to_suite_outcome` is exercised against a
*real* GX validation result (pandas batch, no Snowflake) so the test also guards
the GX 1.17 result shape this adapter depends on (`.type`, injected `batch_id`).
"""

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.datasources.base import CheckRunner, CheckSpec, ConnectionAdapter
from backend.app.datasources.registry import (
    UnsupportedConnectionTypeError,
    get_connection_adapter,
)
from backend.app.datasources.snowflake import (
    SnowflakeCheckRunner,
    SnowflakeConfig,
    SnowflakeConnectionAdapter,
    UnknownExpectationError,
    _expectation_class_name,
    _to_gx_expectation,
    build_connect_args,
    build_connection_string,
    build_snowflake_runner,
    to_suite_outcome,
)


def _rsa_pem(passphrase: str | None = None) -> str:
    """A PEM (PKCS#8) RSA private key — passphrase-protected when one is given."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=(
            serialization.BestAvailableEncryption(passphrase.encode())
            if passphrase
            else serialization.NoEncryption()
        ),
    ).decode()


def _key_pair_payload(pem: str, passphrase: str | None = None) -> str:
    """The combined key-pair secret payload (#194) as the frontend composes it."""
    import json

    payload: dict[str, str] = {"private_key": pem}
    if passphrase is not None:
        payload["passphrase"] = passphrase
    return json.dumps(payload)


# Runtime-generated so no passphrase-looking literal lives in the repo
# (CLAUDE.md §11: no credentials — even mock ones — in tracked files).
def _passphrase() -> str:
    import secrets

    return f"pp-{secrets.token_hex(8)}"


_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}


class _FakeStore:
    """Minimal SecretStore: records the name it was asked for, returns a token."""

    def __init__(self) -> None:
        self.asked: str | None = None

    def get(self, name: str) -> str:
        self.asked = name
        return "s3cr3t-pw"

    def set(self, name: str, value: str) -> None:  # satisfies SecretStore Protocol
        self.asked = name

    def delete(self, name: str) -> None:
        pass


# ───────────────────────── SnowflakeConfig ─────────────────────────


def test_config_maps_schema_alias() -> None:
    cfg = SnowflakeConfig.model_validate(_CONFIG)
    assert cfg.schema_ == "FINANCE"
    assert cfg.role == "DQ_ROLE"


def test_config_role_optional() -> None:
    cfg = SnowflakeConfig.model_validate({k: v for k, v in _CONFIG.items() if k != "role"})
    assert cfg.role is None


def test_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        SnowflakeConfig.model_validate({**_CONFIG, "bogus": "x"})


def test_config_requires_account() -> None:
    with pytest.raises(ValidationError):
        SnowflakeConfig.model_validate({k: v for k, v in _CONFIG.items() if k != "account"})


# ───────────────────────── connection string ───────────────────────


def test_connection_string_url_encodes_credentials() -> None:
    cfg = SnowflakeConfig.model_validate(_CONFIG)
    cs = build_connection_string(cfg, "p@ss/w:rd?")
    assert "p%40ss%2Fw%3Ard%3F" in cs
    assert cs.startswith("snowflake://svc_dataq:")
    assert "@ab12345.eu-west-1/ANALYTICS/FINANCE?" in cs
    assert "warehouse=WH_DQ" in cs
    assert "role=DQ_ROLE" in cs


def test_connection_string_omits_role_when_absent() -> None:
    cfg = SnowflakeConfig.model_validate({k: v for k, v in _CONFIG.items() if k != "role"})
    cs = build_connection_string(cfg, "pw")
    assert "role=" not in cs
    assert "warehouse=WH_DQ" in cs


# ───────────────────────── key-pair auth ───────────────────────────


def test_config_auth_type_defaults_to_password() -> None:
    # Existing configs carry no auth_type → password (back-compat).
    cfg = SnowflakeConfig.model_validate(_CONFIG)
    assert cfg.auth_type == "password"


def test_config_accepts_key_pair_auth_type() -> None:
    cfg = SnowflakeConfig.model_validate({**_CONFIG, "auth_type": "key_pair"})
    assert cfg.auth_type == "key_pair"


def test_config_rejects_unknown_auth_type() -> None:
    with pytest.raises(ValidationError):
        SnowflakeConfig.model_validate({**_CONFIG, "auth_type": "oauth"})


def test_key_pair_connection_string_omits_password() -> None:
    cfg = SnowflakeConfig.model_validate({**_CONFIG, "auth_type": "key_pair"})
    cs = build_connection_string(cfg, _rsa_pem())
    # No `user:password@` — just `user@`; the key rides in connect-args.
    assert cs.startswith("snowflake://svc_dataq@ab12345.eu-west-1/")
    assert "svc_dataq:" not in cs


def test_build_connect_args_password_is_empty() -> None:
    cfg = SnowflakeConfig.model_validate(_CONFIG)
    assert build_connect_args(cfg, "pw") == {}


def test_build_connect_args_key_pair_loads_der_private_key() -> None:
    cfg = SnowflakeConfig.model_validate({**_CONFIG, "auth_type": "key_pair"})
    args = build_connect_args(cfg, _rsa_pem())
    assert set(args) == {"private_key"}
    # DER PKCS8 bytes (not the PEM text) — what snowflake-connector wants.
    assert isinstance(args["private_key"], bytes)
    assert b"-----BEGIN" not in args["private_key"]


def test_build_connect_args_rejects_malformed_key() -> None:
    cfg = SnowflakeConfig.model_validate({**_CONFIG, "auth_type": "key_pair"})
    with pytest.raises(ValueError):
        build_connect_args(cfg, "not a pem key")


# ───────────── GX datasource construction (run_checks, #195 kwargs form) ─────────────


class _FakeDataSources:
    """Captures add_snowflake kwargs; the sentinel exception stops run_checks
    before any further GX machinery runs."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def add_snowflake(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        raise _StopRunError


class _StopRunError(Exception):
    pass


def _patch_gx_context(monkeypatch: pytest.MonkeyPatch) -> _FakeDataSources:
    """Point the runner's gx.get_context at a fake context; return its capture."""
    fake_sources = _FakeDataSources()
    fake_context = SimpleNamespace(data_sources=fake_sources)
    monkeypatch.setattr(
        "backend.app.datasources.snowflake.gx.get_context", lambda mode: fake_context
    )
    return fake_sources


def _captured_add_snowflake(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, Any], secret: str
) -> dict[str, Any]:
    """Run run_checks against a fake GX context and return the add_snowflake kwargs."""
    fake_sources = _patch_gx_context(monkeypatch)
    runner = SnowflakeCheckRunner(SnowflakeConfig.model_validate(config), secret)
    with pytest.raises(_StopRunError):
        runner.run_checks(table="T", schema=None, checks=[])
    assert fake_sources.kwargs is not None
    return fake_sources.kwargs


def test_run_checks_key_pair_uses_gx_kwargs_form(monkeypatch: pytest.MonkeyPatch) -> None:
    # The GX-supported key-pair form (#195): connection details as keyword args
    # with a base64-DER private_key — NOT the deprecated/broken
    # connection_string + kwargs['connect_args'] route.
    kwargs = _captured_add_snowflake(monkeypatch, {**_CONFIG, "auth_type": "key_pair"}, _rsa_pem())
    assert "connection_string" not in kwargs
    assert "kwargs" not in kwargs
    assert kwargs["account"] == _CONFIG["account"]
    assert kwargs["user"] == _CONFIG["user"]
    assert kwargs["database"] == _CONFIG["database"]
    assert kwargs["schema"] == _CONFIG["schema"]
    assert kwargs["warehouse"] == _CONFIG["warehouse"]
    assert kwargs["role"] == _CONFIG["role"]
    # base64 string decodable to the DER key bytes (what GX b64-decodes at
    # engine build).
    der = base64.standard_b64decode(kwargs["private_key"])
    assert der and b"-----BEGIN" not in der


def test_config_key_pair_requires_role() -> None:
    # GX's key-pair form mandates a role, so a role-less key-pair config could
    # never run a suite — rejected at validation (create/edit/test time), not
    # at run time (#195).
    config = {k: v for k, v in _CONFIG.items() if k != "role"} | {"auth_type": "key_pair"}
    with pytest.raises(ValidationError, match="role"):
        SnowflakeConfig.model_validate(config)


def test_config_password_role_stays_optional() -> None:
    cfg = SnowflakeConfig.model_validate({k: v for k, v in _CONFIG.items() if k != "role"})
    assert cfg.role is None


def test_run_checks_password_keeps_connection_string(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = _captured_add_snowflake(monkeypatch, _CONFIG, "pw")
    assert set(kwargs) == {"name", "connection_string"}
    assert kwargs["connection_string"].startswith("snowflake://")


def test_gx_accepts_the_key_pair_kwargs_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    # Feed the EXACT kwargs run_checks passes into the real GX datasource model
    # (pydantic validation only — no connection). Pins that GX resolves them to
    # its key-pair form, so a GX-side tightening of the union/validators fails
    # here in CI instead of only at a live suite run.
    from great_expectations.datasource.fluent.snowflake_datasource import (
        KeyPairConnectionDetails,
        SnowflakeDatasource,
    )

    kwargs = _captured_add_snowflake(monkeypatch, {**_CONFIG, "auth_type": "key_pair"}, _rsa_pem())
    datasource = SnowflakeDatasource(**kwargs)
    assert isinstance(datasource.connection_string, KeyPairConnectionDetails)
    assert datasource.connection_string.role == _CONFIG["role"]


# ─────────────── encrypted key-pair secrets (combined payload, #194) ───────────────


_KP_CONFIG = {**_CONFIG, "auth_type": "key_pair"}


def test_build_connect_args_encrypted_key_with_passphrase() -> None:
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    pp = _passphrase()
    secret = _key_pair_payload(_rsa_pem(pp), pp)
    args = build_connect_args(cfg, secret)
    assert isinstance(args["private_key"], bytes)
    # The connector gets a *decrypted* DER PKCS8 key, not the PEM/encrypted form.
    assert b"-----BEGIN" not in args["private_key"]


def test_build_connect_args_json_payload_without_passphrase() -> None:
    # The JSON shape is valid for unencrypted keys too (passphrase omitted).
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    args = build_connect_args(cfg, _key_pair_payload(_rsa_pem()))
    assert isinstance(args["private_key"], bytes)


def test_build_connect_args_wrong_passphrase_raises_without_leaking_it() -> None:
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    real, wrong = _passphrase(), _passphrase()
    secret = _key_pair_payload(_rsa_pem(real), wrong)
    with pytest.raises(ValueError) as excinfo:
        build_connect_args(cfg, secret)
    assert wrong not in str(excinfo.value)
    assert real not in str(excinfo.value)


def test_build_connect_args_encrypted_key_missing_passphrase_raises() -> None:
    # An encrypted key sent as bare PEM (no payload/passphrase) must fail
    # cleanly as ValueError, not the cryptography TypeError.
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    with pytest.raises(ValueError):
        build_connect_args(cfg, _rsa_pem(_passphrase()))


def test_build_connect_args_passphrase_on_unencrypted_key_raises() -> None:
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    with pytest.raises(ValueError):
        build_connect_args(cfg, _key_pair_payload(_rsa_pem(), _passphrase()))


def test_build_connect_args_empty_passphrase_means_none() -> None:
    # An empty passphrase (frontend field left blank inside the JSON shape)
    # behaves like no passphrase — the unencrypted-key path.
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    args = build_connect_args(cfg, _key_pair_payload(_rsa_pem(), ""))
    assert isinstance(args["private_key"], bytes)


def test_key_pair_payload_malformed_json_raises() -> None:
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    with pytest.raises(ValueError, match="not valid JSON"):
        build_connect_args(cfg, "{not json")


def test_key_pair_payload_missing_private_key_raises() -> None:
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    with pytest.raises(ValueError, match="private_key"):
        build_connect_args(cfg, '{"passphrase": "p"}')


def test_key_pair_payload_non_string_passphrase_raises() -> None:
    cfg = SnowflakeConfig.model_validate(_KP_CONFIG)
    with pytest.raises(ValueError, match="passphrase"):
        build_connect_args(cfg, '{"private_key": "x", "passphrase": 42}')


def test_adapter_test_encrypted_key_pair_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # The adapter test path threads the decrypted key into connect_args.
    engine = _FakeEngine([])
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> _FakeEngine:
        captured["connect_args"] = kwargs.get("connect_args")
        return engine

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    pp = _passphrase()
    secret = _key_pair_payload(_rsa_pem(pp), pp)
    SnowflakeConnectionAdapter().test(_KP_CONFIG, secret)

    connect_args = captured["connect_args"]
    assert isinstance(connect_args, dict)
    assert isinstance(connect_args.get("private_key"), bytes)


# ───────────────────────── expectation translation ─────────────────


def test_expectation_class_name_snake_to_pascal() -> None:
    assert (
        _expectation_class_name("expect_column_values_to_not_be_null")
        == "ExpectColumnValuesToNotBeNull"
    )


def test_to_gx_expectation_builds_real_class() -> None:
    exp = _to_gx_expectation(CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}))
    assert type(exp).__name__ == "ExpectColumnValuesToNotBeNull"
    assert exp.column == "id"


def test_to_gx_expectation_unknown_type_raises() -> None:
    with pytest.raises(UnknownExpectationError, match="expect_nonsense_thing"):
        _to_gx_expectation(CheckSpec("expect_nonsense_thing", {}))


# ───────────────────────── result mapping ──────────────────────────


def _fake_check_result(
    *, success: bool, type_: str, kwargs: dict[str, Any], result: dict[str, Any]
) -> SimpleNamespace:
    return SimpleNamespace(
        success=success,
        expectation_config=SimpleNamespace(type=type_, kwargs=kwargs),
        result=result,
    )


def test_to_suite_outcome_maps_observed_and_expected() -> None:
    gx_result = SimpleNamespace(
        success=True,
        results=[
            _fake_check_result(
                success=True,
                type_="expect_table_row_count_to_be_between",
                kwargs={"min_value": 1, "max_value": 10},
                result={"observed_value": 5},
            )
        ],
    )
    outcome = to_suite_outcome(gx_result)
    assert outcome.success is True
    (check,) = outcome.checks
    assert check.observed_value == {"observed_value": 5}
    assert check.expected_value == {"min_value": 1, "max_value": 10}
    assert check.sample_failures is None


def test_to_suite_outcome_strips_gx_internal_batch_id() -> None:
    gx_result = SimpleNamespace(
        success=False,
        results=[
            _fake_check_result(
                success=False,
                type_="expect_column_values_to_not_be_null",
                kwargs={"batch_id": "sf-t", "column": "id"},
                result={"unexpected_count": 2, "partial_unexpected_list": [None, None]},
            )
        ],
    )
    (check,) = to_suite_outcome(gx_result).checks
    assert check.expected_value == {"column": "id"}  # batch_id removed
    assert check.observed_value is None
    assert check.sample_failures == {"unexpected_count": 2, "partial_unexpected_list": [None, None]}


def test_to_suite_outcome_against_real_gx_result() -> None:
    """Guards the GX 1.17 result shape (`.type`, injected `batch_id`) end-to-end."""
    import great_expectations as gx
    import great_expectations.expectations as gxe
    import pandas as pd

    ctx = gx.get_context(mode="ephemeral")
    asset = ctx.data_sources.add_pandas("p").add_dataframe_asset("a")
    batch_definition = asset.add_batch_definition_whole_dataframe("b")
    suite = ctx.suites.add(
        gx.ExpectationSuite(
            name="s",
            expectations=[
                gxe.ExpectColumnValuesToNotBeNull(column="id"),
                gxe.ExpectTableRowCountToBeBetween(min_value=1, max_value=10),
            ],
        )
    )
    validation_definition = ctx.validation_definitions.add(
        gx.ValidationDefinition(name="vd", data=batch_definition, suite=suite)
    )
    gx_result = validation_definition.run(
        batch_parameters={"dataframe": pd.DataFrame({"id": [1, 2, None]})},
        result_format="COMPLETE",
    )

    outcome = to_suite_outcome(gx_result)
    assert outcome.success is False
    by_type = {c.expectation_type: c for c in outcome.checks}
    not_null = by_type["expect_column_values_to_not_be_null"]
    assert not_null.success is False
    assert not_null.expected_value == {"column": "id"}  # no batch_id leak
    assert (not_null.sample_failures or {})["unexpected_count"] == 1
    row_count = by_type["expect_table_row_count_to_be_between"]
    assert row_count.success is True
    assert row_count.observed_value == {"observed_value": 3}


# ───────────────────────── factory ─────────────────────────────────


def test_build_runner_resolves_secret_and_returns_check_runner() -> None:
    store = _FakeStore()
    runner = build_snowflake_runner(config=_CONFIG, secret_ref="snowflake-dev", secret_store=store)
    assert isinstance(runner, SnowflakeCheckRunner)
    assert isinstance(runner, CheckRunner)  # satisfies the Protocol
    assert store.asked == "snowflake-dev"


def test_build_runner_requires_secret_ref() -> None:
    with pytest.raises(ValueError, match="secret_ref"):
        build_snowflake_runner(config=_CONFIG, secret_ref=None, secret_store=_FakeStore())


# ───────────────────────── ConnectionAdapter ───────────────────────


class _FakeConn:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def execute(self, statement: object) -> None:
        self._executed.append(str(statement))

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: object) -> None:
        return None  # don't suppress exceptions (falsy, like the real cursor CM)


class _FakeEngine:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed
        self.disposed = False

    def connect(self) -> _FakeConn:
        return _FakeConn(self._executed)

    def dispose(self) -> None:
        self.disposed = True


def test_adapter_validate_config_returns_model() -> None:
    cfg = SnowflakeConnectionAdapter().validate_config(_CONFIG)
    assert isinstance(cfg, SnowflakeConfig)
    assert cfg.schema_ == "FINANCE"


def test_adapter_validate_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        SnowflakeConnectionAdapter().validate_config({**_CONFIG, "bogus": "x"})


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(SnowflakeConnectionAdapter(), ConnectionAdapter)


def test_adapter_test_runs_select_1_and_disposes(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[str] = []
    engine = _FakeEngine(executed)
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> _FakeEngine:
        captured["url"] = url
        captured["connect_args"] = kwargs.get("connect_args")
        return engine

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    SnowflakeConnectionAdapter().test(_CONFIG, "p@ss")

    assert executed == ["SELECT 1"]
    assert engine.disposed is True
    assert captured["connect_args"] == {"login_timeout": 10, "network_timeout": 10}
    assert "p%40ss" in str(captured["url"])  # password URL-encoded into the DSN


def test_adapter_test_key_pair_passes_private_key_in_connect_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine([])
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> _FakeEngine:
        captured["url"] = url
        captured["connect_args"] = kwargs.get("connect_args")
        return engine

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    SnowflakeConnectionAdapter().test({**_CONFIG, "auth_type": "key_pair"}, _rsa_pem())

    connect_args = captured["connect_args"]
    assert isinstance(connect_args, dict)
    # The DER private key is threaded into connect_args alongside the timeouts…
    assert isinstance(connect_args.get("private_key"), bytes)
    assert connect_args["login_timeout"] == 10
    # …and the DSN carries no password.
    assert "svc_dataq:" not in str(captured["url"])


def test_adapter_test_disposes_engine_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine([])

    def boom_connect() -> _FakeConn:
        raise RuntimeError("warehouse unreachable")

    engine.connect = boom_connect  # type: ignore[method-assign]
    monkeypatch.setattr("sqlalchemy.create_engine", lambda url, **kw: engine)

    with pytest.raises(RuntimeError, match="warehouse unreachable"):
        SnowflakeConnectionAdapter().test(_CONFIG, "p@ss")
    assert engine.disposed is True


# ───────────────────────── registry ────────────────────────────────


def test_registry_returns_snowflake_adapter() -> None:
    adapter = get_connection_adapter("snowflake")
    assert isinstance(adapter, SnowflakeConnectionAdapter)
    assert isinstance(adapter, ConnectionAdapter)


def test_registry_unknown_type_raises() -> None:
    # All seven CONNECTION_TYPES now have adapters, so probe a type that isn't a
    # valid connection type at all (a post-v1 RDBMS candidate, ADR 0011).
    with pytest.raises(UnsupportedConnectionTypeError, match="mssql"):
        get_connection_adapter("mssql")
