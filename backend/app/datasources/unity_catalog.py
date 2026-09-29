"""Unity Catalog (Databricks) connection adapter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar
from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, ConfigDict, field_validator

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import (
    SAMPLE_HEAD,
    CheckOutcome,
    CheckSpec,
    MonitorSpec,
    SampleSpec,
    SuiteOutcome,
    ValueSignalGate,
)
from backend.app.datasources.databricks_dqx import (
    DQX_ENGINE,
    DqxJobs,
    checkpoint_root,
    run_dqx_batch,
)
from backend.app.datasources.gx_runner import ephemeral_gx_context, run_expectations
from backend.app.datasources.monitors import (
    AGGREGATE,
    FRESHNESS,
    VOLUME,
    row_count_from_scalar,
    run_monitors_over_engine,
)
from backend.app.datasources.sampling import (
    SamplingDrawError,
    enforce_frame_cap,
    enforce_row_cap,
    enforce_sample_cap,
    sampling_record,
    split_row_count_checks,
    stamp_sampling,
)
from backend.app.datasources.sql import (
    LazyEngine,
    core_table,
    fold_reflection_keyed_columns,
    is_sql_identifier,
    qualified_sql_name,
)
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE, is_custom_sql
from backend.app.services.failure_classifier import classify_failure_reason

log = get_logger(__name__)


class UnityCatalogConfig(BaseModel):
    """Non-secret Databricks/UC connection config (the PAT comes from secrets)."""

    model_config = ConfigDict(extra="forbid")

    workspace_url: str
    warehouse_id: str
    # Warehouse inventory sync (#919, ADR 0040) — on by default; see SnowflakeConfig.
    inventory_sync: bool = True
    # Automatic coverage (ADR 0047) — off unless switched on; read by coverage_service.
    auto_coverage: bool = False
    # `catalog.schema.volume` for the throwaway checkpoints of stream-mode DQX checks.
    dqx_checkpoint_volume: str | None = None

    @field_validator("dqx_checkpoint_volume")
    @classmethod
    def _volume(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        checkpoint_root(value.strip())
        return value.strip()

    @field_validator("workspace_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("workspace_url must start with http:// or https://")
        return value.rstrip("/")

    @property
    def server_hostname(self) -> str:
        return urlparse(self.workspace_url).netloc

    @property
    def http_path(self) -> str:
        return f"/sql/1.0/warehouses/{self.warehouse_id}"


class UnityCatalogConnectionAdapter:
    """`ConnectionAdapter` for Unity Catalog — config validation + a SELECT 1 probe."""

    # #1401: `workspace_url` is the host the Databricks PAT is sent to.
    destination_fields: ClassVar[dict[str, tuple[str, ...]]] = {"secret": ("workspace_url",)}

    def validate_config(self, raw: dict[str, Any]) -> UnityCatalogConfig:
        return UnityCatalogConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Open a SQL-Warehouse connection and run ``SELECT 1``; raise on failure."""
        if secret is None:
            raise ValueError("a credential is required to test a Unity Catalog connection")
        from databricks import sql

        config = self.validate_config(raw)
        # databricks-sql-connector is only partially typed; treat the connection
        # as dynamic so strict mypy doesn't flag no-untyped-call on its methods.
        connection: Any = sql.connect(
            server_hostname=config.server_hostname,
            http_path=config.http_path,
            access_token=secret,
        )
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()


def build_databricks_url(
    config: UnityCatalogConfig,
    token: str,
    *,
    catalog: str | None = None,
    schema: str | None = None,
) -> str:
    """SQLAlchemy URL for the Databricks SQL Warehouse (databricks dialect)."""
    url = (
        f"databricks://token:{quote_plus(token)}@{config.server_hostname}"
        f"?http_path={quote_plus(config.http_path)}"
    )
    if catalog:
        url += f"&catalog={quote_plus(catalog)}"
    if schema:
        url += f"&schema={quote_plus(schema)}"
    return url


# The expectation types that need a SQL execution engine and therefore a SQL batch, not this
# runner's pandas one (#1179).
SQL_BATCH_EXPECTATION_TYPES: frozenset[str] = frozenset({CUSTOM_SQL_EXPECTATION_TYPE})

