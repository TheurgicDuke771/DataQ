"""Apache Iceberg connection adapter + native read runner (ADR 0030, #716).

A datasource (CLAUDE.md §4): DQ checks run against an Iceberg **table** read
**natively** — `pyiceberg` resolves the current snapshot → applies v2 deletes →
reconciles schema by field-id → materialises a DataFrame, which GX validates.
This is the no-query-engine path; engine-registered Iceberg tables (a Snowflake
``CREATE ICEBERG TABLE`` or a Databricks UniForm/foreign catalog table) already
work with **zero code** under the existing ``snowflake`` / ``unity_catalog``
connections, because those runners speak SQL to the engine and never see the file
format (ADR 0030 §1).

Format-version 2 is the baseline; v3 (deletion vectors, row lineage) is deferred
behind a later capability gate (ADR 0030 §2, #717).

**Self-contained (Option A, ADR 0030 §3):** the connection carries its catalog
config in ``Connection.config`` **and its own** storage/catalog credential in a
single ``secret_ref`` — no reference to a separate ADLS/S3 connection. The one
secret is injected into ``load_catalog`` as the property named by
``secret_property`` (e.g. ``token`` for a REST catalog, ``s3.secret-access-key``
for S3-backed storage), so one credential slot serves any backend without
hardcoding a cloud. A credential-less catalog (local warehouse, vended-credentials
REST) may omit the secret entirely (like the ADLS/S3 adapters).

**Materialisation (ADR 0030 §1 / #716):** the exact-expectation path goes through
``scan().to_arrow()`` → ``to_pandas(types_mapper=pd.ArrowDtype)`` — Arrow-backed
pandas dtypes, keeping parity with ``FlatFileCheckRunner``'s
``dtype_backend="pyarrow"`` — **not** the bare ``scan().to_pandas()`` shortcut
(which drops to numpy dtypes). Monitors avoid materialising the whole table:
volume is ``scan().count()`` and freshness scans only its one timestamp column.

``pyiceberg`` is imported lazily (like the other adapters' clients) so importing
this module stays cheap and the dependency only loads on a live Iceberg path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

import great_expectations as gx
from pydantic import BaseModel, ConfigDict, model_validator

from backend.app.core.secrets import SecretStore
from backend.app.core.uri_credentials import inject_uri_password, uri_password
from backend.app.datasources.base import CheckOutcome, CheckSpec, MonitorSpec, SuiteOutcome
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import (
    FRESHNESS,
    MONITOR_KINDS,
    VOLUME,
    MonitorConfigError,
    run_monitor_specs,
    validate_monitor_config,
)

# Catalog backends pyiceberg's ``load_catalog`` understands. REST + SQL are the
# self-hostable baseline; Glue/Hive are cloud/metastore-backed. All read Iceberg
# v2 the same way once the catalog is loaded — the type only changes the connect
# properties.
IcebergCatalogType = Literal["rest", "sql", "glue", "hive"]

# Catalog types whose connection needs a URI (REST endpoint, SQL/metastore URI).
# Glue is region-scoped via ``properties`` (e.g. ``{"glue.region": "us-east-1"}``),
# not a URI.
_URI_REQUIRED: frozenset[str] = frozenset({"rest", "sql", "hive"})


class IcebergConfig(BaseModel):
    """Non-secret Iceberg catalog + storage config (the credential is the secret).

    Maps from ``Connection.config``. ``catalog_name`` is the pyiceberg catalog
    name; ``catalog_type`` selects the backend; ``catalog_uri`` points at it
    (required for rest/sql/hive). ``warehouse`` is the table warehouse/storage
    root. ``properties`` carries any extra non-secret catalog + storage options
    (e.g. ``{"s3.region": "us-east-1", "adls.account-name": "acct"}``).
    ``secret_property`` names the single ``load_catalog`` property the connection's
    secret fills — typically the *storage* credential (e.g. ``adls.account-key``).

    **A SQL catalog needs a SECOND credential** (the catalog DB password), and
    ``pyiceberg`` only accepts it inside the SQLAlchemy ``uri``. Putting it there in
    config is what caused #754/#826: `config` is non-secret, so the password was
    persisted, copied into the asset's OpenLineage namespace, served by the read API,
    **rendered in the UI**, and sent to third-party catalogs in a query string.

    So ``catalog_uri`` must ship **credential-less** (username is fine — that's an
    identifier, not a credential) and ``catalog_secret_name`` names a SecretStore
    entry holding the password. The caller resolves it and hands it in; it is injected
    into the URI's userinfo at catalog-load time and never persisted. A password left
    inline in ``catalog_uri`` is rejected outright by the validator below.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_name: str = "default"
    catalog_type: IcebergCatalogType
    catalog_uri: str | None = None
    warehouse: str | None = None
    properties: dict[str, str] = {}
    secret_property: str | None = None
    # A SecretStore *key name*, not a credential — safe to keep in non-secret config
    # (same idiom as the `*_WEBHOOK_SECRET_NAME` settings).
    catalog_secret_name: str | None = None

    @model_validator(mode="after")
    def _uri_present(self) -> IcebergConfig:
        if self.catalog_type in _URI_REQUIRED and not self.catalog_uri:
            raise ValueError(f"catalog_uri is required for a {self.catalog_type!r} catalog")
        return self

    @model_validator(mode="after")
    def _uri_carries_no_password(self) -> IcebergConfig:
        """Reject a password smuggled into `catalog_uri` (#754 AC2).

        `config` is NOT a secret: it is persisted in plaintext JSONB, returned by the
        read API, and used to derive the asset's OpenLineage identity. A credential in
        here leaks by construction, so refuse it at the door rather than redacting it
        forever after — and point the author at the slot that does the right thing.
        """
        if self.catalog_uri and uri_password(self.catalog_uri):
            raise ValueError(
                "catalog_uri must not embed a password (config is stored and returned "
                "in plaintext, and becomes the asset's lineage identity). Put the "
                "catalog credential in the secret store and name it via "
                "'catalog_secret_name'; keep the username in the URI."
            )
        return self

    def catalog_properties(
        self, secret: str | None, catalog_secret: str | None = None
    ) -> dict[str, str]:
        """The keyword properties handed to ``pyiceberg.catalog.load_catalog``.

        The freeform ``properties`` go in **first** so the validated
        ``type``/``uri``/``warehouse`` overwrite (never get shadowed by) any
        collision — otherwise a stray ``properties={'type': …}`` would diverge from
        what the ``_uri_present`` validator reasoned about. The single secret under
        ``secret_property`` is applied last so it can't be shadowed either.
        """
        props: dict[str, str] = dict(self.properties)
        props["type"] = self.catalog_type
        if self.catalog_uri:
            # The catalog credential is re-attached HERE and nowhere else — the last
            # possible moment, in memory, for this one load. `catalog_uri` itself
            # stays credential-less at rest (#754/#826).
            props["uri"] = (
                inject_uri_password(self.catalog_uri, catalog_secret)
                if catalog_secret
                else self.catalog_uri
            )
        if self.warehouse:
            props["warehouse"] = self.warehouse
        if self.secret_property and secret is not None:
            props[self.secret_property] = secret
        return props


