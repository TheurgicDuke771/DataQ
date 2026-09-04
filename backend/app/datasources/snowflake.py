"""Snowflake datasource adapter (GX Core 1.17)."""

from __future__ import annotations

import base64
import json
from typing import Any, ClassVar, Literal
from urllib.parse import quote_plus

import great_expectations as gx
from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome, CheckSpec, MonitorSpec, SuiteOutcome

# GX-translation machinery is shared across runners (see `gx_runner`); re-exported
# here so existing importers (and tests) keep resolving these from `snowflake`.
from backend.app.datasources.gx_runner import (
    UnknownExpectationError,
    _expectation_class_name,
    _to_gx_expectation,
    run_expectations,
    to_suite_outcome,
)
from backend.app.datasources.monitors import FRESHNESS, VOLUME, run_monitors_over_engine
from backend.app.datasources.snowflake_dmf import (
    DMF_ENGINE,
    evaluate_dmf_check,
    probe_dmf_capability,
)
from backend.app.datasources.sql import LazyEngine, fold_reflection_keyed_columns

__all__ = [
    "SnowflakeCheckRunner",
    "SnowflakeConfig",
    "SnowflakeConnectionAdapter",
    "UnknownExpectationError",
    "_expectation_class_name",
    "_to_gx_expectation",
    "build_connect_args",
    "build_connection_string",
    "build_snowflake_runner",
    "to_suite_outcome",
]


class SnowflakeConfig(BaseModel):
    """Non-secret Snowflake connection config (the password comes from secrets)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    account: str
    user: str
    database: str
    schema_: str = Field(alias="schema")
    warehouse: str
    role: str | None = None
    # Warehouse inventory sync opt-in (#919, ADR 0040): when true, the daily sync enumerates this
    # connection's database into `assets`.
    inventory_sync: bool = False
    # Auth method. 'password' (default — back-compat for existing configs that carry no auth_type)
    # puts the password in the DSN. 'key_pair' authenticates with an RSA private key passed as
    # `private_key` connect-arg, and the DSN carries no password; the secret is either a bare PEM
    # key or the JSON payload for passphrase-protected keys (see `_parse_key_pair_secret`).
    auth_type: Literal["password", "key_pair"] = "password"

    @model_validator(mode="after")
    def _requires_role(self) -> SnowflakeConfig:
        """Every Snowflake connection must carry a role, whatever the auth type."""
        if not self.role:
            raise ValueError("'role' is required (GX mandates it for every suite run)")
        return self


def _parse_key_pair_secret(secret: str) -> tuple[str, bytes | None]:
    """Split a key-pair secret payload into (PEM key, passphrase bytes or None)."""
    if not secret.lstrip().startswith("{"):
        return secret, None
    try:
        payload = json.loads(secret)
    except json.JSONDecodeError as exc:
        raise ValueError("key-pair secret payload is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("private_key"), str):
        raise ValueError("key-pair secret payload must carry a 'private_key' string")
    passphrase = payload.get("passphrase")
    if passphrase is not None and not isinstance(passphrase, str):
        raise ValueError("key-pair secret 'passphrase' must be a string")
    return payload["private_key"], passphrase.encode() if passphrase else None


def _private_key_der(secret: str) -> bytes:
    """Load the key-pair secret → DER PKCS8 bytes."""
    pem, passphrase = _parse_key_pair_secret(secret)
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=passphrase)
    except TypeError as exc:
        # cryptography reports passphrase-presence mismatches (encrypted key without a passphrase,
        # or a passphrase for an unencrypted key) as TypeError.
        raise ValueError(str(exc)) from exc
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def build_connection_string(config: SnowflakeConfig, secret: str) -> str:
    """Assemble a snowflake-sqlalchemy URL. User/password/params are URL-encoded."""
    params = {"warehouse": config.warehouse}
    if config.role:
        params["role"] = config.role
    query = "&".join(f"{key}={quote_plus(value)}" for key, value in params.items())
    credentials = (
        quote_plus(config.user)
        if config.auth_type == "key_pair"
        else f"{quote_plus(config.user)}:{quote_plus(secret)}"
    )
    return f"snowflake://{credentials}@{config.account}/{config.database}/{config.schema_}?{query}"


def build_connect_args(config: SnowflakeConfig, secret: str) -> dict[str, Any]:
    """SQLAlchemy `connect_args` carrying the key-pair credential, if any."""
    if config.auth_type == "key_pair":
        return {"private_key": _private_key_der(secret)}
    return {}


# GX's compound_columns_unique metric — and the unexpected_index_column_names lookup every
# map expectation's row locator rides — index the REFLECTED table's columns, whose keys
# snowflake-sqlalchemy rewrites via `normalize_name` (all-caps → lowercase, but only when the
# lowered form needs no quoting: reserved words and quote-requiring names keep their case).
# A user's all-caps spelling therefore KeyErrors on live Snowflake only (#1616). Folding with
# the dialect's own `normalize_name` mirrors the reflection rewrite by construction.
_REFLECTION_KEYED_TYPES = frozenset({"expect_compound_columns_to_be_unique"})


def _reflection_key(name: str) -> str:
    from snowflake.sqlalchemy.snowdialect import SnowflakeDialect

    return SnowflakeDialect().normalize_name(name) or name


def _fold_reflection_keyed_columns(checks: list[CheckSpec]) -> list[CheckSpec]:
    return fold_reflection_keyed_columns(
        checks, reflection_keyed_types=_REFLECTION_KEYED_TYPES, normalize_name=_reflection_key
    )


class SnowflakeCheckRunner:
    """`CheckRunner` for Snowflake. Building the asset connects to the warehouse."""

    # Runner-advertised monitor capability (#429): EXPLICITLY what this runner implements — never
    # frozenset(MONITOR_KINDS).
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME})
    # Native engines this runner evaluates (ADR 0036): the run path routes a check whose `engine` is
    # advertised here to `run_native_check`; anything else lands as a classified per-check error.
    supported_native_engines: ClassVar[frozenset[str]] = frozenset({DMF_ENGINE})

    def __init__(self, config: SnowflakeConfig, secret: str) -> None:
        self._config = config
        self._connection_string = build_connection_string(config, secret)
        # Key-pair auth: the loaded DER private key (under 'private_key'), consumed by the shared
        # engine's connect-args and re-encoded for the GX kwargs form in run_checks.
        self._connect_args = build_connect_args(config, secret)
        # The runner's ONE lazily-built engine (#427) — every non-GX SQL touchpoint shares it (and
        # its pooled session) instead of paying a fresh engine + auth handshake per call.
        self._engine = LazyEngine(self._build_engine)

    def _build_engine(self) -> Any:
        from sqlalchemy import create_engine

        # pool_pre_ping: the pooled session can sit idle across a long GX validation in a mixed
        # suite — revalidate on checkout so a warehouse-side idle reap surfaces as a fresh connect.
        return create_engine(
            self._connection_string,
            connect_args=self._connect_args or {},
            pool_pre_ping=True,
        )

    def close(self) -> None:
        """Dispose the shared engine's pool. Idempotent; a no-op if never used."""
        self._engine.close()

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        context = gx.get_context(mode="ephemeral")
        if self._config.auth_type == "key_pair":
            # GX 1.17's supported key-pair form (KeyPairConnectionDetails): the connection as
            # keyword args with a base64-DER private_key.
            datasource = context.data_sources.add_snowflake(
                name=f"sf-{table}",
                account=self._config.account,
                user=self._config.user,
                database=self._config.database,
                schema=self._config.schema_,
                warehouse=self._config.warehouse,
                role=self._config.role,
                private_key=base64.standard_b64encode(self._connect_args["private_key"]).decode(),
            )
        else:
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
            context,
            batch_definition=batch_definition,
            checks=_fold_reflection_keyed_columns(checks),
            name=f"suite-{table}",
            # index_columns deliberately NOT folded: live-verified 2026-08-28 that GX's
            # unexpected_index_column_names path accepts the authored (upper) casing and keys
            # the locators by it — folding would lowercase the locator keys users see.
            index_columns=index_columns,
        )

    def run_native_check(
        self,
        *,
        kind: str,
        expectation_type: str,
        config: dict[str, Any],
        table: str,
        schema: str | None,
    ) -> CheckOutcome:
        """Evaluate ONE dmf-engine check via ad-hoc system-DMF SQL (ADR 0036)."""
        from sqlalchemy import text

        with self._engine.get().connect() as conn:
            return evaluate_dmf_check(
                lambda statement: conn.execute(text(statement)).scalar(),
                kind=kind,
                expectation_type=expectation_type,
                config=config,
                table=table,
                schema=schema or self._config.schema_,
            )

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors via scalar SQL aggregates (no GX)."""
        return run_monitors_over_engine(
            self._engine.get(),
            table=table,
            schema=schema or self._config.schema_,
            catalog=None,
            monitors=monitors,
        )


def build_snowflake_runner(
    *,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
) -> SnowflakeCheckRunner:
    """Build a runner from a `Connection` row's config + secret_ref."""
    if not secret_ref:
        raise ValueError("Snowflake connection requires secret_ref for the password / private key")
    sf_config = SnowflakeConfig.model_validate(config)
    secret = secret_store.get(secret_ref)
    return SnowflakeCheckRunner(sf_config, secret)


