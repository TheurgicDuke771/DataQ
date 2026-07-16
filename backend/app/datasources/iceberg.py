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
(which drops to numpy dtypes). Monitors avoid touching data files entirely where
the metadata is trustworthy (#859): volume answers from the snapshot summary's
``total-records`` and freshness from per-file column upper bounds — degrading
honestly to ``scan().count()`` / a one-column ``MAX`` scan (with the reason
recorded on the result) when summary fields are absent, row-level deletes exist,
or a file lacks stats.

``pyiceberg`` is imported lazily (like the other adapters' clients) so importing
this module stays cheap and the dependency only loads on a live Iceberg path.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import great_expectations as gx
from pydantic import BaseModel, ConfigDict, model_validator

from backend.app.core.secrets import SecretStore
from backend.app.core.uri_credentials import inject_uri_password, uri_password
from backend.app.datasources.base import CheckOutcome, CheckSpec, MonitorSpec, SuiteOutcome
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import (
    FRESHNESS,
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
        sources: dict[int, dict[str, Any]] = {}
        index = iter(range(len(monitors)))

        def scalar_for(spec: MonitorSpec) -> Any:
            i = next(index)
            scalar, detail = self._monitor_scalar(loaded, spec)
            sources[i] = detail
            return scalar

        outcomes = run_monitor_specs(scalar_for, monitors=monitors, now=datetime.now(UTC))
        # Stamp WHICH path answered (#859 / the #828 lesson: a degraded answer must
        # say so) — metadata (`snapshot-summary` / `file-bounds`) or the scan
        # fallback with its reason — onto the successful outcomes' detail.
        return [
            (
                replace(oc, observed_value={**oc.observed_value, **detail})
                if not oc.errored and oc.observed_value is not None and (detail := sources.get(i))
                else oc
            )
            for i, oc in enumerate(outcomes)
        ]

    def _monitor_scalar(self, table: Any, spec: MonitorSpec) -> tuple[Any, dict[str, Any]]:
        """The scalar a monitor bands plus a source detail dict (#859).

        The answer is read from table METADATA when it is trustworthy — no data
        scan, no compute burned on the check:

        * ``volume`` — the current snapshot summary's ``total-records``;
        * ``freshness`` — the max per-file upper bound of the timestamp column
          across the current snapshot's data files (exact for timestamp/date
          types), UNLESS row-level deletes exist (a deleted row may hold the
          max, so the bound would over-report freshness).

        Summary fields are engine-written and OPTIONAL, so every metadata path
        degrades to the scan (``scan().count()`` / a one-column ``MAX``) with the
        reason recorded — never a confident answer from an untrustworthy source.
        """
        validate_monitor_config(spec.kind, spec.config)  # structural gate (bad column/range)
        if spec.kind == VOLUME:
            try:
                total, delta = _volume_from_snapshot_summary(table)
            except Exception as exc:  # the FAST path must never fail the check
                total, delta = None, {
                    "fallback_reason": f"metadata unavailable ({type(exc).__name__})"
                }
            if total is not None:
                return total, {"source": "snapshot-summary", **delta}
            return table.scan().count(), {"source": "scan-fallback", **delta}
        if spec.kind == FRESHNESS:
            column = spec.config["column"]
            try:
                bound, reason = _freshness_from_file_bounds(table, column)
            except MonitorConfigError:
                raise  # unknown column = the check's own config error, not a degrade
            except Exception as exc:
                bound, reason = None, f"metadata unavailable ({type(exc).__name__})"
            if reason is None:
                return bound, {"source": "file-bounds"}
            import pyarrow.compute as pc

            arrow = table.scan(selected_fields=(column,)).to_arrow()
            if arrow.num_rows == 0:
                # empty table → monitor_outcome maps to an operational error
                return None, {"source": "scan-fallback", "fallback_reason": reason}
            return pc.max(arrow.column(column)).as_py(), {
                "source": "scan-fallback",
                "fallback_reason": reason,
            }
        raise MonitorConfigError(f"unknown monitor kind: {spec.kind!r}")


def _summary_get(summary: Any, key: str) -> Any:
    """A snapshot summary field, tolerating pyiceberg's Summary object OR a plain
    mapping OR None — summary fields are engine-written and optional, so absence
    is an expected answer, never an exception."""
    if summary is None:
        return None
    get = getattr(summary, "get", None)
    if callable(get):
        return get(key)
    try:
        return summary[key]
    except (KeyError, TypeError, IndexError):
        return None


def _summary_int(summary: Any, key: str) -> int | None:
    raw = _summary_get(summary, key)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


_DELETE_TOTAL_KEYS = ("total-delete-files", "total-position-deletes", "total-equality-deletes")


def _row_delete_guard(summary: Any) -> str | None:
    """``None`` when the summary PROVES the snapshot has zero row-level deletes;
    otherwise the degrade reason. All three ``total-*`` fields are optional and
    engine-written, so an ABSENT field is "cannot prove", never "no deletes" —
    with live delete files, summary ``total-records`` over-counts (it nets only
    data-file records; verified against pyiceberg's ``_update_totals``) and a
    column upper bound may belong to a deleted row. Both metadata paths refuse
    to answer unless proven clean."""
    for key in _DELETE_TOTAL_KEYS:
        raw = _summary_get(summary, key)
        if raw is None:
            return f"snapshot summary omits {key} — cannot prove no row-level deletes"
        count = _summary_int(summary, key)
        if count is None:
            return f"unparseable snapshot summary field {key}"
        if count > 0:
            return f"row-level deletes present ({key}={count})"
    return None


def _volume_from_snapshot_summary(table: Any) -> tuple[int | None, dict[str, Any]]:
    """``(total_records, delta_detail)`` from the current snapshot's summary (#859).

    ``total-records`` is the row count the engine recorded at commit time — the
    volume answer with zero data-file scanning — but it nets only DATA-file
    records: on a merge-on-read table with live position/equality deletes it
    over-counts (a false-green vs the delete-aware ``scan().count()``), so the
    delete guard applies here exactly as it does to freshness. ``None`` → the
    caller falls back to the scan. The per-commit ``added-records``/
    ``deleted-records`` delta rides along as observed detail when present — a
    row-count scan can never provide it.
    """
    snapshot = table.current_snapshot()
    if snapshot is None:
        return None, {"fallback_reason": "table has no current snapshot"}
    summary = getattr(snapshot, "summary", None)
    total = _summary_int(summary, "total-records")
    if total is None:
        return None, {"fallback_reason": "snapshot summary lacks total-records"}
    guard = _row_delete_guard(summary)
    if guard is not None:
        return None, {"fallback_reason": guard}
    delta: dict[str, Any] = {}
    added = _summary_int(summary, "added-records")
    deleted = _summary_int(summary, "deleted-records")
    if added is not None:
        delta["added_records"] = added
    if deleted is not None:
        delta["deleted_records"] = deleted
    return total, delta


# Iceberg physically stores timestamp bounds as epoch micros and dates as
# epoch days — decode to the datetime/date shapes `monitor_outcome` bands.
_EPOCH_DT = datetime(1970, 1, 1, tzinfo=UTC)
_EPOCH_DATE = date(1970, 1, 1)


def _decode_bound(field_type: Any, raw: bytes) -> Any:
    from pyiceberg.conversions import from_bytes
    from pyiceberg.types import DateType, TimestampType, TimestamptzType

    value = from_bytes(field_type, raw)
    if isinstance(field_type, (TimestampType, TimestamptzType)):
        return _EPOCH_DT + timedelta(microseconds=int(value))
    if isinstance(field_type, DateType):
        return _EPOCH_DATE + timedelta(days=int(value))
    return value  # non-temporal → monitor_outcome raises its established type error


def _freshness_from_file_bounds(table: Any, column: str) -> tuple[Any, str | None]:
    """``(max_bound, None)`` from per-file column upper bounds — or ``(None,
    reason)`` when the metadata can't be trusted and the caller must scan (#859).

    The max upper bound over the current snapshot's data files IS ``MAX(column)``
    for timestamp/date columns (bounds are exact for temporal types) — unless
    row-level deletes exist: a deleted row may hold the max, so the bound would
    over-report freshness (a false-green), and we degrade to the scan instead.
    A file missing the bound (stats disabled) likewise degrades, as does a table
    with no current snapshot (conservative — the scan of a truly empty table is
    free, and a non-standard table object without snapshot metadata keeps
    working). A snapshot with zero live data files returns ``(None, None)`` —
    authoritatively no rows, the same operational error the scan path produces.
    """
    snapshot = table.current_snapshot()
    if snapshot is None:
        return None, "table has no current snapshot"
    guard = _row_delete_guard(getattr(snapshot, "summary", None))
    if guard is not None:
        return None, guard
    schema = table.schema()  # metadata unavailability (non-standard table) → caller degrades
    try:
        field = schema.find_field(column)
    except Exception as exc:
        # A real schema that lacks the column is the CHECK's config error (#122),
        # not a metadata degrade — relabeling it "metadata unavailable" and letting
        # the fallback scan re-raise would make the outcome right by coincidence.
        raise MonitorConfigError(f"unknown freshness column {column!r}") from exc
    max_bound: Any = None
    for task in table.scan(selected_fields=(column,)).plan_files():
        # The authoritative per-task delete signal — belt to the summary guard's
        # braces (a writer could omit the summary fields yet attach delete files).
        if getattr(task, "delete_files", None):
            return None, "scan tasks carry row-level delete files"
        bounds = getattr(task.file, "upper_bounds", None) or {}
        raw_bound = bounds.get(field.field_id)
        if raw_bound is None:
            return None, "a data file lacks an upper bound for the column"
        value = _decode_bound(field.field_type, raw_bound)
        if max_bound is None or value > max_bound:
            max_bound = value
    return max_bound, None  # None with no files = empty table, same as no snapshot


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