# Types that push down to the Databricks-SQL batch under `uc_sql_pushdown` (#1532).
# Pushdown is the DEFAULT for a new type (live-Databricks vetting first); staying on the
# frame batch needs a recorded reason (#1624 — no SQL provider, dtype semantics, sampling).
SQL_PUSHDOWN_EXPECTATION_TYPES: frozenset[str] = frozenset(
    {
        "expect_column_values_to_not_be_null",
        "expect_column_values_to_be_unique",
        "expect_column_values_to_be_between",
        "expect_column_values_to_be_in_set",
        "expect_column_value_lengths_to_be_between",
        "expect_column_values_to_match_regex",
        "expect_table_row_count_to_be_between",
        "expect_column_values_to_be_null",
        "expect_column_values_to_not_be_in_set",
        "expect_column_value_lengths_to_equal",
        "expect_column_values_to_not_match_regex",
        "expect_column_values_to_match_regex_list",
        "expect_column_values_to_not_match_regex_list",
        "expect_column_pair_values_to_be_equal",
        "expect_column_pair_values_to_be_in_set",
        "expect_column_pair_values_a_to_be_greater_than_b",
        "expect_multicolumn_sum_to_equal",
        "expect_select_column_values_to_be_unique_within_record",
        "expect_column_distinct_values_to_be_in_set",
        "expect_column_distinct_values_to_contain_set",
        "expect_compound_columns_to_be_unique",
    }
)


def frame_lane_required(expectation_types: Iterable[str]) -> bool:
    """Whether a suite of these expectation types will materialise a pandas frame (#1998).

    Routing-only, from the same two inputs `_routes_to_sql` uses. It deliberately does NOT
    see the runner's live target check (`_sql_target_problem`), so an unresolvable SQL target
    reads here as pushdown and the frame it actually falls back to goes unmetered; the scan
    caps still bound that read.
    """
    pushdown_on = get_settings().uc_sql_pushdown
    for expectation_type in expectation_types:
        if is_custom_sql(expectation_type):
            continue
        if pushdown_on and expectation_type in SQL_PUSHDOWN_EXPECTATION_TYPES:
            continue
        return True
    return False


# This metric indexes REFLECTED columns, whose keys the Databricks dialect rewrites via its
# own `normalize_name` — fold authored names to match, or an all-caps spelling KeyErrors.
# Other column_list-keyed types (multicolumn_sum_to_equal, select_column_values_to_be_
# unique_within_record) build plain column references instead and don't need this — live-
# verified with an all-caps column_list on both.
_REFLECTION_KEYED_TYPES = frozenset({"expect_compound_columns_to_be_unique"})


def _reflection_key(name: str) -> str:
    from databricks.sqlalchemy.base import DatabricksDialect

    dialect: Any = DatabricksDialect()
    return dialect.normalize_name(name) or name


def _fold_reflection_keyed_columns(checks: list[CheckSpec]) -> list[CheckSpec]:
    return fold_reflection_keyed_columns(
        checks, reflection_keyed_types=_REFLECTION_KEYED_TYPES, normalize_name=_reflection_key
    )


def _spec_columns(spec: CheckSpec) -> set[str]:
    """The lowercased column name(s) a check's row locator would select — covers every
    kwarg shape in the catalog (`column`, `column_A`/`column_B`, `column_list`)."""
    names: set[str] = set()
    single = spec.kwargs.get("column")
    if isinstance(single, str):
        names.add(single.lower())
    for key in ("column_A", "column_B"):
        value = spec.kwargs.get(key)
        if isinstance(value, str):
            names.add(value.lower())
    column_list = spec.kwargs.get("column_list")
    if isinstance(column_list, list):
        names.update(c.lower() for c in column_list if isinstance(c, str))
    return names


#: How far a Bernoulli ``TABLESAMPLE`` is over-drawn before the ``LIMIT`` trims it (#595).
_SAMPLE_OVERSHOOT = 1.2

#: Decimal places the percentage is rendered with.
_SAMPLE_PERCENT_DECIMALS = 6

#: Floor for the computed percentage, and it MUST survive the formatting above — ``1e-06`` renders
#: as ``0.000001`` at six places, and anything smaller would round to ``0.000000``.
_MIN_SAMPLE_PERCENT = 0.000001

#: Smallest expected draw the percentage is sized for, independent of how few rows were asked for.
_MIN_EXPECTED_DRAW_ROWS = 100


def _sample_percent(rows: int, total: int) -> float:
    """The ``TABLESAMPLE`` percentage that reliably draws at least ``rows`` of ``total``."""
    if total <= 0 or rows >= total:
        return 100.0
    wanted = max(rows * _SAMPLE_OVERSHOOT, float(_MIN_EXPECTED_DRAW_ROWS))
    percent = round(wanted / total * 100.0, _SAMPLE_PERCENT_DECIMALS)
    return max(min(100.0, percent), _MIN_SAMPLE_PERCENT)


