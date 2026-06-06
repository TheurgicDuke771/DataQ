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
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckSpec, SuiteOutcome

# GX-translation machinery is shared across runners (see `gx_runner`); re-exported
# here so existing importers (and tests) keep resolving these from `snowflake`.
from backend.app.datasources.gx_runner import (
    UnknownExpectationError,
    _expectation_class_name,
    _to_gx_expectation,
    run_expectations,
    to_suite_outcome,
)

__all__ = [
    "SnowflakeCheckRunner",
    "SnowflakeConfig",
    "SnowflakeConnectionAdapter",
    "UnknownExpectationError",
    "_expectation_class_name",
    "_to_gx_expectation",
    "build_connection_string",
    "build_snowflake_runner",
    "to_suite_outcome",
]


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
        # The table asset resolves its own batch, so no batch_parameters; the
        # ephemeral context makes the fixed suite/vd names safe across runs.
        batch_definition = asset.add_batch_definition_whole_table(name="whole_table")
        return run_expectations(
            context, batch_definition=batch_definition, checks=checks, name=f"suite-{table}"
        )


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
