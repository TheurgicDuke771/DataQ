"""Apache Iceberg connection adapter + native read runner (ADR 0030, #716)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar, Literal

import great_expectations as gx
from pydantic import BaseModel, ConfigDict, model_validator

from backend.app.core.credential_expiry import azure_sas_expiry
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

# Catalog backends pyiceberg's ``load_catalog`` understands.
IcebergCatalogType = Literal["rest", "sql", "glue", "hive"]

# Catalog types whose connection needs a URI (REST endpoint, SQL/metastore URI).
_URI_REQUIRED: frozenset[str] = frozenset({"rest", "sql", "hive"})

# The pyiceberg ADLS FileIO property family whose value is a SAS — and therefore the only
# ``secret_property`` whose credential states its own expiry (#838).
_SAS_PROPERTY_PREFIX = "adls.sas-token"

# `properties` KEYS pyiceberg documents as holding a credential directly (not a reference to one) —
# a literal value here is exactly the #754/#826 leak.
_CREDENTIAL_PROPERTY_KEYS: frozenset[str] = frozenset({"s3.secret-access-key", "adls.account-key"})
# Generic name-based hints — anything that reads as a password/token by NAME, not just the two known
# keys above (a future catalog/storage backend's property family is unknowable in advance).
_CREDENTIAL_NAME_HINTS: tuple[str, ...] = ("password", "token")
_CREDENTIAL_NAME_EXEMPTIONS: frozenset[str] = frozenset({"s3.access-key-id"})


def _looks_like_a_credential_property(key: str) -> bool:
    """True when `key` names something pyiceberg treats as (or that reads like)
    a literal credential, rather than a non-secret identifier or option.
    """
    lowered = key.lower()
    if lowered in _CREDENTIAL_NAME_EXEMPTIONS:
        return False
    if lowered in _CREDENTIAL_PROPERTY_KEYS or lowered.startswith(_SAS_PROPERTY_PREFIX):
        return True
    return any(hint in lowered for hint in _CREDENTIAL_NAME_HINTS)


class IcebergConfig(BaseModel):
    """Non-secret Iceberg catalog + storage config (the credential is the secret)."""

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
        """Reject a password smuggled into `catalog_uri` (#754 AC2)."""
        if self.catalog_uri and uri_password(self.catalog_uri):
            raise ValueError(
                "catalog_uri must not embed a password (config is stored and returned "
                "in plaintext, and becomes the asset's lineage identity). Put the "
                "catalog credential in the secret store and name it via "
                "'catalog_secret_name'; keep the username in the URI."
            )
        return self

    @model_validator(mode="after")
    def _properties_carry_no_credential(self) -> IcebergConfig:
        """Reject a credential smuggled into `properties` (#754/#826, extended to
        the properties dict by #1181's editor exposure).
        """
        for key, value in self.properties.items():
            if _looks_like_a_credential_property(key):
                raise ValueError(
                    f"properties[{key!r}] looks like a credential-bearing property and "
                    "must not carry a literal value (config is stored and returned in "
                    "plaintext). Use 'secret_property' for the storage credential or "
                    "'catalog_secret_name' for the catalog password instead."
                )
            if uri_password(value):
                raise ValueError(
                    f"properties[{key!r}] must not embed a password in a URI-shaped "
                    "value (config is stored and returned in plaintext). Put the "
                    "credential in the secret store instead."
                )
        return self

    def catalog_properties(
        self, secret: str | None, catalog_secret: str | None = None
    ) -> dict[str, str]:
        """The keyword properties handed to ``pyiceberg.catalog.load_catalog``."""
        props: dict[str, str] = dict(self.properties)
        props["type"] = self.catalog_type
        if self.catalog_uri:
            # The catalog credential is re-attached HERE and nowhere else — the last possible
            # moment, in memory, for this one load.
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
    """Load an Iceberg table by its ``namespace.table`` identifier (the live seam)."""
    from pyiceberg.catalog import load_catalog

    catalog: Any = load_catalog(
        config.catalog_name, **config.catalog_properties(secret, catalog_secret)
    )
    return catalog.load_table(identifier)


def _to_arrow_backed_pandas(arrow: Any) -> Any:
    """Materialise an Arrow table as Arrow-backed pandas (``pd.ArrowDtype``)."""
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
    """Materialise an Iceberg table as an Arrow-backed pandas DataFrame (#721)."""
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
    """Column (field) names of an Iceberg table from its schema — **no data scan**."""
    table = load_iceberg_table(config, secret, identifier, catalog_secret)
    return [field.name for field in table.schema().fields]


class IcebergConnectionAdapter:
    """`ConnectionAdapter` for Iceberg — config validation + a metadata probe."""

    # #1401 — the only type with two credential slots, and they have genuinely different
    # destinations, which is why this attribute is per-slot rather than one flat set: catalog →
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {
        "catalog": ("catalog_uri",),
        "secret": ("catalog_uri", "warehouse", "properties", "secret_property"),
    }

    # A credential-less catalog (local warehouse, vended-credentials REST) is a legitimate config
    # (ADR 0030 §3, the class docstring above).
    secret_optional = True

    def validate_config(self, raw: dict[str, Any]) -> IcebergConfig:
        return IcebergConfig.model_validate(raw)

    def credential_expiry(self, raw: dict[str, Any], secret: str, **_: Any) -> datetime | None:
        """When the storage credential stops working (#838), or ``None``."""
        if not (self.validate_config(raw).secret_property or "").startswith(_SAS_PROPERTY_PREFIX):
            return None
        return azure_sas_expiry(secret)

    def test(
        self,
        raw: dict[str, Any],
        secret: str | None,
        *,
        catalog_secret: str | None = None,
        **_: Any,
    ) -> None:
        """Load the catalog and list namespaces; raise on failure."""
        from pyiceberg.catalog import load_catalog

        config = self.validate_config(raw)
        catalog: Any = load_catalog(
            config.catalog_name, **config.catalog_properties(secret, catalog_secret)
        )
        catalog.list_namespaces()


class IcebergCheckRunner:
    """GX `CheckRunner` + `MonitorRunner` for a natively-read Iceberg table."""

    # Runner-advertised monitor capability (#429): EXPLICITLY what this runner implements — never
    # frozenset(MONITOR_KINDS).
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME})

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
        """Materialise the whole current snapshot as Arrow-backed pandas."""
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
        """Evaluate freshness/volume monitors natively (no SQL engine)."""
        loaded = self._load_table(table)  # load failure propagates — before the loop
        sources: dict[int, dict[str, Any]] = {}
        index = iter(range(len(monitors)))

        def scalar_for(spec: MonitorSpec) -> Any:
            i = next(index)
            scalar, detail = self._monitor_scalar(loaded, spec)
            sources[i] = detail
            return scalar

        outcomes = run_monitor_specs(scalar_for, monitors=monitors, now=datetime.now(UTC))
        # Stamp WHICH path answered (#859 / the #828 lesson: a degraded answer must say so) —
        # metadata (`snapshot-summary` / `file-bounds`) or the scan fallback with its reason.
        return [
            (
                replace(oc, observed_value={**oc.observed_value, **detail})
                if not oc.errored and oc.observed_value is not None and (detail := sources.get(i))
                else oc
            )
            for i, oc in enumerate(outcomes)
        ]

    def _monitor_scalar(self, table: Any, spec: MonitorSpec) -> tuple[Any, dict[str, Any]]:
        """The scalar a monitor bands plus a source detail dict (#859)."""
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
    is an expected answer, never an exception.
    """
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
    """``None`` when the summary PROVES the snapshot has zero row-level deletes; otherwise the
    degrade reason.
    """
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
    """``(total_records, delta_detail)`` from the current snapshot's summary (#859)."""
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
        # A real schema that lacks the column is the CHECK's config error (#122), not a metadata
        # degrade.
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
    """``(storage_secret, catalog_secret)`` for an Iceberg connection."""
    secret = secret_store.get(secret_ref) if secret_ref else None
    catalog_secret = (
        secret_store.get(config.catalog_secret_name) if config.catalog_secret_name else None
    )
    return secret, catalog_secret


def build_iceberg_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, **_: Any
) -> IcebergCheckRunner:
    """Build a runner from an ``iceberg`` `Connection`'s primitives."""
    iceberg_config = IcebergConfig.model_validate(config)
    secret, catalog_secret = iceberg_credentials(iceberg_config, secret_ref, secret_store)
    return IcebergCheckRunner(config=iceberg_config, secret=secret, catalog_secret=catalog_secret)
