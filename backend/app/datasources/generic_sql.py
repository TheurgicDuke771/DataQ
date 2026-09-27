"""The engine-generic SQLAlchemy datasource base (#1678).

One adapter, one runner and one table enumerator for any SQL engine that GX can address through
a SQLAlchemy dialect and that DataQ reaches with a host / port / database / user + password.
An engine plugs in with a `SqlEngineSpec` — its config model, driver, GX datasource factory,
connect-args and catalog queries — never with a copy of this module (ADR 0010/0013: the adapter
is named for the engine, never for a vendor that hosts it).

Everything runs by pushdown: expectations on a GX SQL batch, monitors as scalar Core aggregates,
so no rows are ever materialised in the worker (and a run target takes no ``sampling`` block).
Every session DataQ opens is READ ONLY at the session level — DataQ never writes to a
datasource, so a custom-SQL check that slips a side effect past the ADR 0019 validator still
cannot change data.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.core.errors import SafeMonitorError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import (
    CheckOutcome,
    CheckSpec,
    MonitorSpec,
    SuiteOutcome,
    ValueSignalGate,
)
from backend.app.datasources.monitors import FRESHNESS, VOLUME, run_monitors_over_engine
from backend.app.datasources.sql import LazyEngine, is_sql_identifier

if TYPE_CHECKING:
    from sqlalchemy.engine import URL

log = get_logger(__name__)

# A hostname or IPv4 literal; an IPv6 literal (bracket-less) is checked by `ipaddress` instead.
# Deliberately excludes `/`, `@`, `:` and whitespace: the host lands in the asset identity
# (ADR 0034) and decides where the password is sent (#1401), so a URL, a port or userinfo smuggled
# through it must be refused, not parsed around.
_HOST_RE = re.compile(r"[A-Za-z0-9._\-]+")
_MAX_HOST = 253
_MAX_NAME = 128
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _is_ipv6(host: str) -> bool:
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    return True


#: Seconds a login / connect may take before the test, profiler or browse gives up.
CONNECT_TIMEOUT = 10


class GenericSqlConfig(BaseModel):
    """Non-secret config every generic SQL engine shares (the password is the connection's
    secret). An engine subclasses this for its TLS vocabulary and its default port/schema.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    #: The engine's standard port, used when ``port`` is omitted.
    default_port: ClassVar[int]
    #: The engine's identifier length limit. Enforced, not left to the server: PostgreSQL
    #: silently TRUNCATES a longer name, which would resolve a different object.
    max_identifier_length: ClassVar[int]

    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str
    user: str
    # The schema an unqualified run target resolves in. `None` means the engine's own default.
    schema_: str | None = Field(default=None, alias="schema")
    # Warehouse inventory sync (ADR 0040) — on by default; see SnowflakeConfig.
    inventory_sync: bool = True

    @field_validator("port", "schema_", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # A cleared optional form field arrives as "" — it means "use the default", not an error.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("host")
    @classmethod
    def _plain_host(cls, value: str) -> str:
        host = value.strip()
        if not host or len(host) > _MAX_HOST or not (_HOST_RE.fullmatch(host) or _is_ipv6(host)):
            raise ValueError(
                "host must be a bare hostname or IP address (no scheme, port, path or "
                "credentials — the port has its own field)"
            )
        return host

    @field_validator("database", "user")
    @classmethod
    def _plain_name(cls, value: str | None) -> str | None:
        # `None` only reaches here where an engine made the field optional (an SQL Server
        # service principal has no `user`); the engine's own validator decides if it may be.
        if value is None:
            return None
        if not value or len(value) > _MAX_NAME or _CONTROL.search(value):
            raise ValueError(f"must be 1-{_MAX_NAME} characters with no control characters")
        return value

    @field_validator("schema_")
    @classmethod
    def _schema_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_sql_identifier(value) or len(value) > cls.max_identifier_length:
            raise ValueError(
                "schema must be a plain SQL identifier (letters, digits, _ and $; not "
                f"starting with a digit) of at most {cls.max_identifier_length} characters"
            )
        return value

    @property
    def effective_port(self) -> int:
        return self.port if self.port is not None else self.default_port

    @property
    def default_schema(self) -> str:
        """The schema an unqualified target resolves in."""
        return self.schema_ or self.engine_default_schema()

    def engine_default_schema(self) -> str:
        """The engine's own default schema when the connection names none."""
        raise NotImplementedError


@dataclass(frozen=True)
class ColumnCaps:
    """What the engine can aggregate over one column's type (the profiler's question)."""

    #: MIN/MAX exist for the type.
    orderable: bool = True
    #: Equality exists for the type — COUNT(DISTINCT) and GROUP BY work.
    groupable: bool = True


#: `(conn, schema, table)` → the per-column capabilities of a live table.
ColumnCapsReader = Callable[[Any, str, str], dict[str, ColumnCaps]]

#: `(context, name, connection_string, engine_kwargs)` → a registered GX SQL datasource.
GxDatasourceFactory = Callable[[Any, str, str, dict[str, Any]], Any]


@dataclass(frozen=True)
class SqlCatalog:
    """The engine's catalog queries — the ONE place a generic engine names its system views, so
    the inventory sync (ADR 0040) and the run-target browser (#466) list the same tables.
    """

    #: ``SELECT <schema>`` over every schema the credential may use, excluding system schemas.
    #: Takes ``:lim``.
    schemas_sql: str
    #: ``SELECT <schema>, <table>`` over every table/view the credential may read. Takes
    #: ``:schema`` (NULL = every schema) and ``:lim``.
    tables_sql: str


@dataclass(frozen=True)
class SqlEngineSpec:
    """Everything that differs between two generic SQL engines."""

    conn_type: str
    #: Human name for messages and the LLM dialect hint — the ENGINE, never a hosting vendor.
    display_name: str
    config_model: type[GenericSqlConfig]
    drivername: str
    gx_datasource: GxDatasourceFactory
    #: DBAPI ``connect_args`` for a session: TLS, timeout, and the session-level READ ONLY guard.
    connect_args: Callable[[Any, int | None], dict[str, Any]]
    #: OpenLineage namespace scheme (``postgres://host:port``).
    namespace_scheme: str
    #: Whether the asset name carries the database ahead of the schema (``db.schema.table``).
    #: False where a schema IS a database (MySQL: ``db.table``).
    database_in_name: bool
    catalog: SqlCatalog
    #: Reads which columns of a table the profiler must not MIN/MAX or GROUP BY, for an engine
    #: whose aggregates are not defined over every type. ``None`` = every type aggregates.
    column_caps: ColumnCapsReader | None = None
    #: Extra ``create_engine`` keyword arguments for the RUN path's engines (GX's and the
    #: monitors'), e.g. how the driver hands back JSON cells.
    run_engine_options: dict[str, Any] = field(default_factory=dict)
    #: Whether a SESSION can be scoped to the run target's schema (a PostgreSQL ``search_path``,
    #: a MySQL default database). SQL Server cannot — a login's default schema is fixed on the
    #: server — so there GX is handed the schema instead.
    session_schema: bool = True
    #: Per credential slot, the config fields that decide where the secret is sent (#1401).
    destination_fields: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {"secret": ("host", "port")}
    )
    #: What the connection's one secret is called in messages.
    credential_noun: str = "password"
    #: Turns a failure whose cause the ENGINE knows (a documented driver limitation, a missing
    #: optional driver) into a DataQ-authored message that is safe to show verbatim. ``None`` —
    #: or a ``None`` return — leaves the failure to the generic classifier.
    explain_failure: Callable[[GenericSqlConfig, BaseException], str | None] | None = None
    #: How the SQL generator names the dialect to the model, when ``"<display_name> SQL"`` would
    #: not say enough (T-SQL has no LIMIT and quotes with brackets).
    llm_dialect: str | None = None
    #: Allowlisted expectation types GX has no translation for on this dialect — refused at
    #: author time (a check that saves cleanly and errors on every run is the alternative),
    #: with ``unsupported_reason`` saying why and what to use instead.
    unsupported_expectation_types: frozenset[str] = frozenset()
    unsupported_reason: str = ""

    def validate_config(self, raw: dict[str, Any]) -> GenericSqlConfig:
        return self.config_model.model_validate(raw)

    def scoped_config(self, config: GenericSqlConfig, schema: str | None) -> GenericSqlConfig:
        """``config`` with its default schema set to ``schema`` — RE-validated, never
        `model_copy`'d: the schema reaches the session's own settings (a PostgreSQL
        ``search_path``, a MySQL default database), so it must pass the identifier allowlist.
        """
        if schema is None:
            return config
        return self.validate_config({**config.model_dump(by_alias=True), "schema": schema})

    def url(self, config: GenericSqlConfig, secret: str) -> URL:
        """The SQLAlchemy URL — built with `URL.create`, which escapes every part."""
        from sqlalchemy.engine import URL

        return URL.create(
            self.drivername,
            username=config.user,
            password=secret,
            host=config.host,
            port=config.effective_port,
            database=config.database,
        )

    def url_string(self, config: GenericSqlConfig, secret: str) -> str:
        """`url` rendered with the password, for GX (which takes a string). Never log it."""
        return self.url(config, secret).render_as_string(hide_password=False)

    def engine_args(
        self, config: GenericSqlConfig, secret: str, *, timeout: int | None = CONNECT_TIMEOUT
    ) -> tuple[str, dict[str, Any]]:
        """``(url, connect_args)`` for a plain SQLAlchemy engine (profiler, schema drift, …)."""
        return self.url_string(config, secret), self.connect_args(config, timeout)

    def namespace(self, config: GenericSqlConfig) -> str:
        """The OpenLineage namespace: ``<scheme>://<host>:<port>`` (host case-folded)."""
        host = config.host.lower()
        if ":" in host:  # an IPv6 literal needs its brackets back in an authority
            host = f"[{host}]"
        return f"{self.namespace_scheme}://{host}:{config.effective_port}"

    def asset_name(self, config: GenericSqlConfig, *, schema: str, table: str) -> str:
        """The OpenLineage name. Parts are kept VERBATIM: these engines resolve a name DataQ
        emits exactly as spelled (see `sql.folding_identifier`), and their catalogs report it
        the same way, so a suite target and an enumerated table join byte-for-byte.
        """
        parts = (config.database, schema, table) if self.database_in_name else (schema, table)
        return ".".join(parts)