# Snowflake connector timeouts (seconds) for the connectivity test — fail fast
# rather than hanging the request thread on an unreachable account.
_TEST_LOGIN_TIMEOUT = 10
_TEST_NETWORK_TIMEOUT = 10


class SnowflakeConnectionAdapter:
    """`ConnectionAdapter` for Snowflake — config validation + a SELECT 1 test."""

    # #1401: `account` becomes `<account>.snowflakecomputing.com`, so it decides which host receives
    # the password/key-pair.
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {"secret": ("account",)}

    def validate_config(self, raw: dict[str, Any]) -> SnowflakeConfig:
        return SnowflakeConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Open a connection and run ``SELECT 1``; raise on any failure."""
        if secret is None:
            raise ValueError("a credential is required to test a snowflake connection")
        from sqlalchemy import create_engine, text

        config = self.validate_config(raw)
        engine = create_engine(
            build_connection_string(config, secret),
            connect_args={
                "login_timeout": _TEST_LOGIN_TIMEOUT,
                "network_timeout": _TEST_NETWORK_TIMEOUT,
                **build_connect_args(config, secret),
            },
        )
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()

    def probe_dmf(self, raw: dict[str, Any], secret: str | None) -> dict[str, Any]:
        """DMF availability (#1867), on an ALREADY-verified-live connection —
        callers only invoke this after `test` succeeds.
        """
        from sqlalchemy import create_engine, text

        if secret is None:
            raise ValueError("a credential is required to probe DMF availability")
        config = self.validate_config(raw)
        engine = create_engine(
            build_connection_string(config, secret),
            connect_args={
                "login_timeout": _TEST_LOGIN_TIMEOUT,
                "network_timeout": _TEST_NETWORK_TIMEOUT,
                **build_connect_args(config, secret),
            },
        )
        try:
            with engine.connect() as conn:
                return probe_dmf_capability(lambda stmt: conn.execute(text(stmt)).scalar())
        finally:
            engine.dispose()
