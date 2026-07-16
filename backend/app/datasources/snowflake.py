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
from backend.app.datasources.monitors import MONITOR_KINDS, run_monitors_over_engine
from backend.app.datasources.sql import LazyEngine

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
    # Auth method. 'password' (default — back-compat for existing configs that
    # carry no auth_type) puts the password in the DSN. 'key_pair' authenticates
    # with an RSA private key passed as `private_key` connect-arg, and the DSN
    # carries no password; the secret is either a bare PEM key or the JSON
    # payload for passphrase-protected keys (see `_parse_key_pair_secret`).
    # Both auth methods share the same SecretStore `secret_ref`.
    auth_type: Literal["password", "key_pair"] = "password"

    @model_validator(mode="after")
    def _key_pair_requires_role(self) -> SnowflakeConfig:
        """Key-pair connections must carry a role.

        GX's key-pair form (`KeyPairConnectionDetails`, #195) mandates one, so a
        role-less key-pair connection could never run a suite. Enforcing it here
        makes the failure a clear 422 at create/edit/test time instead of an
        opaque failed run later. Password auth keeps role optional.
        """
        if self.auth_type == "key_pair" and not self.role:
            raise ValueError("key-pair auth requires 'role' (suite runs mandate it)")
        return self


def _parse_key_pair_secret(secret: str) -> tuple[str, bytes | None]:
    """Split a key-pair secret payload into (PEM key, passphrase bytes or None).

    Two shapes are accepted: a bare PEM string (an unencrypted key — the
    original v1 form, unchanged) or a JSON object
    ``{"private_key": "<PEM>", "passphrase": "<str>"}`` for passphrase-protected
    keys. One SecretStore entry carries both parts so the connection keeps a
    single `secret_ref` and rotation via re-auth stays atomic (#194).

    Error messages never include payload content — they can carry key material.
    """
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
    """Load the key-pair secret → DER PKCS8 bytes.

    snowflake-connector's `private_key` connect-arg wants DER PKCS8, not PEM.
    Passphrase-protected keys arrive as the JSON payload
    (see `_parse_key_pair_secret`); bare PEM means an unencrypted key.
    """
    pem, passphrase = _parse_key_pair_secret(secret)
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=passphrase)
    except TypeError as exc:
        # cryptography reports passphrase-presence mismatches (encrypted key
        # without a passphrase, or a passphrase for an unencrypted key) as
        # TypeError; normalise to ValueError so callers see one failure type.
        # Its messages state the mismatch only — no key/passphrase material.
        raise ValueError(str(exc)) from exc
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def build_connection_string(config: SnowflakeConfig, secret: str) -> str:
    """Assemble a snowflake-sqlalchemy URL. User/password/params are URL-encoded.

    For key-pair auth the DSN carries **no password** (the key rides in
    connect-args, see `build_connect_args`); ``secret`` is then ignored here.
    """
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
    """SQLAlchemy `connect_args` carrying the key-pair credential, if any.

    Empty for password auth (the password is in the DSN). For key-pair, the
    loaded DER private key under `private_key` (the snowflake-connector arg).
    """
    if config.auth_type == "key_pair":
        return {"private_key": _private_key_der(secret)}
    return {}


class SnowflakeCheckRunner:
    """`CheckRunner` for Snowflake. Building the asset connects to the warehouse."""

    # Runner-advertised monitor capability (#429) — the run path gates on this.
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset(MONITOR_KINDS)

    def __init__(self, config: SnowflakeConfig, secret: str) -> None:
        self._config = config
        self._connection_string = build_connection_string(config, secret)
        # Key-pair auth: the loaded DER private key (under 'private_key'),
        # consumed by the shared engine's connect-args and re-encoded for the
        # GX kwargs form in run_checks; empty for password auth.
        self._connect_args = build_connect_args(config, secret)
        # The runner's ONE lazily-built engine (#427) — every non-GX SQL
        # touchpoint shares it (and its pooled session) instead of paying a
        # fresh engine + auth handshake per call. Disposed by `close()`; the
        # run path owns that lifecycle via `registry.owned_runner`. (GX's
        # `run_checks` still builds its own engine internally from the
        # connection string — not injectable.)
        self._engine = LazyEngine(self._build_engine)

    def _build_engine(self) -> Any:
        from sqlalchemy import create_engine

        # pool_pre_ping: the pooled session can sit idle across a long GX
        # validation in a mixed suite — revalidate on checkout so a
        # warehouse-side idle reap surfaces as a fresh connect, not a dead
        # connection failing the monitors.
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
            # GX 1.17's supported key-pair form (KeyPairConnectionDetails): the
            # connection as keyword args with a base64-DER private_key. The old
            # kwargs['connect_args'] route never passed GX's datasource
            # validation for a passwordless DSN — it was broken, not just
            # deprecated (#195). Live-verified 2026-07-04: b64-DER works; a PEM
            # string does not. GX requires `role` here; SnowflakeConfig's
            # validator guarantees key-pair configs carry one.
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
            checks=checks,
            name=f"suite-{table}",
            index_columns=index_columns,
        )

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors via scalar SQL aggregates (no GX).

        Runs over the runner's shared engine (#427 — one connection per run, no
        per-call engine); Snowflake addresses the target as ``schema.table`` (the
        database is in the DSN), so no catalog. A connection-level failure (can't
        reach the warehouse) propagates, failing the run like the GX path; a bad
        monitor errors only itself."""
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
    """Build a runner from a `Connection` row's config + secret_ref.

    Takes primitives (not the ORM model) to keep the adapter decoupled from the
    DB layer.
    """
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

    def validate_config(self, raw: dict[str, Any]) -> SnowflakeConfig:
        return SnowflakeConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str, **_: Any) -> None:
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
                **build_connect_args(config, secret),
            },
        )
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