class KnownDatasourceLimitationError(SafeMonitorError, RuntimeError):
    """A failure the engine explained (`SqlEngineSpec.explain_failure`), or a precondition it
    checked: the message is DataQ-authored, so it reaches the user verbatim. The driver error, if
    any, stays the ``__cause__`` for the server log.
    """


def explained(spec: SqlEngineSpec, config: GenericSqlConfig, exc: Exception) -> Exception:
    """``exc``, or the engine's explanation of it when it has one."""
    if spec.explain_failure is None or isinstance(exc, SafeMonitorError):
        return exc
    message = spec.explain_failure(config, exc)
    if not message:
        return exc
    log.warning(
        "generic_sql_failure_explained", conn_type=spec.conn_type, error_type=type(exc).__name__
    )
    return KnownDatasourceLimitationError(message)


class GenericSqlConnectionAdapter:
    """`ConnectionAdapter` for one generic SQL engine — config validation + a ``SELECT 1``."""

    def __init__(self, spec: SqlEngineSpec) -> None:
        self.spec = spec
        # #1401: e.g. the host and port decide which server receives the password.
        self.destination_fields: dict[str, tuple[str, ...]] = spec.destination_fields

    def validate_config(self, raw: dict[str, Any]) -> GenericSqlConfig:
        return self.spec.validate_config(raw)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Open a session and run ``SELECT 1``; raise on any failure."""
        if secret is None:
            raise ValueError(
                f"a {self.spec.credential_noun} is required to test a "
                f"{self.spec.display_name} connection"
            )
        from sqlalchemy import create_engine, text

        config = self.validate_config(raw)
        try:
            url, connect_args = self.spec.engine_args(config, secret)
            engine = create_engine(url, connect_args=connect_args)
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            finally:
                engine.dispose()
        except Exception as exc:
            reason = explained(self.spec, config, exc)
            if reason is exc:
                raise
            raise reason from exc


class GenericSqlCheckRunner:
    """`CheckRunner` for a generic SQL engine: one GX SQL batch per run, monitors by scalar SQL."""

    # Runner-advertised monitor capability (#429): EXPLICITLY what this runner implements.
    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME})
    # The run path hands a `value_signal_gate` only to runners advertising it (#2014).
    accepts_value_signal_gate: ClassVar[bool] = True

    def __init__(self, spec: SqlEngineSpec, config: GenericSqlConfig, secret: str) -> None:
        self._spec = spec
        self._config = config
        self._secret = secret
        # The runner's ONE lazily-built engine (#427) for the non-GX SQL (monitors).
        self._engine = LazyEngine(self._build_engine)

    def _build_engine(self) -> Any:
        from sqlalchemy import create_engine

        url, connect_args = self._spec.engine_args(self._config, self._secret, timeout=None)
        # pool_pre_ping: the session can sit idle across a long GX validation.
        return create_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
            **self._spec.run_engine_options,
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
        value_signal_gate: ValueSignalGate | None = None,
    ) -> SuiteOutcome:
        try:
            return self._run_checks(
                table=table,
                schema=schema,
                checks=checks,
                index_columns=index_columns,
                value_signal_gate=value_signal_gate,
            )
        except Exception as exc:
            reason = explained(self._spec, self._config, exc)
            if reason is exc:
                raise
            raise reason from exc

    def _run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None,
        value_signal_gate: ValueSignalGate | None,
    ) -> SuiteOutcome:
        import great_expectations as gx

        from backend.app.datasources.gx_runner import run_expectations

        # GX lower-cases an unquoted schema name, which on an engine that resolves names exactly
        # as spelled retargets a mixed-case schema. So where the engine can, the SESSION is
        # scoped to the target's schema instead (the engine's default schema) and GX gets no
        # schema at all. Where it cannot (SQL Server), GX gets the schema — which its default
        # case-insensitive collations resolve whatever GX's casing.
        if self._spec.session_schema:
            scoped = self._spec.scoped_config(self._config, schema)
            gx_schema = None
        else:
            scoped = self._config
            gx_schema = schema or self._config.default_schema
        connections = GxConnectionSource(self._spec, scoped, self._secret)
        context = gx.get_context(mode="ephemeral")
        datasource: Any = None
        try:
            # GX tests the connection inside `add_*`, so a failure there must still close it.
            datasource = self._spec.gx_datasource(
                context,
                f"{self._spec.conn_type}-{table}",
                self._spec.url_string(scoped, self._secret),
                {
                    "connect_args": self._spec.connect_args(scoped, None),
                    "creator": connections.connect,
                    **self._spec.run_engine_options,
                },
            )
            asset = datasource.add_table_asset(
                name=table, table_name=gx_table_name(table), schema_name=gx_schema
            )
            batch_definition = asset.add_batch_definition_whole_table(name="whole_table")
            return run_expectations(
                context,
                batch_definition=batch_definition,
                checks=checks,
                name=f"suite-{table}",
                index_columns=index_columns,
                value_signal_gate=value_signal_gate,
            )
        finally:
            if datasource is not None:
                _dispose_gx_engine(datasource)
            connections.close()

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors via scalar SQL aggregates (no GX)."""
        try:
            return run_monitors_over_engine(
                self._engine.get(),
                table=table,
                schema=schema or self._config.default_schema,
                catalog=None,
                monitors=monitors,
            )
        except Exception as exc:
            reason = explained(self._spec, self._config, exc)
            if reason is exc:
                raise
            raise reason from exc