def load_iceberg_table(
    config: IcebergConfig,
    secret: str | None,
    identifier: str,
    catalog_secret: str | None = None,
) -> Any:
    """Load an Iceberg table by its ``namespace.table`` identifier (the live seam).

    The single catalog-load + ``load_table`` round-trip shared by the check/monitor
    runner and the column profiler (#721), so the two can't drift on how a
    connection's config + optional secret map to a ``pyiceberg`` catalog.
    ``pyiceberg`` is imported lazily (per this module's idiom) so the dependency
    only loads on a live Iceberg path.
    """
    from pyiceberg.catalog import load_catalog

    catalog: Any = load_catalog(
        config.catalog_name, **config.catalog_properties(secret, catalog_secret)
    )
    return catalog.load_table(identifier)


def _to_arrow_backed_pandas(arrow: Any) -> Any:
    """Materialise an Arrow table as Arrow-backed pandas (``pd.ArrowDtype``).

    The one conversion that keeps Iceberg's DataFrame dtypes consistent with the
    flat-file/UC paths (``dtype_backend="pyarrow"``) — reused by both the runner's
    whole-table read and the profiler's sampled read (#721)."""
    import pandas as pd

    return arrow.to_pandas(types_mapper=pd.ArrowDtype)


def read_iceberg_dataframe(
    config: IcebergConfig,
    secret: str | None,
    identifier: str,
    *,
    columns: list[str] | None = None,
    limit: int | None = None,
    table: Any = None,
    catalog_secret: str | None = None,
) -> Any:
    """Materialise an Iceberg table as an Arrow-backed pandas DataFrame (#721).

    The column profiler's read seam: like the runner it goes through
    ``scan().to_arrow()`` → Arrow-backed pandas, but adds the two "load less data"
    levers the flat-file profiler uses — column **projection** (``selected_fields``,
    restricted to columns that actually exist so a stray name doesn't fail the
    scan — the caller reports genuinely-missing columns as a clean 422) and a row
    **limit** (sampling). ``columns=None`` reads every column; ``limit=None`` reads
    every row (the runner's whole-table contract).

    Pass an already-loaded ``table`` to scan it directly instead of loading it
    again — the profiler's pre-scan column validation (`profile_service`) loads
    the table once (for `table.schema()`) and reuses it here, so a request is
    never charged a second catalog round-trip for the scan (#721 code review).
    ``table=None`` (every other caller) loads it here, unchanged."""
    if table is None:
        table = load_iceberg_table(config, secret, identifier, catalog_secret)
    if columns:
        available = {field.name for field in table.schema().fields}
        selected = tuple(c for c in columns if c in available) or ("*",)
    else:
        selected = ("*",)
    arrow = table.scan(selected_fields=selected, limit=limit).to_arrow()
    return _to_arrow_backed_pandas(arrow)


