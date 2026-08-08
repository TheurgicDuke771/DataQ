"""Unity Catalog (Databricks) connection adapter.

A datasource (CLAUDE.md §4): DQ checks run against Unity Catalog tables via a
Databricks SQL Warehouse. Week 2 ships only the `ConnectionAdapter` seam (config
validation + connectivity `test`).

**Runner seam note:** the *check-run* path for UC must sit behind a
``UnityCatalogCheckRunner`` interface so v1.1 can swap GX for Databricks Labs DQX
on DLT/streaming (CLAUDE.md §5, ADR 0003). That runner is Week-3 work and is
deliberately **not** built here — this module is connection config + a
connectivity probe only.

Auth is a **personal access token (PAT)** — the v1 default, held in the
SecretStore (no credential-less mode, so none of the ADLS/S3 ``secret_ref``
nullability deferral applies). ``test`` opens a SQL-Warehouse connection and runs
``SELECT 1`` — a green test means the workspace + warehouse are reachable and the
PAT authenticates. ``databricks-sql-connector`` is imported lazily (per
``core/secrets.py``); like the other adapters it runs live and fails-soft pending
real credentials.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import quote_plus, urlparse

import great_expectations as gx
from pydantic import BaseModel, ConfigDict, field_validator

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome, CheckSpec, MonitorSpec, SuiteOutcome
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import FRESHNESS, VOLUME, run_monitors_over_engine
from backend.app.datasources.sql import LazyEngine, is_sql_identifier
from backend.app.services.custom_sql import is_custom_sql

log = get_logger(__name__)


class UnityCatalogConfig(BaseModel):
    """Non-secret Databricks/UC connection config (the PAT comes from secrets).

    Maps from ``Connection.config``. ``workspace_url`` is the workspace root
    (e.g. ``https://adb-1234.5.azuredatabricks.net``); ``warehouse_id`` is the
    SQL Warehouse id, from which the connector's ``http_path`` is built. The PAT
    is resolved from the SecretStore at test time.
    """

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

    def validate_config(self, raw: dict[str, Any]) -> UnityCatalogConfig:
        return UnityCatalogConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Open a SQL-Warehouse connection and run ``SELECT 1``; raise on failure.

        ``secret`` is the Databricks PAT. Deliberately GX/DQX-free — a lightweight
        connectivity probe, not a suite run. Typed ``str | None`` only because
        the shared `ConnectionAdapter` Protocol also serves credential-less
        types (#351) — a Databricks PAT is always mandatory, so the guard below
        turns a missing one into a clear error rather than a confusing SDK
        failure.
        """
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
    """SQLAlchemy URL for the Databricks SQL Warehouse (databricks dialect).

    The PAT, http_path, `catalog` and `schema` are URL-encoded. Pinning `catalog`
    sets the session default so a 2-level `schema.table` reference resolves to
    `catalog.schema.table`; the profiler leaves it unset and qualifies the
    namespace in the query instead.

    `schema` is a session default too, and every caller but one leaves it unset
    (they qualify the namespace themselves). The exception is GX's
    `DatabricksSQLDatasource`, whose `DatabricksDsn` validator **requires**
    `http_path`, `catalog` *and* `schema` on the URL before it will accept it —
    see `UnityCatalogCheckRunner._sql_batch_definition` (#1179).
    """
    url = (
        f"databricks://token:{quote_plus(token)}@{config.server_hostname}"
        f"?http_path={quote_plus(config.http_path)}"
    )
    if catalog:
        url += f"&catalog={quote_plus(catalog)}"
    if schema:
        url += f"&schema={quote_plus(schema)}"
    return url


class UnityCatalogCheckRunner:
    """GX `CheckRunner` for Unity Catalog via the Databricks SQL Warehouse.

    The UC run path reads the target table into a pandas DataFrame and validates
    that frame with GX — the "GX DataFrame datasource" shape (CLAUDE.md §5), the
    same shape Databricks Labs DQX consumes, so v1.1 can swap GX for DQX behind
    this same interface without touching the suite/check/result layer.

    The **one** exception is the custom-SQL check (ADR 0019), whose GX metrics
    have no pandas provider at all; it runs against a GX Databricks-SQL batch
    over the same table instead. `run_checks` owns that split — see its
    docstring for why, and #1179 for what it was before. The DataFrame shape is
    unchanged for every other expectation, so the DQX swap-in argument still
    holds where it applies (custom SQL is SQL by definition and was never going
    to be a DQX rule).

    `table` + `schema` come from `run_checks` (the suite's target); `catalog` is
    fixed per run (held here). The two live seams are `_read_table` (reflect +
    read the frame) and `_sql_batch_definition` (build the SQL batch), both
    monkeypatched in tests; GX then runs through the shared `gx_runner`, so the
    validation and result-mapping paths themselves are fully covered.
    """

    # Runner-advertised monitor capability (#429): EXPLICITLY what this runner
    # implements — never frozenset(MONITOR_KINDS), which would auto-advertise
    # every future registry entry and self-defeat the per-kind gate (a stateful
    # kind must be claimed by a runner only once it actually evaluates it).
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME})

    def __init__(self, *, config: UnityCatalogConfig, token: str, catalog: str) -> None:
        self._config = config
        self._token = token
        self._catalog = catalog
        # The runner's ONE lazily-built engine (#427), shared by the GX read
        # (`_read_table`) AND `run_monitors` — a mixed suite (expectations +
        # monitors) pays a single warehouse session instead of two. Disposed by
        # `close()`; the run path owns that lifecycle via `registry.owned_runner`.
        self._engine = LazyEngine(self._build_engine)

    def _build_engine(self) -> Any:
        from sqlalchemy import create_engine

        # pool_pre_ping: run_monitors may draw the connection _read_table checked
        # in before a long GX validation — revalidate on checkout so a
        # warehouse-side idle reap / auto-stop surfaces as a fresh connect, not a
        # dead connection failing every monitor (the old per-call engine always
        # got a fresh one).
        return create_engine(
            build_databricks_url(self._config, self._token, catalog=self._catalog),
            pool_pre_ping=True,
        )

    def close(self) -> None:
        """Dispose the shared engine's pool. Idempotent; a no-op if never used."""
        self._engine.close()

    def _read_table(self, *, table: str, schema: str | None) -> Any:
        """Reflect + read the whole table into a DataFrame (live seam).

        `read_sql_table` reflects through SQLAlchemy (proper dialect quoting), so
        the table/schema identifiers are never string-formatted into SQL; the
        pinned catalog + `schema` qualify it to `catalog.schema.table`.
        """
        import pandas as pd

        return pd.read_sql_table(table, self._engine.get(), schema=schema)

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        """Evaluate `checks`, routing each to the batch its expectation can run on.

        **Two batches, one target (#1179).** Almost every GX expectation is
        engine-agnostic and runs on the pandas frame this runner has always used
        (the DQX swap-in shape — see the class docstring). The one that is not is
        the custom-SQL check (ADR 0019 — a GX ``UnexpectedRowsExpectation``): its
        metrics are ``unexpected_rows_query.{table,row_count}``, which have a
        SqlAlchemy provider and no pandas one, so on the DataFrame batch GX
        raises ``No provider found for unexpected_rows_query.table using
        PandasExecutionEngine`` — the reported bug. Custom SQL had therefore
        **never** worked on Unity Catalog, in a run or a dry-run, since the
        capability was declared in ADR 0019.

        So custom-SQL checks (and only those) run against a **GX Databricks-SQL
        batch** over the same table. Deliberately GX's own SQL datasource rather
        than a hand-rolled COUNT/LIMIT of our own: the result semantics
        (``{batch}`` substitution, 0 rows → pass, the unexpected row count as
        ``observed_value``, a query error as an operational `error`) are then
        identical to the Snowflake path **by construction** instead of by
        re-implementation, and this module adds no SQL-string interpolation of
        its own, so the guardrail set stays exactly ADR 0019's (author-time
        read-only single-statement validation plus the connection's
        least-privilege role) with nothing new to weaken.

        The split is also why the two groups are re-merged **positionally**:
        `run_service` zips outcomes back onto its `checks` list, so the returned
        order must be submission order regardless of which batch evaluated what.

        Neither batch is built unless its group is non-empty — an all-custom-SQL
        suite never pays the full-table DataFrame read, and a suite with no
        custom SQL opens no second warehouse session.
        """
        sql_positions = [i for i, spec in enumerate(checks) if is_custom_sql(spec.expectation_type)]
        frame_positions = [
            i for i, spec in enumerate(checks) if not is_custom_sql(spec.expectation_type)
        ]
        # Keyed by submission position, never appended to: a missing key is a
        # loud KeyError below rather than a silently short/misaligned outcome
        # list, which `run_service`'s positional zip would map onto wrong checks.
        by_position: dict[int, CheckOutcome] = {}
        success = True
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
                table=table, schema=schema, checks=[checks[i] for i in sql_positions]
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
        df = self._read_table(table=table, schema=schema)
        context = gx.get_context(mode="ephemeral")
        asset = context.data_sources.add_pandas(name="uc").add_dataframe_asset(name="table")
        batch_definition = asset.add_batch_definition_whole_dataframe(name="whole_dataframe")
        return run_expectations(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name="suite-uc",
            batch_parameters={"dataframe": df},
            index_columns=index_columns,
        )

    def _sql_target_problem(self, *, table: str, schema: str | None) -> str | None:
        """Why this target can't back a SQL batch, or ``None`` when it can.

        Two guards. A UC suite target may legally omit the schema (the DataFrame
        read falls back to the session default), but GX's `DatabricksDsn`
        requires one on the URL and an unqualified name would in any case resolve
        against whatever the session default happens to be — a *wrong table* read
        rather than an error. And the three identifiers are interpolated into the
        DSN / asset, so they go through the shared #428 allowlist here: the
        message names the user's own configuration only (never target data), so
        it is safe to persist verbatim on the result row.
        """
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
        """A GX Databricks-SQL whole-table batch over the target (live seam).

        Returns ``(datasource, batch_definition)`` — the datasource because GX
        builds and owns the warehouse engine behind it, so the caller needs a
        handle to close it (`_dispose_gx_engine`).

        ``create_temp_table=False``: GX's SQL datasources offer a temp-table
        materialization we have no use for here (the custom-SQL metrics wrap the
        batch selectable directly), and asking a SQL Warehouse for one is a
        needless way to fail.
        """
        datasource = context.data_sources.add_databricks_sql(
            name=f"uc-sql-{table}",
            connection_string=build_databricks_url(
                self._config, self._token, catalog=self._catalog, schema=schema
            ),
            create_temp_table=False,
        )
        asset = datasource.add_table_asset(name=table, table_name=table, schema_name=schema)
        return datasource, asset.add_batch_definition_whole_table(name="whole_table")

    def _run_sql_checks(
        self, *, table: str, schema: str | None, checks: list[CheckSpec]
    ) -> SuiteOutcome:
        """Evaluate custom-SQL checks against a GX Databricks-SQL batch (#1179).

        ``index_columns`` is deliberately **not** threaded through:
        ``UnexpectedRowsExpectation`` is a batch expectation whose `_validate`
        reads only the two query metrics and never computes an
        ``unexpected_index_list``, so `unexpected_index_column_names` cannot
        change its result — while requesting it would arm `run_expectations`'
        all-errored retry and bill a second warehouse round-trip for a query
        whose only outcome is the same error.
        """
        problem = self._sql_target_problem(table=table, schema=schema)
        if problem is not None:
            # A target-shape problem is this group's own operational `error`,
            # not the run's: the sibling expectations on the DataFrame batch
            # evaluated fine and must still be persisted (#122).
            return SuiteOutcome(
                success=False,
                checks=[
                    CheckOutcome(
                        expectation_type=spec.expectation_type,
                        success=False,
                        errored=True,
                        error_message=problem,
                        expected_value=dict(spec.kwargs) or None,
                    )
                    for spec in checks
                ],
            )
        assert schema is not None  # narrowed by `_sql_target_problem`
        context = gx.get_context(mode="ephemeral")
        datasource, batch_definition = self._sql_batch_definition(
            context, table=table, schema=schema
        )
        try:
            return run_expectations(
                context,
                batch_definition=batch_definition,
                checks=checks,
                name=f"suite-uc-sql-{table}",
            )
        finally:
            self._dispose_gx_engine(datasource)

    @staticmethod
    def _dispose_gx_engine(datasource: Any) -> None:
        """Close the warehouse session GX opened for its own SQL datasource.

        GX builds and owns that engine internally (only a connection string goes
        in), so the runner's `close()` can't reach it. Without this it survives
        until the ephemeral context is garbage-collected — non-deterministic, and
        a long-lived Celery worker would hold a Databricks session per run in the
        meantime. Best-effort by design: a failure to tidy up must never replace
        the outcome the caller is returning — including in the `finally` of a
        call that is itself raising.

        Only the exception TYPE is logged, never its text: the engine was built
        from a URL carrying the PAT, and a driver/SQLAlchemy message is exactly
        the shape that has echoed a credential before (#849).
        """
        try:
            datasource.get_engine().dispose()
        except Exception as exc:
            log.warning("uc_sql_engine_dispose_failed", error_type=type(exc).__name__)

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors via scalar SQL aggregates over the SQL
        Warehouse (no GX / no DataFrame read), over the runner's shared engine
        (#427 — one connection per run, no per-call engine). The pinned
        ``catalog`` qualifies the target as ``catalog.schema.table``. A connection
        failure propagates; a bad monitor errors only itself."""
        return run_monitors_over_engine(
            self._engine.get(),
            table=table,
            schema=schema,
            catalog=self._catalog,
            monitors=monitors,
        )


def build_unity_catalog_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, catalog: str
) -> UnityCatalogCheckRunner:
    """Build a runner from a UC `Connection`'s primitives + the target `catalog`.

    Mirrors `build_snowflake_runner`: resolves the PAT eagerly and takes the raw
    config dict (not the ORM model) to keep the adapter decoupled from `db/`.
    """
    if not secret_ref:
        raise ValueError("Unity Catalog connection requires secret_ref for the PAT")
    uc_config = UnityCatalogConfig.model_validate(config)
    token = secret_store.get(secret_ref)
    return UnityCatalogCheckRunner(config=uc_config, token=token, catalog=catalog)