class GxConnectionSource:
    """The DBAPI connections a GX run is allowed to use, opened by DataQ and closed by DataQ.

    GX builds a fresh SQLAlchemy engine for every execution engine it makes and never disposes
    them, so a run left one server session per run open until garbage collection (live-found:
    three runs, three idle PostgreSQL backends — an OLTP server's connection slots are small and
    shared). Handed to GX as its engines' `creator`, this source records every connection it
    opens and `close` shuts them all, whichever engine pooled them.
    """

    def __init__(self, spec: SqlEngineSpec, config: GenericSqlConfig, secret: str) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        url, connect_args = spec.engine_args(config, secret, timeout=None)
        self._source = create_engine(url, connect_args=connect_args, poolclass=NullPool)
        self._opened: list[Any] = []

    def connect(self) -> Any:
        proxied = self._source.raw_connection()
        # The caller's pool owns it from here; NullPool would close it on check-in otherwise.
        proxied.detach()
        connection = proxied.dbapi_connection
        self._opened.append(connection)
        return connection

    def close(self) -> None:
        while self._opened:
            connection = self._opened.pop()
            try:
                connection.close()
            except Exception as exc:  # already closed by its pool, or the server went away
                log.debug("generic_sql_gx_connection_close_failed", error_type=type(exc).__name__)
        self._source.dispose()


