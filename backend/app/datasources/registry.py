"""Connection-type → adapter + runner registry."""

from __future__ import annotations

import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.adls import AdlsConnectionAdapter
from backend.app.datasources.base import (
    BatchSpec,
    CheckRunner,
    ConnectionAdapter,
    ExpiringCredentialAdapter,
    ResolvedTarget,
    SampleSpec,
    TargetShapeError,
)
from backend.app.datasources.flatfile import build_flatfile_runner
from backend.app.datasources.iceberg import IcebergConnectionAdapter, build_iceberg_runner
from backend.app.datasources.s3 import S3ConnectionAdapter
from backend.app.datasources.sampling import SamplingConfigError, parse_sample_spec
from backend.app.datasources.snowflake import SnowflakeConnectionAdapter, build_snowflake_runner
from backend.app.datasources.unity_catalog import (
    UnityCatalogConnectionAdapter,
    build_unity_catalog_runner,
)
from backend.app.orchestration.adf import ADFConnectionAdapter
from backend.app.orchestration.airflow import AirflowConnectionAdapter
from backend.app.orchestration.dbt import DbtConnectionAdapter

log = get_logger(__name__)


class UnsupportedConnectionTypeError(ValueError):
    """Raised when no adapter is registered for a connection type."""


# Datasource and orchestration-provider connection types share this one registry (both implement the
# `ConnectionAdapter` seam); the run path keeps them apart — only datasources get a `CheckRunner`.
_ADAPTERS: dict[str, ConnectionAdapter] = {
    "snowflake": SnowflakeConnectionAdapter(),
    "adls_gen2": AdlsConnectionAdapter(),
    "s3": S3ConnectionAdapter(),
    "unity_catalog": UnityCatalogConnectionAdapter(),
    "iceberg": IcebergConnectionAdapter(),
    "adf": ADFConnectionAdapter(),
    "airflow": AirflowConnectionAdapter(),
    "dbt": DbtConnectionAdapter(),
}


def get_connection_adapter(conn_type: str) -> ConnectionAdapter:
    adapter = _ADAPTERS.get(conn_type)
    if adapter is None:
        raise UnsupportedConnectionTypeError(
            f"No connection adapter registered for type {conn_type!r}"
        )
    return adapter


def destination_fields(conn_type: str) -> dict[str, tuple[str, ...]]:
    """Per credential slot, the config fields that decide where it is SENT (#1401)."""
    adapter = get_connection_adapter(conn_type)
    slots = getattr(adapter, "destination_fields", None)
    if slots is None:
        raise UnsupportedConnectionTypeError(
            f"adapter for type {conn_type!r} declares no 'destination_fields'; every "
            "adapter must name its credential-destination config fields (#1401), "
            "explicitly empty if it has none"
        )
    return {slot: tuple(fields) for slot, fields in slots.items()}


def credential_expiry(
    conn_type: str, config: dict[str, Any], secret: str, **extra_secrets: Any
) -> datetime | None:
    """When this connection's credential stops working (#838), or ``None``."""
    try:
        adapter = get_connection_adapter(conn_type)
        if not isinstance(adapter, ExpiringCredentialAdapter):
            return None
        return adapter.credential_expiry(config, secret, **extra_secrets)
    except Exception:
        log.warning("credential_expiry_unreadable", type=conn_type)
        return None


# ───────────────────────── CheckRunner registry ─────────────────────
#
# Only datasources get a runner (orchestration providers are absent → asking for
# their runner raises). Each builder is normalised to one signature so the worker
# can dispatch through `build_check_runner` without branching on the type. The
# underlying `build_*` take primitives (not the ORM `Connection`) to keep the
# adapters decoupled from `db/`; the caller unpacks the row.


class _RunnerBuilder(Protocol):
    def __call__(
        self,
        *,
        conn_type: str,
        config: dict[str, Any],
        secret_ref: str | None,
        secret_store: SecretStore,
        catalog: str | None,
        sampling: SampleSpec | None,
    ) -> CheckRunner: ...