#: Peak worker bytes per cell on the frame lane's Arrow read (#2144, recalibrated in #2149 once GX
#: stopped hashing the whole frame): 2 GiB rig, 400k-row views of samples.tpch, the whole GX run
#: included. Fixed-width types measured 29-37 B/cell, DECIMAL 55 (its float cast), booleans 11.
#: Text is an envelope over every measured shape, 1.1-1.6x the measurement and never under.
_BOOL_CELL_BYTES = 14
_FIXED_CELL_BYTES = 44
_DECIMAL_CELL_BYTES = 62
#: Strings (and any type not listed above) scale with their length.
_TEXT_CELL_BYTES = 42
_TEXT_BYTES_PER_CHAR = 3
#: LIST/MAP/STRUCT cells stay native Python objects in the frame, so they cost far more than their
#: printed length: two nested views measured 142 + 5.3 x that length; this adds headroom (1.2x).
_NESTED_CELL_BYTES = 170
_NESTED_BYTES_PER_CHAR = 6.5
_LENGTH_SAMPLE_ROWS = 1000
#: Rows of the ``TABLESAMPLE`` draw the width probe averages lengths over beyond the head (#2221).
_WIDTH_SAMPLE_ROWS = 10_000


def _fixed_cell_bytes(arrow_type: Any) -> int | None:
    """The per-cell cost of a fixed-width Arrow type, or ``None`` for a length-dependent one.

    Keyed on the Arrow schema, not SQLAlchemy reflection: the Databricks dialect cannot
    reflect an INTERVAL column at all (#2152).
    """
    import pyarrow as pa

    if pa.types.is_boolean(arrow_type):
        return _BOOL_CELL_BYTES
    if pa.types.is_decimal(arrow_type):
        return _DECIMAL_CELL_BYTES
    if (
        pa.types.is_integer(arrow_type)
        or pa.types.is_floating(arrow_type)
        or pa.types.is_temporal(arrow_type)
    ):
        return _FIXED_CELL_BYTES
    return None


def frame_row_bytes(column_types: dict[str, Any], mean_lengths: dict[str, float]) -> int:
    """Estimated peak worker bytes per row of a frame-lane read (#2087)."""
    import pyarrow as pa

    total = 0
    for name, column_type in column_types.items():
        fixed = _fixed_cell_bytes(column_type)
        if fixed is None:
            base, per_char = (
                (_NESTED_CELL_BYTES, _NESTED_BYTES_PER_CHAR)
                if pa.types.is_nested(column_type)
                else (_TEXT_CELL_BYTES, _TEXT_BYTES_PER_CHAR)
            )
            fixed = base + int(per_char * mean_lengths.get(name, 0.0))
        total += fixed
    return total


def _mean_text_length(column: Any) -> float:
    """Characters for text, bytes for BINARY (not its escaped repr), printed length otherwise."""
    import pyarrow as pa
    import pyarrow.compute as pc

    kind = column.type
    if pa.types.is_string(kind) or pa.types.is_large_string(kind):
        lengths = pc.utf8_length(column)
    elif pa.types.is_binary(kind) or pa.types.is_large_binary(kind):
        lengths = pc.binary_length(column)
    else:
        values = [v for v in column.to_pylist() if v is not None]
        return sum(len(str(v)) for v in values) / len(values) if values else 0.0
    mean = pc.mean(lengths).as_py()
    return float(mean) if mean is not None else 0.0


def _length_expression(column: str, arrow_type: Any) -> str:
    """Databricks SQL for the length `_mean_text_length` measures on ``column`` (quoted)."""
    import pyarrow as pa

    if pa.types.is_nested(arrow_type):
        return f"length(to_json({column}))"
    if any(
        check(arrow_type)
        for check in (
            pa.types.is_string,
            pa.types.is_large_string,
            pa.types.is_binary,
            pa.types.is_large_binary,
        )
    ):
        return f"length({column})"
    return f"length(CAST({column} AS STRING))"


def _fetchall_arrow(cursor: Any) -> Any:
    """The Databricks cursor's whole result as one Arrow table (live seam)."""
    return cursor.fetchall_arrow()