def gx_table_name(table: str) -> str:
    """The table name as GX must be handed it so the name is resolved exactly as spelled.

    GX lower-cases an unquoted name; a name bracketed by quotes is unwrapped into a
    `quoted_name(quote=True)` the dialect re-quotes in its own style (backticks on MySQL). An
    all-lower-case name stays bare — the same decision `sql.folding_identifier` makes for the
    Core statements, so a check and a monitor on one target resolve the same object.
    """
    if not is_sql_identifier(table):
        raise ValueError(f"invalid table identifier: {table[:128]!r}")
    return table if table == table.lower() else f'"{table}"'


def _dispose_gx_engine(datasource: Any) -> None:
    """Close the pool GX opened for its own datasource — an OLTP server's connection slots are a
    shared, small resource, so a finished run must not keep one checked out.
    """
    try:
        datasource.get_engine().dispose()
    except Exception as exc:
        log.warning("generic_sql_gx_engine_dispose_failed", error_type=type(exc).__name__)


def build_generic_sql_runner(
    spec: SqlEngineSpec,
    *,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
) -> GenericSqlCheckRunner:
    """Build a runner from a `Connection` row's config + secret_ref."""
    if not secret_ref:
        raise ValueError(f"{spec.display_name} connection requires secret_ref for the password")
    validated = spec.validate_config(config)
    return GenericSqlCheckRunner(spec, validated, secret_store.get(secret_ref))


def schema_names(spec: SqlEngineSpec, conn: Any, *, limit: int | None) -> list[str]:
    """Schemas the credential may use, in name order — see `SqlCatalog.schemas_sql`."""
    from sqlalchemy import text

    rows = conn.execute(text(spec.catalog.schemas_sql), {"lim": _limit(limit)}).all()
    return [str(name) for (name,) in rows if name]


def table_rows(
    spec: SqlEngineSpec, conn: Any, *, schema: str | None = None, limit: int | None = None
) -> list[tuple[str, str]]:
    """``(schema, table)`` for every table/view the credential may read, optionally one schema."""
    from sqlalchemy import text

    rows = conn.execute(
        text(spec.catalog.tables_sql), {"schema": schema, "lim": _limit(limit)}
    ).all()
    return [(str(s), str(t)) for s, t in rows if s and t]


# No LIMIT is a "no limit" bind value both engines accept as a plain integer.
_NO_LIMIT = 2**62


def _limit(limit: int | None) -> int:
    return _NO_LIMIT if limit is None else int(limit)
