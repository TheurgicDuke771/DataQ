"""Tests for the Snowflake GX adapter — the GX-free, deterministic parts.

`SnowflakeCheckRunner.run_checks` connects to a live warehouse at asset-build
time, so it is not unit-tested here (deferred follow-up). Everything else —
config parsing, connection-string building, expectation translation, and the
GX-result → DTO mapping — is covered. `to_suite_outcome` is exercised against a
*real* GX validation result (pandas batch, no Snowflake) so the test also guards
the GX 1.17 result shape this adapter depends on (`.type`, injected `batch_id`).
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.datasources.base import CheckRunner, CheckSpec
from backend.app.datasources.snowflake import (
    SnowflakeCheckRunner,
    SnowflakeConfig,
    UnknownExpectationError,
    _expectation_class_name,
    _to_gx_expectation,
    build_connection_string,
    build_snowflake_runner,
    to_suite_outcome,
)

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


def _fake_check_result(*, success: bool, type_: str, kwargs: dict, result: dict) -> SimpleNamespace:
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
    assert not_null.sample_failures["unexpected_count"] == 1
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
