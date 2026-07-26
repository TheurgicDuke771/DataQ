"""Connection-type → adapter + runner registry.

The single place that maps a ``Connection.type`` to its `ConnectionAdapter`
(`get_connection_adapter`, all six types) and — for datasources only — to its
`CheckRunner` builder (`build_check_runner`). Service/worker code dispatches
through these and never branches on ``connection.type`` itself; adding a
datasource is an entry here plus the adapter/runner, nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
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
    TargetShapeError,
)
from backend.app.datasources.flatfile import build_flatfile_runner
from backend.app.datasources.iceberg import IcebergConnectionAdapter, build_iceberg_runner
from backend.app.datasources.s3 import S3ConnectionAdapter
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


# Datasource and orchestration-provider connection types share this one registry
# (both implement the `ConnectionAdapter` seam); the run path keeps them apart —
# only datasources get a `CheckRunner`. ADF, Airflow, and dbt are orchestration
# providers, so their adapters live under `orchestration/`, not `datasources/`
# (CLAUDE.md §4).
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


def credential_expiry(
    conn_type: str, config: dict[str, Any], secret: str, **extra_secrets: Any
) -> datetime | None:
    """When this connection's credential stops working (#838), or ``None``.

    The one place that asks an adapter about credential lifetime, so callers never
    branch on `connection.type`. ``None`` means **unknown** — the adapter doesn't
    implement `ExpiringCredentialAdapter`, the credential carries no expiry, or the
    read failed. It never means "does not expire".

    Fail-soft by construction: a credential lifetime is an advisory signal, so a
    surprising credential shape — or an unregistered type — must not break the
    caller (connection CRUD, the daily sweep). The failure is logged with the
    connection **type** but **without the secret and without the exception text**:
    a malformed credential's error can quote the credential itself (the #536
    precedent), while the type is a non-secret identifier and the only thing that
    makes the line actionable.
    """
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
    ) -> CheckRunner: ...


def _snowflake_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, **_: Any
) -> CheckRunner:
    return build_snowflake_runner(config=config, secret_ref=secret_ref, secret_store=secret_store)


def _flatfile_runner(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    **_: Any,
) -> CheckRunner:
    return build_flatfile_runner(
        conn_type=conn_type, config=config, secret_ref=secret_ref, secret_store=secret_store
    )


def _unity_catalog_runner(
    *,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    catalog: str | None,
    **_: Any,
) -> CheckRunner:
    if not catalog:
        raise UnsupportedConnectionTypeError("Unity Catalog run requires a catalog")
    return build_unity_catalog_runner(
        config=config, secret_ref=secret_ref, secret_store=secret_store, catalog=catalog
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
) -> CheckRunner:
    """Build the `CheckRunner` for ``conn_type`` from a connection's primitives.

    Dispatches by type to the registered builder. Raises
    `UnsupportedConnectionTypeError` for a type with no runner (e.g. an
    orchestration provider, or Unity Catalog without a ``catalog``).
    """
    builder = _RUNNER_BUILDERS.get(conn_type)
    if builder is None:
        raise UnsupportedConnectionTypeError(f"No check runner registered for type {conn_type!r}")
    return builder(
        conn_type=conn_type,
        config=config,
        secret_ref=secret_ref,
        secret_store=secret_store,
        catalog=catalog,
    )


def close_check_runner(runner: object) -> None:
    """Release any datasource resources the runner holds — its shared SQL engine
    pool (#427). Runners without a ``close`` (flat-file, Iceberg — nothing pooled
    to release) are a no-op, so callers never branch on the runner type.

    Best-effort by design: this runs inside the run path's ``finally``, so a
    raising ``dispose()`` must never replace the in-flight result (it would turn
    an already-terminal run into a task error that skips incident sync + alert
    dispatch, or mask a dry-run's mapped 422/502 as a 500). Failures are logged,
    never raised."""
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
    """Scope a runner's datasource resources to a ``with`` block (#427).

    The run-owning paths (worker suite run, dry-run) wrap everything after
    `build_check_runner` in this, so the shared engine pool is released on every
    exit — normal, handled-failure, or propagating exception — without threading
    a second function signature or hand-rolled try/finally through each caller.
    """
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
    # A batch target (regex `pattern`) resolves to a concrete path at run time; a
    # literal target carries `path`. Mutually exclusive — both set is an ambiguous
    # target, not a silent batch win.
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
    # Iceberg addresses a table by its ``namespace.table`` identifier (the namespace
    # may itself be multi-level, ``a.b``). Fold the optional ``namespace`` into the
    # identifier the native runner passes to ``catalog.load_table`` — carried in
    # ``table``; Iceberg has no separate SQL schema, so schema/catalog stay None
    # (ADR 0030).
    table = _require(target, "table", conn_type)
    namespace = _opt(target.get("namespace"))
    return ResolvedTarget(
        table=f"{namespace}.{table}" if namespace else table, schema=None, catalog=None
    )


#: Flat-file batch selection strategies. Lives beside `_batch_spec` (#727) —
#: it is a property of the flat-file target shape, not of the service layer.
_BATCH_STRATEGIES = {"latest", "specific"}


def _batch_spec(target: dict[str, Any]) -> BatchSpec:
    """Validate + build a flat-file `BatchSpec` from a batch target (422 on bad shape).

    Validates at save time what would otherwise only fail (or silently skip
    forever) at run time: the regex must compile, and a ``specific`` strategy needs
    a capture group in the pattern to extract the batch key — without one,
    `resolve_batch` can never match a key, so every run would skip indefinitely and
    mask the misconfiguration.
    """
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


def resolve_target_shape(conn_type: str, target: dict[str, Any]) -> ResolvedTarget:
    """The datasource-specific half of target resolution, or raise.

    A type with no entry has no run path — every orchestration provider, and any
    datasource whose author forgot this registration. Raising is the point: the
    alternative is a suite that saves and then fails at run time.
    """
    resolver = _TARGET_RESOLVERS.get(conn_type)
    if resolver is None:
        raise TargetShapeError(f"connection type {conn_type!r} has no run path (not a datasource)")
    return resolver(target, conn_type)