def arrow_to_frame(table: Any) -> Any:
    """The frame `pd.read_sql_table` builds from the same rows, without its per-cell objects.

    Integers widen to int64 and floats/decimals to float64, as the DBAPI path's coercion does;
    timestamps go through the same `pd.to_datetime` its harmonisation calls, so the unit and
    timezone match whichever pandas is installed. DATE stays an Arrow date, as on the Parquet
    and Iceberg frame lanes (#2151): as datetime64, a date bound errored and a date value set
    failed every row. BINARY and nested types stay native.
    """
    import pandas as pd
    import pyarrow as pa
    import pyarrow.compute as pc

    columns: dict[str, Any] = {}
    for field, column in zip(table.schema, table.columns, strict=True):
        kind = field.type
        if pa.types.is_date(kind):
            columns[field.name] = column.to_pandas(types_mapper=pd.ArrowDtype)
            continue
        if pa.types.is_timestamp(kind):
            objects = column.to_pandas(timestamp_as_object=True)
            columns[field.name] = pd.to_datetime(objects, errors="coerce", utc=kind.tz is not None)
            continue
        if pa.types.is_integer(kind) and kind != pa.int64():
            column = pc.cast(column, pa.int64())
        elif pa.types.is_decimal(kind) or (pa.types.is_floating(kind) and kind != pa.float64()):
            column = pc.cast(column, pa.float64(), safe=False)
        columns[field.name] = column.to_pandas()
    return pd.DataFrame(columns, index=pd.RangeIndex(table.num_rows))


def format_sample_percent(percent: float) -> str:
    """Render ``percent`` as a fixed-point DECIMAL literal Databricks will parse."""
    return f"{percent:.{_SAMPLE_PERCENT_DECIMALS}f}"