def list_iceberg_columns(
    config: IcebergConfig,
    secret: str | None,
    identifier: str,
    catalog_secret: str | None = None,
) -> list[str]:
    """Column (field) names of an Iceberg table from its schema — **no data scan**.

    Reads the table's ``schema()`` field names (a metadata-only lookup, like the
    flat-file lister's Parquet-footer read), so the check editor's column dropdown
    (#474) never scans table data to populate itself (#721)."""
    table = load_iceberg_table(config, secret, identifier, catalog_secret)
    return [field.name for field in table.schema().fields]


class IcebergConnectionAdapter:
    """`ConnectionAdapter` for Iceberg — config validation + a metadata probe."""

    def validate_config(self, raw: dict[str, Any]) -> IcebergConfig:
        return IcebergConfig.model_validate(raw)

    def test(
        self, raw: dict[str, Any], secret: str, *, catalog_secret: str | None = None, **_: Any
    ) -> None:
        """Load the catalog and list namespaces; raise on failure.

        A lightweight metadata round-trip — a green test means the catalog is
        reachable and the credential authenticates. Deliberately reads no table
        data (no scan), so it stays cheap.

        ``catalog_secret`` is the SQL-catalog DB password, already resolved by the
        caller (adapters never touch the SecretStore — `base.ConnectionAdapter`).
        """
        from pyiceberg.catalog import load_catalog

        config = self.validate_config(raw)
        catalog: Any = load_catalog(
            config.catalog_name, **config.catalog_properties(secret, catalog_secret)
        )
        catalog.list_namespaces()


