"""Snowflake datasource adapter (GX Core 1.17).

All Great-Expectations-specific machinery for Snowflake lives here, behind the
`CheckRunner` seam in ``base.py`` — per CLAUDE.md, the GX version-specific API
must not leak into the suite / check / result layer (GX v1 has drifted across
point releases).

The full GX chain (``add_snowflake`` → ``add_table_asset`` →
``add_batch_definition_whole_table`` → ``ValidationDefinition.run``) connects to
Snowflake at asset-build time, so ``run_checks`` cannot run without a live
warehouse. Tests therefore exercise the GX-free parts directly — config
validation, connection-string building, the snake_case→GX-class translation,
and the GX-result→`CheckOutcome` mapping (fed a canned result) — and inject a
fake `CheckRunner` elsewhere. End-to-end validation against a real Snowflake DEV
warehouse is a tracked follow-up.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

import great_expectations as gx
import great_expectations.expectations as gxe
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome

# GX result keys that describe failing rows — copied into CheckOutcome.sample_failures.
# These may contain real data, so they only ever reach logs via the redactor.
_SAMPLE_KEYS = ("partial_unexpected_list", "unexpected_count", "unexpected_percent")

# GX injects internal bookkeeping keys into expectation_config.kwargs at run
# time (e.g. batch_id); strip them so expected_value persists only the check's
# own parameters.
_GX_INTERNAL_KWARGS = frozenset({"batch_id"})


class UnknownExpectationError(ValueError):
    """Raised when a check's expectation_type has no matching GX expectation."""


class SnowflakeConfig(BaseModel):
    """Non-secret Snowflake connection config (the password comes from secrets).

    Maps from ``Connection.config``. ``schema`` is aliased to ``schema_`` to
    avoid shadowing pydantic's ``BaseModel.schema``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    account: str
    user: str
    database: str
    schema_: str = Field(alias="schema")
    warehouse: str
    role: str | None = None


def build_connection_string(config: SnowflakeConfig, password: str) -> str:
    """Assemble a snowflake-sqlalchemy URL. User/password/params are URL-encoded."""
    params = {"warehouse": config.warehouse}
    if config.role:
        params["role"] = config.role
    query = "&".join(f"{key}={quote_plus(value)}" for key, value in params.items())
    return (
        f"snowflake://{quote_plus(config.user)}:{quote_plus(password)}"
        f"@{config.account}/{config.database}/{config.schema_}?{query}"
    )


def _expectation_class_name(expectation_type: str) -> str:
    """snake_case GX type → PascalCase class name.

    ``expect_column_values_to_not_be_null`` → ``ExpectColumnValuesToNotBeNull``.
    """
    return "".join(part.title() for part in expectation_type.split("_"))


def _to_gx_expectation(spec: CheckSpec) -> Any:
    class_name = _expectation_class_name(spec.expectation_type)
    expectation_cls = getattr(gxe, class_name, None)
    if expectation_cls is None:
        raise UnknownExpectationError(
            f"Unknown expectation_type {spec.expectation_type!r} (no gx class {class_name!r})"
        )
    return expectation_cls(**spec.kwargs)


def _extract_sample_failures(result: dict[str, Any]) -> dict[str, Any] | None:
    sample = {key: result[key] for key in _SAMPLE_KEYS if key in result}
    return sample or None


def _expected_value(kwargs: Any) -> dict[str, Any] | None:
    cleaned = {key: value for key, value in dict(kwargs).items() if key not in _GX_INTERNAL_KWARGS}
    return cleaned or None


def to_suite_outcome(gx_result: Any) -> SuiteOutcome:
    """Map a GX ExpectationSuiteValidationResult onto our GX-agnostic DTO.

    Kept module-level (not a private method) so it is unit-testable with a
    constructed GX result, no Snowflake connection required.
    """
    outcomes: list[CheckOutcome] = []
    for check_result in gx_result.results:
        config = check_result.expectation_config
        detail: dict[str, Any] = check_result.result or {}
        observed = (
            {"observed_value": detail["observed_value"]} if "observed_value" in detail else None
        )
        outcomes.append(
            CheckOutcome(
                expectation_type=config.type,
                success=bool(check_result.success),
                observed_value=observed,
                expected_value=_expected_value(config.kwargs) if config.kwargs else None,
                sample_failures=_extract_sample_failures(detail),
            )
        )
    return SuiteOutcome(success=bool(gx_result.success), checks=outcomes)


class SnowflakeCheckRunner:
    """`CheckRunner` for Snowflake. Building the asset connects to the warehouse."""

    def __init__(self, config: SnowflakeConfig, password: str) -> None:
        self._config = config
        self._connection_string = build_connection_string(config, password)

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
    ) -> SuiteOutcome:
        context = gx.get_context(mode="ephemeral")
        datasource = context.data_sources.add_snowflake(
            name=f"sf-{table}",
            connection_string=self._connection_string,
        )
        asset = datasource.add_table_asset(
            name=table,
            table_name=table,
            schema_name=schema or self._config.schema_,
        )
        batch_definition = asset.add_batch_definition_whole_table(name="whole_table")
        # GX 1.17 requires the suite and validation definition to be registered
        # on the context before run(); a free-standing ValidationDefinition.run()
        # raises ValidationDefinitionRelatedResourcesFreshnessError. The context
        # is ephemeral (per-run), so the fixed names never collide across runs.
        suite = context.suites.add(
            gx.ExpectationSuite(
                name=f"suite-{table}",
                expectations=[_to_gx_expectation(check) for check in checks],
            )
        )
        validation_definition = context.validation_definitions.add(
            gx.ValidationDefinition(
                name=f"vd-{table}",
                data=batch_definition,
                suite=suite,
            )
        )
        result = validation_definition.run(result_format="COMPLETE")
        return to_suite_outcome(result)


def build_snowflake_runner(
    *,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
) -> SnowflakeCheckRunner:
    """Build a runner from a `Connection` row's config + secret_ref.

    Takes primitives (not the ORM model) to keep the adapter decoupled from the
    DB layer.
    """
    if not secret_ref:
        raise ValueError("Snowflake connection requires secret_ref for the password")
    sf_config = SnowflakeConfig.model_validate(config)
    password = secret_store.get(secret_ref)
    return SnowflakeCheckRunner(sf_config, password)


# Snowflake connector timeouts (seconds) for the connectivity test — fail fast
# rather than hanging the request thread on an unreachable account.
_TEST_LOGIN_TIMEOUT = 10
_TEST_NETWORK_TIMEOUT = 10


class SnowflakeConnectionAdapter:
    """`ConnectionAdapter` for Snowflake — config validation + a SELECT 1 test."""

    def validate_config(self, raw: dict[str, Any]) -> SnowflakeConfig:
        return SnowflakeConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str) -> None:
        """Open a connection and run ``SELECT 1``; raise on any failure.

        Deliberately GX-free — a lightweight connectivity probe, not a suite run.
        """
        from sqlalchemy import create_engine, text

        config = self.validate_config(raw)
        engine = create_engine(
            build_connection_string(config, secret),
            connect_args={
                "login_timeout": _TEST_LOGIN_TIMEOUT,
                "network_timeout": _TEST_NETWORK_TIMEOUT,
            },
        )
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