class UnityCatalogCheckRunner:
    """GX `CheckRunner` for Unity Catalog via the Databricks SQL Warehouse."""

    # Runner-advertised monitor capability (#429): EXPLICITLY what this runner implements — never
    # frozenset(MONITOR_KINDS).
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME, AGGREGATE})
    # The run path hands a `value_signal_gate` only to runners advertising it (#2014).
    accepts_value_signal_gate: ClassVar[bool] = True
    # DQX (ADR 0036 §6) runs as a serverless job in the connection's own workspace.
    supported_native_engines: ClassVar[frozenset[str]] = frozenset({DQX_ENGINE})

    def __init__(
        self,
        *,
        config: UnityCatalogConfig,
        token: str,
        catalog: str,
        sampling: SampleSpec | None = None,
    ) -> None:
        self._config = config
        self._token = token
        self._catalog = catalog
        self._sampling = sampling
        # The runner's ONE **runner-owned** lazily-built engine (#427), shared by the GX read
        # (`_read_table`) AND `run_monitors`.
        self._engine = LazyEngine(self._build_engine)

    def _build_engine(self) -> Any:
        from sqlalchemy import create_engine

        # pool_pre_ping: run_monitors may draw the connection _read_table checked in before a long
        # GX validation.
        return create_engine(
            build_databricks_url(self._config, self._token, catalog=self._catalog),
            pool_pre_ping=True,
        )

    def close(self) -> None:
        """Dispose the shared engine's pool. Idempotent; a no-op if never used."""
        self._engine.close()

    def _read_table(self, *, table: str, schema: str | None) -> Any:
        """Read the whole table into a DataFrame via the connector's Arrow fetch (live seam).

        Not `pd.read_sql_table` (#2144): its per-cell Python objects peaked at ~17x the finished
        frame, and on pandas 3 it casts every column the dialect reflects as String — BINARY,
        ARRAY, MAP, STRUCT included — to `str`.
        """
        return self._fetch_frame(
            f"SELECT * FROM {self._qualified(table, schema)}"  # noqa: S608  # nosec B608
        )

    def _qualified(self, table: str, schema: str | None) -> str:
        """The allowlist-checked, dialect-quoted target name for a `SELECT` statement."""
        return qualified_sql_name(
            table=table,
            schema=schema,
            catalog=self._catalog if schema else None,
            dialect=self._engine.get().dialect,
        )

    def _fetch_frame(self, statement: str) -> Any:
        """Run ``statement`` and build the frame from its Arrow result.

        Both frame-lane reads — whole table and sample — go through here, so the width
        estimate's per-cell costs (measured on this path) price every read the cap admits.
        """
        return arrow_to_frame(self._fetch_arrow(statement))

    def _fetch_arrow(self, statement: str) -> Any:
        """Run ``statement`` on a raw connection and return its whole result as Arrow."""
        raw = self._engine.get().raw_connection()
        try:
            cursor = raw.cursor()
            try:
                cursor.execute(statement)
                return _fetchall_arrow(cursor)
            finally:
                cursor.close()
        finally:
            raw.close()

    def _count_rows(self, *, table: str, schema: str | None) -> int:
        """``COUNT(*)`` over the target — the size probe (live seam, #595).

        The scalar is normalised by `monitors.row_count_from_scalar`, the one place
        that knows a COUNT crosses a driver boundary (#1330) — the volume monitor
        reads the same value off the same warehouse.
        """
        from sqlalchemy import func, select

        engine = self._engine.get()
        target = core_table(
            table=table,
            schema=schema,
            catalog=self._catalog if schema else None,
            dialect=engine.dialect,
        )
        with engine.connect() as conn:
            return row_count_from_scalar(
                conn.execute(select(func.count()).select_from(target)).scalar_one()
            )

    def _probe_row_bytes(
        self, *, table: str, schema: str | None, total_rows: int | None = None
    ) -> int:
        """Column types from a head read's Arrow schema; text lengths from the head AND a random
        draw across the table, the longer mean winning (#2221).

        A head alone sees only the first files, so a table whose later loads hold longer text
        is priced from the short ones. A head shorter than its limit is the whole table, so it
        needs no draw. ``total_rows`` sizes the draw; it is counted when not passed.
        """
        qualified = self._qualified(table, schema)
        head = self._fetch_arrow(
            f"SELECT * FROM {qualified} LIMIT {_LENGTH_SAMPLE_ROWS}"  # noqa: S608  # nosec B608
        )
        columns = {field.name: field.type for field in head.schema}
        lengths = {
            name: _mean_text_length(head.column(name))
            for name, kind in columns.items()
            if _fixed_cell_bytes(kind) is None
        }
        if lengths and head.num_rows >= _LENGTH_SAMPLE_ROWS:
            if total_rows is None:
                total_rows = self._count_rows(table=table, schema=schema)
            drawn = self._sampled_lengths(qualified, {n: columns[n] for n in lengths}, total_rows)
            lengths = {name: max(mean, drawn.get(name, 0.0)) for name, mean in lengths.items()}
        return frame_row_bytes(columns, lengths)

    def _sampled_lengths(
        self, qualified: str, columns: dict[str, Any], total_rows: int
    ) -> dict[str, float]:
        """Mean length of each column over a Bernoulli draw of ~`_WIDTH_SAMPLE_ROWS` rows,
        aggregated in the warehouse so one row comes back, whatever the widths.

        No ``LIMIT`` on the draw: it would keep whichever files the warehouse read first,
        the head bias again (it priced a skewed table 1.2x its exact mean, live).
        """
        quote = self._engine.get().dialect.identifier_preparer.quote_identifier
        names = list(columns)
        averages = ", ".join(
            f"avg({_length_expression(quote(name), columns[name])}) AS w{i}"
            for i, name in enumerate(names)
        )
        projection = ", ".join(quote(name) for name in names)
        percent = format_sample_percent(_sample_percent(_WIDTH_SAMPLE_ROWS, total_rows))
        drawn = self._fetch_arrow(
            f"SELECT {averages} FROM (SELECT {projection} FROM {qualified} "  # noqa: S608  # nosec B608
            f"TABLESAMPLE ({percent} PERCENT))"
        )
        values = drawn.to_pylist()[0] if drawn.num_rows else {}
        return {
            name: float(values[f"w{i}"])
            for i, name in enumerate(names)
            if values.get(f"w{i}") is not None
        }

    def probe_frame(self, *, table: str, schema: str | None) -> tuple[int, int]:
        """``(rows, bytes per row)`` of the frame this runner's read would materialise.

        The same two probes `_load_frame` gates the read on, so admission (#2145) reserves
        what the frame cap is checked against. A sample's rows are its size, not a count.
        """
        if self._sampling is not None:
            rows = self._sampling.rows
        else:
            rows = self._count_rows(table=table, schema=schema)
        total = None if self._sampling is not None else rows
        return rows, self._probe_row_bytes(table=table, schema=schema, total_rows=total)

    def _enforce_frame_cap(
        self, rows: int, *, table: str, schema: str | None, total_rows: int | None = None
    ) -> None:
        cap = get_settings().run_max_frame_bytes
        if cap <= 0:
            return
        row_bytes = self._probe_row_bytes(table=table, schema=schema, total_rows=total_rows)
        enforce_frame_cap(rows, row_bytes=row_bytes, cap=cap, target=f"table {table!r}")

    def _read_sampled_table(
        self, *, table: str, schema: str | None, sample: SampleSpec
    ) -> tuple[Any, dict[str, Any]]:
        """A bounded sample of the target, pushed down to the warehouse (#595)."""
        engine = self._engine.get()
        qualified = qualified_sql_name(
            table=table,
            schema=schema,
            catalog=self._catalog if schema else None,
            dialect=engine.dialect,
        )
        total: int | None = None
        if sample.strategy == SAMPLE_HEAD:
            # Interpolation is injection-safe: `qualified` is built from allowlist-checked
            # identifiers and dialect-quoted by `qualified_sql_name`, and the limit is an `int`.
            statement = (
                f"SELECT * FROM {qualified} LIMIT {sample.rows + 1}"  # noqa: S608  # nosec B608
            )
        else:
            total = self._count_rows(table=table, schema=schema)
            percent = format_sample_percent(_sample_percent(sample.rows, total))
            # `REPEATABLE (seed)` is what makes a seeded run actually reproducible.
            repeatable = f" REPEATABLE ({sample.seed})" if sample.seed is not None else ""
            statement = (
                f"SELECT * FROM {qualified} "  # noqa: S608  # nosec B608
                f"TABLESAMPLE ({percent} PERCENT){repeatable} LIMIT {sample.rows}"
            )
        frame = self._fetch_frame(statement)

        if sample.strategy == SAMPLE_HEAD:
            truncated = len(frame) > sample.rows
            if truncated:
                frame = frame.head(sample.rows)
            else:
                # The table ended inside the probe row, so its exact size is now
                # known for free and the read was complete.
                total = len(frame)
        else:
            self._require_non_empty_draw(frame, table=table, total=total)
            truncated = sample.rows < (total or 0)
        return frame, sampling_record(sample, rows=len(frame), total_rows=total, sampled=truncated)

    @staticmethod
    def _require_non_empty_draw(frame: Any, *, table: str, total: int | None) -> None:
        """Refuse a Bernoulli draw that came back EMPTY from a non-empty table (#595)."""
        if total and len(frame) == 0:
            raise SamplingDrawError(
                f"the random sample of {table!r} returned no rows from a table of "
                f"{total:,} — every check would pass on an empty frame without "
                "asserting anything, so DataQ refuses the run. Re-run, raise the "
                "sample size, or use the 'head' strategy."
            )

    def _load_frame(self, *, table: str, schema: str | None) -> tuple[Any, dict[str, Any] | None]:
        """The DataFrame the expectations run against, plus its sampling record."""
        settings = get_settings()
        if self._sampling is not None:
            enforce_sample_cap(self._sampling, cap=settings.run_max_scan_rows)
            self._enforce_frame_cap(self._sampling.rows, table=table, schema=schema)
            return self._read_sampled_table(table=table, schema=schema, sample=self._sampling)
        cap = settings.run_max_scan_rows
        if cap > 0 or settings.run_max_frame_bytes > 0:
            rows = self._count_rows(table=table, schema=schema)
            enforce_row_cap(rows, cap=cap, target=f"table {table!r}")
            self._enforce_frame_cap(rows, table=table, schema=schema, total_rows=rows)
        return self._read_table(table=table, schema=schema), None

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
        value_signal_gate: ValueSignalGate | None = None,
    ) -> SuiteOutcome:
        """Evaluate `checks`, routing each to the batch its expectation can run on."""
        pushdown_on = (
            get_settings().uc_sql_pushdown
            and self._sampling is None
            and self._sql_target_problem(table=table, schema=schema) is None
        )

        def _routes_to_sql(spec: CheckSpec) -> bool:
            if is_custom_sql(spec.expectation_type):
                return True
            return pushdown_on and spec.expectation_type in SQL_PUSHDOWN_EXPECTATION_TYPES

        sql_positions = [i for i, spec in enumerate(checks) if _routes_to_sql(spec)]
        frame_positions = [i for i, spec in enumerate(checks) if not _routes_to_sql(spec)]
        # Keyed by submission position, never appended to: a missing key is a loud KeyError below
        # rather than a silently short/misaligned outcome list.
        by_position: dict[int, CheckOutcome] = {}
        success = True
        # A THIRD group when sampling is on: a table row-count expectation against a sampled frame
        # measures the sample and reports it as the dataset's size (#595 C6).
        if self._sampling is not None and frame_positions:
            keep, refused = split_row_count_checks([checks[i] for i in frame_positions])
            if refused:
                by_position.update({frame_positions[i]: outcome for i, outcome in refused.items()})
                frame_positions = [frame_positions[i] for i in keep]
                success = False
        if frame_positions:
            frame_outcome = self._run_dataframe_checks(
                table=table,
                schema=schema,
                checks=[checks[i] for i in frame_positions],
                index_columns=index_columns,
            )
            success = success and frame_outcome.success
            by_position.update(zip(frame_positions, frame_outcome.checks, strict=True))
        if sql_positions:
            sql_outcome = self._run_sql_checks(
                table=table,
                schema=schema,
                checks=[checks[i] for i in sql_positions],
                index_columns=index_columns,
                value_signal_gate=value_signal_gate,
            )
            success = success and sql_outcome.success
            by_position.update(zip(sql_positions, sql_outcome.checks, strict=True))
        return SuiteOutcome(success=success, checks=[by_position[i] for i in range(len(checks))])

    def _run_dataframe_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None,
    ) -> SuiteOutcome:
        """The historical UC path: read the table into pandas, validate that frame."""
        df, sampling = self._load_frame(table=table, schema=schema)
        with ephemeral_gx_context() as context:
            asset = context.data_sources.add_pandas(name="uc").add_dataframe_asset(name="table")
            batch_definition = asset.add_batch_definition_whole_dataframe(name="whole_dataframe")
            outcome = run_expectations(
                context,
                batch_definition=batch_definition,
                checks=checks,
                name="suite-uc",
                batch_parameters={"dataframe": df},
                index_columns=index_columns,
            )
        return stamp_sampling(outcome, sampling)

    def _sql_target_problem(self, *, table: str, schema: str | None) -> str | None:
        """Why this target can't back a SQL batch, or ``None`` when it can."""
        if schema is None:
            return (
                "a Unity Catalog custom-SQL check needs the suite target's schema "
                "(GX addresses the batch as catalog.schema.table)"
            )
        for part, label in ((table, "table"), (schema, "schema"), (self._catalog, "catalog")):
            if not is_sql_identifier(part):
                return f"invalid {label} identifier for a Unity Catalog custom-SQL check: {part!r}"
        return None

    def _sql_batch_definition(self, context: Any, *, table: str, schema: str) -> tuple[Any, Any]:
        """A GX Databricks-SQL whole-table batch over the target (live seam)."""
        datasource = context.data_sources.add_databricks_sql(
            name=f"uc-sql-{table}",
            connection_string=build_databricks_url(
                self._config, self._token, catalog=self._catalog, schema=schema
            ),
            create_temp_table=False,
        )
        # `add_databricks_sql` has ALREADY built and tested the engine by the time it returns (GX
        # calls `test_connection()` -> `get_engine()` before it registers the datasource).
        try:
            # `schema_name` is deprecated in GX 1.14+ ("pass the schema in your datasource's
            # connection configuration instead") but still load-bearing: `DatabricksSQLDatasource`
            asset = datasource.add_table_asset(name=table, table_name=table, schema_name=schema)
            return datasource, asset.add_batch_definition_whole_table(name="whole_table")
        except Exception:
            self._dispose_gx_engine(datasource)
            raise

    def _run_sql_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
        value_signal_gate: ValueSignalGate | None = None,
    ) -> SuiteOutcome:
        """Evaluate the SQL-batch group (custom SQL #1179, pushdown types #1532)
        on one Databricks-SQL batch.
        """
        if all(is_custom_sql(spec.expectation_type) for spec in checks):
            index_columns = None
        problem = self._sql_target_problem(table=table, schema=schema)
        if problem is not None:
            return self._sql_group_errored(checks, problem)
        assert schema is not None  # narrowed by `_sql_target_problem`
        # Acquired outside the try: a busy lock must reach the caller, not error the group.
        with ephemeral_gx_context() as context:
            datasource: Any = None
            try:
                checks = _fold_reflection_keyed_columns(checks)
                datasource, batch_definition = self._sql_batch_definition(
                    context, table=table, schema=schema
                )
                # A check on a column that is ALSO an index column runs without the index request:
                # the locator query would select the column twice and Databricks' arrow layer
                # refuses ("Can't unify schema with duplicate field names" — live-found, #1532).
                index_lower = {c.lower() for c in index_columns or ()}
                clash_set = {
                    i for i, spec in enumerate(checks) if _spec_columns(spec) & index_lower
                }
                if not clash_set:
                    return run_expectations(
                        context,
                        batch_definition=batch_definition,
                        checks=checks,
                        name=f"suite-uc-sql-{table}",
                        index_columns=index_columns,
                        value_signal_gate=value_signal_gate,
                    )
                keep = [i for i in range(len(checks)) if i not in clash_set]
                clash = sorted(clash_set)
                outcomes: dict[int, CheckOutcome] = {}
                success = True
                if keep:
                    kept_checks = [checks[i] for i in keep]
                    kept = run_expectations(
                        context,
                        batch_definition=batch_definition,
                        checks=kept_checks,
                        name=f"suite-uc-sql-{table}",
                        # Same rule as the top of this method: a keep group that is
                        # pure custom SQL has no use for the index request.
                        index_columns=(
                            None
                            if all(is_custom_sql(s.expectation_type) for s in kept_checks)
                            else index_columns
                        ),
                        value_signal_gate=value_signal_gate,
                    )
                    success = kept.success
                    outcomes.update(zip(keep, kept.checks, strict=True))
                clashed_checks = [checks[i] for i in clash]
                try:
                    clashed = run_expectations(
                        context,
                        batch_definition=batch_definition,
                        checks=clashed_checks,
                        name=f"suite-uc-sql-noidx-{table}",
                        value_signal_gate=value_signal_gate,
                    )
                except Exception as exc:
                    # Error ONLY the not-yet-evaluated group; the keep group's real
                    # outcomes are already computed and must survive.
                    log.exception("uc_sql_batch_unavailable", table=table)
                    clashed = self._sql_group_errored(clashed_checks, classify_failure_reason(exc))
                success = success and clashed.success
                outcomes.update(zip(clash, clashed.checks, strict=True))
                return SuiteOutcome(
                    success=success, checks=[outcomes[i] for i in range(len(checks))]
                )
            except Exception as exc:
                # Building the SQL batch can fail on its own — GX tests the connection inside
                # `add_databricks_sql` and validates the table inside `add_table_asset`.
                log.exception("uc_sql_batch_unavailable", table=table)
                return self._sql_group_errored(checks, classify_failure_reason(exc))
            finally:
                if datasource is not None:
                    self._dispose_gx_engine(datasource)

    @staticmethod
    def _sql_group_errored(checks: list[CheckSpec], reason: str) -> SuiteOutcome:
        """One operational `error` outcome per check in the SQL group, siblings untouched."""
        return SuiteOutcome(
            success=False,
            checks=[
                CheckOutcome(
                    expectation_type=spec.expectation_type,
                    success=False,
                    errored=True,
                    error_message=reason,
                    expected_value=dict(spec.authored_kwargs or spec.kwargs) or None,
                )
                for spec in checks
            ],
        )

    @staticmethod
    def _dispose_gx_engine(datasource: Any) -> None:
        """Close the warehouse session GX opened for its own SQL datasource."""
        try:
            datasource.get_engine().dispose()
        except Exception as exc:
            log.warning("uc_sql_engine_dispose_failed", error_type=type(exc).__name__)

    def run_native_checks(
        self,
        engine: str,
        checks: list[tuple[str, str, dict[str, Any]]],
        *,
        table: str,
        schema: str | None,
        previous: list[dict[str, Any] | None] | None = None,
    ) -> list[CheckOutcome]:
        """Every ``engine`` check of a run in one batch — one DQX job, not one per check.
        ``previous`` is each check's last evaluated ``observed_value`` (stream checks resume
        from it)."""
        specs = [(expectation_type, config) for _kind, expectation_type, config in checks]
        if engine != DQX_ENGINE or schema is None:
            reason = (
                f"engine {engine!r} is not evaluated by this runner"
                if engine != DQX_ENGINE
                else "a dqx check needs the suite target's schema (catalog.schema.table)"
            )
            return [
                CheckOutcome(expectation_type=t, success=False, errored=True, error_message=reason)
                for t, _config in specs
            ]
        jobs = DqxJobs(workspace_url=self._config.workspace_url, token=self._token)
        return run_dqx_batch(
            jobs,
            specs,
            catalog=self._catalog,
            schema=schema,
            table=table,
            previous=previous,
            checkpoint_volume=self._config.dqx_checkpoint_volume,
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
        """One native check (the dry-run path); a batch of one."""
        (outcome,) = self.run_native_checks(
            DQX_ENGINE, [(kind, expectation_type, config)], table=table, schema=schema
        )
        return outcome

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors via scalar SQL aggregates over the SQL Warehouse (no
        GX / no DataFrame read), over the runner's shared engine (#427 — one connection per run,
        no per-call engine). The pinned ``catalog`` qualifies the target as
        ``catalog.schema.table``. A connection failure propagates; a bad monitor errors only
        itself.
        """
        return run_monitors_over_engine(
            self._engine.get(),
            table=table,
            schema=schema,
            catalog=self._catalog,
            monitors=monitors,
        )


def build_unity_catalog_runner(
    *,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    catalog: str,
    sampling: SampleSpec | None = None,
) -> UnityCatalogCheckRunner:
    """Build a runner from a UC `Connection`'s primitives + the target `catalog`."""
    if not secret_ref:
        raise ValueError("Unity Catalog connection requires secret_ref for the PAT")
    uc_config = UnityCatalogConfig.model_validate(config)
    token = secret_store.get(secret_ref)
    return UnityCatalogCheckRunner(
        config=uc_config, token=token, catalog=catalog, sampling=sampling
    )