class IcebergCheckRunner:
    """GX `CheckRunner` + `MonitorRunner` for a natively-read Iceberg table.

    Reads the target table into an Arrow-backed pandas DataFrame via ``pyiceberg``
    and validates that frame with GX — the "GX DataFrame datasource" shape
    (CLAUDE.md §5), like `UnityCatalogCheckRunner`, so the run path never sees
    Iceberg internals. ``table`` is the ``namespace.table`` identifier (``schema``
    is folded into it upstream — Iceberg namespaces aren't a separate SQL schema).

    Loading the table (`_load_table`) is the live seam, monkeypatched in tests; GX
    then runs in-process on the returned frame, so the validation path is fully
    covered without a live catalog.
    """

    # Runner-advertised monitor capability (#429) — the run path gates on this.
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset(MONITOR_KINDS)

    def __init__(
        self, *, config: IcebergConfig, secret: str | None, catalog_secret: str | None = None
    ) -> None:
        self._config = config
        self._secret = secret
        self._catalog_secret = catalog_secret

    def _load_table(self, identifier: str) -> Any:
        """Load the Iceberg table by ``namespace.table`` identifier (live seam)."""
        return load_iceberg_table(self._config, self._secret, identifier, self._catalog_secret)

    def _read_dataframe(self, identifier: str) -> Any:
        """Materialise the whole current snapshot as Arrow-backed pandas.

        ``.to_arrow()`` (not the bare ``.to_pandas()`` shortcut) + Arrow-backed
        pandas dtypes keep Iceberg's GX behaviour consistent with the flat-file/UC
        DataFrame paths (via the shared ``_to_arrow_backed_pandas``). GX
        expectations are exact and need the whole frame, so this materialises the
        full table (ADR 0030 — G-b scale ceiling). Goes through ``self._load_table``
        (not the module helper directly) so tests can patch the load seam.
        """
        table = self._load_table(identifier)
        return _to_arrow_backed_pandas(table.scan().to_arrow())

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        df = self._read_dataframe(table)
        context = gx.get_context(mode="ephemeral")
        asset = context.data_sources.add_pandas(name="iceberg").add_dataframe_asset(name="table")
        batch_definition = asset.add_batch_definition_whole_dataframe(name="whole_dataframe")
        return run_expectations(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name="suite-iceberg",
            batch_parameters={"dataframe": df},
            index_columns=index_columns,
        )

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors natively (no SQL engine).

        Reuses the shared `monitors.run_monitor_specs` banding loop — only the
        scalar source differs: volume is ``scan().count()`` (no materialisation),
        freshness scans just its timestamp column for its ``MAX``. The table is
        loaded **once, before the loop**, so a catalog/load failure propagates and
        fails the whole run (matching the SQL runners' open-connection-first
        contract); a bad *monitor* then errors only itself (#122)."""
        loaded = self._load_table(table)  # load failure propagates — before the loop
        return run_monitor_specs(
            lambda spec: self._monitor_scalar(loaded, spec),
            monitors=monitors,
            now=datetime.now(UTC),
        )

    def _monitor_scalar(self, table: Any, spec: MonitorSpec) -> Any:
        """The scalar a monitor bands: ``COUNT(*)`` (volume) or ``MAX(column)``
        (freshness), computed from the Iceberg table without materialising it."""
        validate_monitor_config(spec.kind, spec.config)  # structural gate (bad column/range)
        if spec.kind == VOLUME:
            return table.scan().count()
        if spec.kind == FRESHNESS:
            import pyarrow.compute as pc

            column = spec.config["column"]
            arrow = table.scan(selected_fields=(column,)).to_arrow()
            if arrow.num_rows == 0:
                return None  # empty table → monitor_outcome maps to an operational error
            return pc.max(arrow.column(column)).as_py()
        raise MonitorConfigError(f"unknown monitor kind: {spec.kind!r}")


def iceberg_credentials(
    config: IcebergConfig, secret_ref: str | None, secret_store: SecretStore
) -> tuple[str | None, str | None]:
    """``(storage_secret, catalog_secret)`` for an Iceberg connection.

    **The one place both credentials are resolved.** An Iceberg SQL catalog needs two
    (the storage key AND the catalog DB password, #754/#826), and `catalog_uri` no
    longer carries the second one — so a caller that resolves only the storage secret
    would connect to the catalog with *no password* and fail obscurely, or worse,
    succeed against an unauthenticated catalog. Every read path (runner, profiler,
    comparison reader) goes through here so none of them can forget.

    Both are optional: a local warehouse / vended-credentials REST catalog has neither.
    """
    secret = secret_store.get(secret_ref) if secret_ref else None
    catalog_secret = (
        secret_store.get(config.catalog_secret_name) if config.catalog_secret_name else None
    )
    return secret, catalog_secret


def build_iceberg_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, **_: Any
) -> IcebergCheckRunner:
    """Build a runner from an ``iceberg`` `Connection`'s primitives.

    Mirrors `build_unity_catalog_runner`: takes the raw config dict (not the ORM
    model) to stay decoupled from ``db/``. The storage/catalog credential is
    optional — a credential-less catalog (local warehouse, vended-credentials
    REST) has no ``secret_ref`` (like the ADLS/S3 adapters).
    """
    iceberg_config = IcebergConfig.model_validate(config)
    secret, catalog_secret = iceberg_credentials(iceberg_config, secret_ref, secret_store)
    return IcebergCheckRunner(config=iceberg_config, secret=secret, catalog_secret=catalog_secret)