def _snowflake_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, **_: Any
) -> CheckRunner:
    # `sampling` is swallowed by `**_` on purpose: the Snowflake runner has no sampled mode because
    # it never materialises rows.
    return build_snowflake_runner(config=config, secret_ref=secret_ref, secret_store=secret_store)


def _flatfile_runner(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    sampling: SampleSpec | None = None,
    **_: Any,
) -> CheckRunner:
    return build_flatfile_runner(
        conn_type=conn_type,
        config=config,
        secret_ref=secret_ref,
        secret_store=secret_store,
        sampling=sampling,
    )


def _unity_catalog_runner(
    *,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    catalog: str | None,
    sampling: SampleSpec | None = None,
    **_: Any,
) -> CheckRunner:
    if not catalog:
        raise UnsupportedConnectionTypeError("Unity Catalog run requires a catalog")
    return build_unity_catalog_runner(
        config=config,
        secret_ref=secret_ref,
        secret_store=secret_store,
        catalog=catalog,
        sampling=sampling,
    )


def _iceberg_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, **_: Any
) -> CheckRunner:
    # Iceberg reads natively by ``namespace.table`` identifier (folded into the
    # runner's ``table`` arg upstream), so it needs no ``catalog`` param.
    return build_iceberg_runner(config=config, secret_ref=secret_ref, secret_store=secret_store)


_RUNNER_BUILDERS: dict[str, _RunnerBuilder] = {
    "snowflake": _snowflake_runner,
    "adls_gen2": _flatfile_runner,
    "s3": _flatfile_runner,
    "unity_catalog": _unity_catalog_runner,
    "iceberg": _iceberg_runner,
}


def build_check_runner(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    catalog: str | None = None,
    sampling: SampleSpec | None = None,
) -> CheckRunner:
    """Build the `CheckRunner` for ``conn_type`` from a connection's primitives."""
    builder = _RUNNER_BUILDERS.get(conn_type)
    if builder is None:
        raise UnsupportedConnectionTypeError(f"No check runner registered for type {conn_type!r}")
    return builder(
        conn_type=conn_type,
        config=config,
        secret_ref=secret_ref,
        secret_store=secret_store,
        catalog=catalog,
        sampling=sampling,
    )


