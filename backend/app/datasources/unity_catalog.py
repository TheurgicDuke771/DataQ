"""Unity Catalog (Databricks) connection adapter."""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import quote_plus, urlparse

import great_expectations as gx
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
)
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import FRESHNESS, VOLUME, run_monitors_over_engine
from backend.app.datasources.sampling import (
    SamplingDrawError,
    enforce_row_cap,
    enforce_sample_cap,
    sampling_record,
    split_row_count_checks,
    stamp_sampling,
)
from backend.app.datasources.sql import (
    LazyEngine,
    core_table,
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
    # Warehouse inventory sync opt-in (#919, ADR 0040) — see SnowflakeConfig.
    inventory_sync: bool = False

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
    }
)

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


def format_sample_percent(percent: float) -> str:
    """Render ``percent`` as a fixed-point DECIMAL literal Databricks will parse."""
    return f"{percent:.{_SAMPLE_PERCENT_DECIMALS}f}"


class UnityCatalogCheckRunner:
    """GX `CheckRunner` for Unity Catalog via the Databricks SQL Warehouse."""

    # Runner-advertised monitor capability (#429): EXPLICITLY what this runner implements — never
    # frozenset(MONITOR_KINDS).
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME})

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
        """Reflect + read the whole table into a DataFrame (live seam)."""
        import pandas as pd

        return pd.read_sql_table(table, self._engine.get(), schema=schema)

    def _count_rows(self, *, table: str, schema: str | None) -> int:
        """``COUNT(*)`` over the target — the size probe (live seam, #595)."""
        from sqlalchemy import func, select

        engine = self._engine.get()
        target = core_table(
            table=table,
            schema=schema,
            catalog=self._catalog if schema else None,
            dialect=engine.dialect,
        )
        with engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(target)).scalar_one())

    def _read_sampled_table(
        self, *, table: str, schema: str | None, sample: SampleSpec
    ) -> tuple[Any, dict[str, Any]]:
        """A bounded sample of the target, pushed down to the warehouse (#595)."""
        import pandas as pd
        from sqlalchemy import text

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
        frame = pd.read_sql_query(text(statement), engine)

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
            return self._read_sampled_table(table=table, schema=schema, sample=self._sampling)
        cap = settings.run_max_scan_rows
        if cap > 0:
            enforce_row_cap(
                self._count_rows(table=table, schema=schema), cap=cap, target=f"table {table!r}"
            )
        return self._read_table(table=table, schema=schema), None

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
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
        context = gx.get_context(mode="ephemeral")
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
        context = gx.get_context(mode="ephemeral")
        datasource: Any = None
        try:
            datasource, batch_definition = self._sql_batch_definition(
                context, table=table, schema=schema
            )
            # A check on a column that is ALSO an index column runs without the index request: the
            # locator query would select the column twice and Databricks' arrow layer refuses
            # ("Can't unify schema with duplicate field names" — live-found, #1532).
            index_lower = {c.lower() for c in index_columns or ()}
            clash_set = {
                i
                for i, spec in enumerate(checks)
                if str(spec.kwargs.get("column", "")).lower() in index_lower
            }
            if not clash_set:
                return run_expectations(
                    context,
                    batch_definition=batch_definition,
                    checks=checks,
                    name=f"suite-uc-sql-{table}",
                    index_columns=index_columns,
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
                )
            except Exception as exc:
                # Error ONLY the not-yet-evaluated group; the keep group's real
                # outcomes are already computed and must survive.
                log.exception("uc_sql_batch_unavailable", table=table)
                clashed = self._sql_group_errored(clashed_checks, classify_failure_reason(exc))
            success = success and clashed.success
            outcomes.update(zip(clash, clashed.checks, strict=True))
            return SuiteOutcome(success=success, checks=[outcomes[i] for i in range(len(checks))])
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
                    expected_value=dict(spec.kwargs) or None,
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