def close_check_runner(runner: object) -> None:
    """Release any datasource resources the runner holds — its shared SQL engine
    pool (#427). Runners without a ``close`` (flat-file, Iceberg — nothing pooled
    to release) are a no-op, so callers never branch on the runner type.
    """
    close = getattr(runner, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:
        log.warning(
            "check_runner_close_failed",
            runner_type=type(runner).__name__,
            error_type=type(exc).__name__,
        )


@contextmanager
def owned_runner(runner: CheckRunner) -> Generator[CheckRunner]:
    """Scope a runner's datasource resources to a ``with`` block (#427)."""
    try:
        yield runner
    finally:
        close_check_runner(runner)


# ───────────────── target-shape resolution (one entry per type) ──────────────
#
# #727: this used to be an `if conn_type ==` chain in `services/run_target.py`, a
# second dispatch site outside this registry. Adding a datasource therefore meant
# editing that function TOO, quietly falsifying this module's "adding a datasource
# is one entry here" contract — the Iceberg addition (#716) already had to.
#
# The service layer keeps what is genuinely shared (targetless suites,
# orchestration-provider rejection, the HTTP error contract); only the SHAPE lives
# here, next to the adapter and runner for the same type.


def _require(target: dict[str, Any], field: str, conn_type: str) -> str:
    value = target.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TargetShapeError(f"{conn_type} target requires a {field!r}")
    return value


def _opt(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _flatfile_target(target: dict[str, Any], conn_type: str) -> ResolvedTarget:
    # A batch target (regex `pattern`) resolves to a concrete path at run time; a literal target
    # carries `path`.
    if "pattern" in target and target.get("path"):
        raise TargetShapeError(
            "flat-file target is ambiguous: set either 'path' (literal) or "
            "'pattern' (batch), not both"
        )
    if "pattern" in target:
        return ResolvedTarget(table="", schema=None, catalog=None, batch=_batch_spec(target))
    return ResolvedTarget(table=_require(target, "path", conn_type), schema=None, catalog=None)


def _snowflake_target(target: dict[str, Any], conn_type: str) -> ResolvedTarget:
    return ResolvedTarget(
        table=_require(target, "table", conn_type),
        schema=_opt(target.get("schema")),
        catalog=None,
    )


def _unity_catalog_target(target: dict[str, Any], conn_type: str) -> ResolvedTarget:
    return ResolvedTarget(
        table=_require(target, "table", conn_type),
        schema=_opt(target.get("schema")),
        catalog=_require(target, "catalog", conn_type),
    )


def _iceberg_target(target: dict[str, Any], conn_type: str) -> ResolvedTarget:
    # Iceberg addresses a table by its ``namespace.table`` identifier (the namespace may itself be
    # multi-level, ``a.b``).
    table = _require(target, "table", conn_type)
    namespace = _opt(target.get("namespace"))
    return ResolvedTarget(
        table=f"{namespace}.{table}" if namespace else table, schema=None, catalog=None
    )


#: Flat-file batch selection strategies. Lives beside `_batch_spec` (#727) —
#: it is a property of the flat-file target shape, not of the service layer.
_BATCH_STRATEGIES = {"latest", "specific"}


def _batch_spec(target: dict[str, Any]) -> BatchSpec:
    """Validate + build a flat-file `BatchSpec` from a batch target (422 on bad shape)."""
    pattern = _require(target, "pattern", "flat-file")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise TargetShapeError(f"batch 'pattern' is not a valid regex: {exc}") from exc
    strategy = target.get("strategy", "latest")
    if strategy not in _BATCH_STRATEGIES:
        raise TargetShapeError(
            f"batch strategy must be one of {sorted(_BATCH_STRATEGIES)}; got {strategy!r}"
        )
    batch = target.get("batch")
    if strategy == "specific":
        if not isinstance(batch, str) or not batch.strip():
            raise TargetShapeError("batch strategy 'specific' requires a non-empty 'batch' key")
        if compiled.groups < 1:
            raise TargetShapeError(
                "batch strategy 'specific' needs a capture group in 'pattern' to "
                "extract the batch key"
            )
    prefix = target.get("prefix", "")
    if not isinstance(prefix, str):
        raise TargetShapeError("batch target 'prefix' must be a string")
    return BatchSpec(
        prefix=prefix,
        pattern=pattern,
        strategy=strategy,
        batch=batch if strategy == "specific" else None,
    )


_TARGET_RESOLVERS: dict[str, Callable[[dict[str, Any], str], ResolvedTarget]] = {
    "snowflake": _snowflake_target,
    "unity_catalog": _unity_catalog_target,
    "iceberg": _iceberg_target,
    "adls_gen2": _flatfile_target,
    "s3": _flatfile_target,
}


#: Datasources whose runner materialises rows in the worker, and which therefore accept a
#: ``sampling`` block on their run target (#595).
SAMPLING_CAPABLE_TYPES: frozenset[str] = frozenset({"adls_gen2", "s3", "unity_catalog"})


def _target_sampling(target: dict[str, Any], conn_type: str) -> SampleSpec | None:
    """Parse + gate the optional ``sampling`` block on a run target."""
    raw = target.get("sampling")
    if raw is None:
        return None
    if conn_type not in SAMPLING_CAPABLE_TYPES:
        raise TargetShapeError(
            f"{conn_type} targets do not take a 'sampling' block — this datasource runs "
            "checks by pushdown and never loads rows into the worker, so sampling would "
            f"change nothing. Supported: {', '.join(sorted(SAMPLING_CAPABLE_TYPES))}"
        )
    try:
        return parse_sample_spec(raw)
    except SamplingConfigError as exc:
        raise TargetShapeError(str(exc)) from exc


def resolve_target_shape(conn_type: str, target: dict[str, Any]) -> ResolvedTarget:
    """The datasource-specific half of target resolution, or raise."""
    resolver = _TARGET_RESOLVERS.get(conn_type)
    if resolver is None:
        raise TargetShapeError(f"connection type {conn_type!r} has no run path (not a datasource)")
    resolved = resolver(target, conn_type)
    sampling = _target_sampling(target, conn_type)
    return resolved if sampling is None else replace(resolved, sampling=sampling)
