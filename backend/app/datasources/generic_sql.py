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

import gc
import ipaddress
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

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
from backend.app.datasources.sql import LazyEngine, is_sql_identifier, object_type

if TYPE_CHECKING:
    from datetime import datetime

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


#: The TLS modes every generic engine offers, in libpq's vocabulary. ``require`` is the default:
#: a mode that silently downgrades to plaintext when the server lacks TLS (libpq's ``prefer``,
#: PyMySQL's default) is not offered at all, and ``disable`` must be chosen explicitly.
SslMode = Literal["disable", "require", "verify-ca", "verify-full"]

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
    #: The engine folds every identifier to lower case, quoted or not, and its catalog reports
    #: names that way (Trino) — so a configured or targeted name must be lower case to join.
    names_are_lower_case: ClassVar[bool] = False
    #: Some configs of this engine authenticate with no stored secret (Trino ``auth_type:
    #: none``); `requires_secret` then says which.
    secret_optional: ClassVar[bool] = False

    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str
    user: str
    # The schema an unqualified run target resolves in. `None` means the engine's own default.
    schema_: str | None = Field(default=None, alias="schema")
    # Warehouse inventory sync (ADR 0040) — on by default; see SnowflakeConfig.
    inventory_sync: bool = True
    sslmode: SslMode = "require"

    @field_validator("sslmode", mode="before")
    @classmethod
    def _blank_sslmode_is_the_default(cls, value: Any) -> Any:
        return (
            "require" if value is None or (isinstance(value, str) and not value.strip()) else value
        )

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
        cls._check_identifier(value, "schema")
        return value

    @classmethod
    def _check_identifier(cls, value: str, label: str) -> None:
        """Raise unless ``value`` is a name this engine addresses as spelled (see
        `identifier_problem`)."""
        problem = cls.identifier_problem(value)
        if problem is not None:
            raise ValueError(f"{label} {problem}")

    @classmethod
    def identifier_problem(cls, value: str) -> str | None:
        """Why ``value`` is not an identifier this engine resolves as spelled, or ``None``."""
        if not is_sql_identifier(value) or len(value) > cls.max_identifier_length:
            return (
                "must be a plain SQL identifier (letters, digits, _ and $; not starting with a "
                f"digit) of at most {cls.max_identifier_length} characters"
            )
        if cls.names_are_lower_case and value != value.lower():
            return (
                f"must be lower case — this engine reports every name in lower case, so "
                f"{value!r} is {value.lower()!r}"
            )
        return None

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

    def url_database(self) -> str:
        """The database the session connects to. An engine where a schema IS a database
        (MySQL) connects to the schema instead, which is how its session gets scoped.
        """
        return self.database

    def requires_secret(self) -> bool:
        """Whether this config authenticates with the connection's stored secret."""
        return True

    def url_query(self) -> dict[str, str]:
        """Non-secret driver options the URL's query carries (Athena's workgroup)."""
        return {}


@dataclass(frozen=True)
class ColumnCaps:
    """What the engine can aggregate over one column's type (the profiler's question)."""

    #: MIN/MAX exist for the type.
    orderable: bool = True
    #: Equality exists for the type — COUNT(DISTINCT) and GROUP BY work.
    groupable: bool = True


#: `(conn, schema, table)` → the per-column capabilities of a live table.
ColumnCapsReader = Callable[[Any, str, str], dict[str, ColumnCaps]]


class ConnectArgs(Protocol):
    """``(config, timeout, *, read_only)`` → DBAPI ``connect_args`` for one session."""

    def __call__(
        self, config: Any, timeout: int | None, *, read_only: bool = True
    ) -> dict[str, Any]: ...


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
    #: DBAPI ``connect_args`` for a session: TLS, timeout, and the session-level READ ONLY guard
    #: (on unless the caller asks otherwise — only `temp_table_types` ever does).
    connect_args: ConnectArgs
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
    #: Expectation types GX evaluates on this dialect by creating session TEMPORARY tables, which a
    #: read-only session refuses (GX's MySQL uniqueness check). They run in a separate GX session
    #: without the read-only guard; nothing a user wrote runs there — custom SQL never does.
    temp_table_types: frozenset[str] = frozenset()
    #: Statements every new session runs, given whether it must be read-only — for an engine
    #: whose driver can only carry ONE init statement (PyMySQL's `init_command`) but needs two,
    #: each spelled in the syntax every server version accepts. ``None`` = `connect_args` does it.
    session_statements: Callable[[bool], tuple[str, ...]] | None = None
    #: ``(config, secret)`` → the DBAPI ``connect_args`` that authenticate, for an engine whose
    #: driver takes an auth OBJECT rather than a URL password (Trino: basic or a JWT bearer).
    #: When set, the secret is never put in the URL. ``None`` = the password rides the URL.
    auth_connect_args: Callable[[Any, str | None], dict[str, Any]] | None = None
    #: ``(config, secret)`` → when the credential stops working (a JWT's ``exp``), or ``None``.
    credential_expiry: Callable[[Any, str | None], datetime | None] | None = None
    #: #1401: the config fields that decide which server receives the secret — and, for an
    #: engine with a custom CA option, whose certificate is trusted to be that server.
    destination_fields: tuple[str, ...] = ("host", "port")
    #: Whether a SESSION can be scoped to the run target's schema (a PostgreSQL ``search_path``,
    #: a MySQL default database). SQL Server cannot — a login's default schema is fixed on the
    #: server — so there GX is handed the schema instead.
    session_schema: bool = True
    #: Hand GX the schema even though the session is scoped to it — for a dialect whose GX column
    #: lookup otherwise matches the table NAME in every schema (Redshift's reads
    #: ``information_schema.columns`` by table name alone, merging same-named tables). Safe only
    #: where names are lower case, since GX lower-cases the schema it is given.
    gx_schema_with_session: bool = False
    #: The view schema drift reads column names and types from (``table_schema``,
    #: ``table_name``, ``column_name``, ``data_type``, ``ordinal_position``) — Redshift's
    #: ``information_schema.columns`` omits late-binding views.
    columns_view: str = "information_schema.columns"
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
    #: ``config`` → ``(types, reason)`` for a gap that depends on WHERE the connection points
    #: rather than on the engine (a Fabric SQL endpoint refuses the temp tables GX's multi-column
    #: SQL Server metrics build). ``None`` = no config-dependent gaps.
    config_unsupported_expectation_types: (
        Callable[[GenericSqlConfig], tuple[frozenset[str], str]] | None
    ) = None
    #: OpenLineage's convention for a regional service endpoint (Athena) names no port.
    namespace_includes_port: bool = True
    #: ``config`` → the namespace's host part, for an engine whose OpenLineage convention is not
    #: the configured host (Redshift names ``<cluster>.<region>``). ``None`` = the host.
    namespace_authority: Callable[[Any], str] | None = None

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

    def url(self, config: GenericSqlConfig, secret: str | None) -> URL:
        """The SQLAlchemy URL — built with `URL.create`, which escapes every part."""
        from sqlalchemy.engine import URL

        return URL.create(
            self.drivername,
            username=config.user,
            password=None if self.auth_connect_args is not None else secret,
            host=config.host,
            port=config.effective_port,
            database=config.url_database(),
            query=config.url_query(),
        )

    def url_string(self, config: GenericSqlConfig, secret: str | None) -> str:
        """`url` rendered with the password, for GX (which takes a string). Never log it."""
        return self.url(config, secret).render_as_string(hide_password=False)

    def session_connect_args(
        self,
        config: GenericSqlConfig,
        secret: str | None,
        timeout: int | None,
        *,
        read_only: bool = True,
    ) -> dict[str, Any]:
        """`connect_args` plus, for an engine that authenticates by object, its auth args."""
        args = self.connect_args(config, timeout, read_only=read_only)
        if self.auth_connect_args is not None:
            args = {**args, **self.auth_connect_args(config, secret)}
        return args

    def engine_args(
        self,
        config: GenericSqlConfig,
        secret: str | None,
        *,
        timeout: int | None = CONNECT_TIMEOUT,
        read_only: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        """``(url, connect_args)`` for a SQLAlchemy engine. An engine built from these must also go
        through `prepare_engine`, which applies `session_statements`.
        """
        return self.url_string(config, secret), self.session_connect_args(
            config, secret, timeout, read_only=read_only
        )

    def prepare_engine(self, engine: Any, *, read_only: bool = True) -> Any:
        """Run `session_statements` on every connection ``engine`` opens from now on. Returns
        ``engine``. Connections already pooled predate the hook — dispose them first.
        """
        if self.session_statements is None:
            return engine
        from sqlalchemy import event

        statements = self.session_statements(read_only)

        def _setup(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                for statement in statements:
                    cursor.execute(statement)
            finally:
                cursor.close()

        event.listen(engine, "connect", _setup)
        return engine

    def create_engine(
        self,
        config: GenericSqlConfig,
        secret: str | None,
        *,
        timeout: int | None = CONNECT_TIMEOUT,
        read_only: bool = True,
        **engine_kwargs: Any,
    ) -> Any:
        """A SQLAlchemy engine with this engine's connect args AND session statements."""
        from sqlalchemy import create_engine

        url, connect_args = self.engine_args(config, secret, timeout=timeout, read_only=read_only)
        engine = create_engine(url, connect_args=connect_args, **engine_kwargs)
        return self.prepare_engine(engine, read_only=read_only)

    def namespace(self, config: GenericSqlConfig) -> str:
        """The OpenLineage namespace: ``<scheme>://<host>:<port>`` (host case-folded)."""
        if self.namespace_authority is not None:
            host = self.namespace_authority(config)
        else:
            host = config.host.lower()
        if ":" in host:  # an IPv6 literal needs its brackets back in an authority
            host = f"[{host}]"
        if not self.namespace_includes_port:
            return f"{self.namespace_scheme}://{host}"
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
        # #1401: the fields that decide which server receives the secret.
        self.destination_fields: dict[str, tuple[str, ...]] = {"secret": spec.destination_fields}
        # Read by the connection service: may a connection of this type store no secret at all?
        # (`test` then decides per config — `requires_secret`.)
        self.secret_optional: bool = spec.config_model.secret_optional

    def validate_config(self, raw: dict[str, Any]) -> GenericSqlConfig:
        return self.spec.validate_config(raw)

    def credential_expiry(self, raw: dict[str, Any], secret: str, **_: Any) -> datetime | None:
        """When the stored credential stops working (#838) — only an engine whose secret states
        its own lifetime (a JWT) knows; a password never does."""
        if self.spec.credential_expiry is None:
            return None
        return self.spec.credential_expiry(self.validate_config(raw), secret)

    def test(self, raw: dict[str, Any], secret: str | None, **_: Any) -> None:
        """Open a session and run ``SELECT 1``; raise on any failure."""
        config = self.validate_config(raw)
        if secret is None and config.requires_secret():
            raise ValueError(
                f"a {self.spec.credential_noun} is required to test a "
                f"{self.spec.display_name} connection"
            )
        from sqlalchemy import text

        try:
            engine = self.spec.create_engine(config, secret)
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

    def __init__(self, spec: SqlEngineSpec, config: GenericSqlConfig, secret: str | None) -> None:
        self._spec = spec
        self._config = config
        self._secret = secret
        # The runner's ONE lazily-built engine (#427) for the non-GX SQL (monitors).
        self._engine = LazyEngine(self._build_engine)

    def _build_engine(self) -> Any:
        # pool_pre_ping: the session can sit idle across a long GX validation.
        return self._spec.create_engine(
            self._config,
            self._secret,
            timeout=None,
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
        temp_types = self._spec.temp_table_types
        writable = [i for i, spec in enumerate(checks) if spec.expectation_type in temp_types]
        if not writable:
            return self._run_batch(
                table=table,
                schema=schema,
                checks=checks,
                index_columns=index_columns,
                value_signal_gate=value_signal_gate,
                read_only=True,
            )
        guarded = [i for i in range(len(checks)) if i not in set(writable)]
        by_position: dict[int, CheckOutcome] = {}
        success = True
        for positions, read_only in ((guarded, True), (writable, False)):
            if not positions:
                continue
            outcome = self._run_batch(
                table=table,
                schema=schema,
                checks=[checks[i] for i in positions],
                index_columns=index_columns,
                value_signal_gate=value_signal_gate,
                read_only=read_only,
            )
            success = success and outcome.success
            by_position.update(zip(positions, outcome.checks, strict=True))
        return SuiteOutcome(success=success, checks=[by_position[i] for i in range(len(checks))])

    def _run_batch(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None,
        value_signal_gate: ValueSignalGate | None,
        read_only: bool,
    ) -> SuiteOutcome:
        # GX lower-cases an unquoted schema name, which on an engine that resolves names exactly
        # as spelled retargets a mixed-case schema. So where the engine can, the SESSION is
        # scoped to the target's schema instead (the engine's default schema) and GX gets no
        # schema at all. Where it cannot (SQL Server), GX gets the schema — which its default
        # case-insensitive collations resolve whatever GX's casing.
        if self._spec.session_schema:
            scoped = self._spec.scoped_config(self._config, schema)
            gx_schema = scoped.default_schema if self._spec.gx_schema_with_session else None
        else:
            scoped = self._config
            gx_schema = schema or self._config.default_schema
        connections = GxConnectionSource(self._spec, scoped, self._secret, read_only=read_only)
        try:
            return self._evaluate(
                connections,
                scoped=scoped,
                gx_schema=gx_schema,
                table=table,
                checks=checks,
                index_columns=index_columns,
                value_signal_gate=value_signal_gate,
                read_only=read_only,
            )
        except BaseException as exc:
            # A propagating traceback keeps `_evaluate`'s locals — GX's engines — alive past
            # `close`; its finished frames are cleared so a failed run is collected like a
            # passing one.
            traceback.clear_frames(exc.__traceback__)
            raise
        finally:
            # After `_evaluate` has returned, so the GX objects holding these connections are
            # already garbage when `close` collects them (see there).
            connections.close()

    def _evaluate(
        self,
        connections: GxConnectionSource,
        *,
        scoped: GenericSqlConfig,
        gx_schema: str | None,
        table: str,
        checks: list[CheckSpec],
        index_columns: list[str] | None,
        value_signal_gate: ValueSignalGate | None,
        read_only: bool,
    ) -> SuiteOutcome:
        import great_expectations as gx

        from backend.app.datasources.gx_runner import run_expectations

        context = gx.get_context(mode="ephemeral")
        datasource: Any = None
        try:
            # GX tests the connection inside `add_*`, so a failure there must still close it.
            datasource = self._spec.gx_datasource(
                context,
                f"{self._spec.conn_type}-{table}",
                self._spec.url_string(scoped, self._secret),
                {
                    "connect_args": self._spec.session_connect_args(
                        scoped, self._secret, None, read_only=read_only
                    ),
                    "creator": connections.connect,
                    **self._spec.run_engine_options,
                },
            )
            if gx_schema is not None and gx_schema != gx_schema.lower():
                asset = _add_exact_schema_table_asset(datasource, table=table, schema=gx_schema)
            else:
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
            _release_gx_project(context)

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
    three runs, three idle PostgreSQL backends). Handing GX this source as its engines'
    `creator` fixes both halves: every connection runs the engine's session statements (which
    an event hook on the one engine GX exposes never reaches), and `close` shuts every
    connection the run opened, whichever engine pooled it.
    """

    def __init__(
        self,
        spec: SqlEngineSpec,
        config: GenericSqlConfig,
        secret: str | None,
        *,
        read_only: bool,
    ) -> None:
        from sqlalchemy.pool import NullPool

        self._source = spec.create_engine(
            config, secret, timeout=None, read_only=read_only, poolclass=NullPool
        )
        self._opened: list[Any] = []

    def connect(self) -> Any:
        proxied = self._source.raw_connection()
        # The caller's pool owns it from here; NullPool would close it on check-in otherwise.
        proxied.detach()
        connection = proxied.dbapi_connection
        self._opened.append(connection)
        return connection

    def close(self) -> None:
        # GX keeps each engine's connection checked out for the engine's lifetime, and its
        # engines sit in reference cycles. Collected AFTER these connections were closed, their
        # pools roll the dead connections back, a strict driver (pyodbc) raises, and SQLAlchemy
        # logs it at ERROR although nothing failed (#2141). Collected first, the rollback runs
        # on an open connection and succeeds; the loop below still closes every connection.
        gc.collect()
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


def _add_exact_schema_table_asset(datasource: Any, *, table: str, schema: str) -> Any:
    """A GX table asset on a mixed-case ``schema``, resolved exactly as spelled (#2137).

    Only engines that cannot scope a session to a schema (SQL Server) hand GX the schema at all.
    GX lower-cases an unquoted schema — on a case-sensitive catalog (a Fabric Warehouse's) `Sales`
    then resolves as `sales` — so the schema goes in as a quoted name. GX's own asset check
    compares the schema against the server's schema names LOWER-CASED, which refuses an exactly
    cased one on every catalog, so that comparison is skipped; its query half is kept, so a
    missing table fails here with GX's own message, as it does for any other target.
    """
    import sqlalchemy as sa
    from great_expectations.datasource.fluent.interfaces import TestConnectionError
    from great_expectations.datasource.fluent.sql_datasource import TableAsset
    from sqlalchemy.sql.elements import quoted_name

    class _ExactSchemaTableAsset(TableAsset):  # type: ignore[misc]
        def test_connection(self) -> None:
            try:
                with self.datasource.get_engine().connect() as connection:
                    target = sa.table(self.table_name, schema=self.schema_name)
                    connection.execute(sa.select(1, target).limit(1))
            except Exception as query_error:
                # An error message, not a query.
                raise TestConnectionError(
                    f"Attempt to connect to table: {self.qualified_name} failed because the test "  # noqa: S608  # nosec B608
                    "query failed. Ensure the table exists and the user has access to select "
                    f"data from the table: {query_error}"
                ) from query_error

    if not is_sql_identifier(schema):
        raise ValueError(f"invalid schema identifier: {schema[:128]!r}")
    asset = _ExactSchemaTableAsset(
        name=table, table_name=gx_table_name(table), schema_name=quoted_name(schema, quote=True)
    )
    datasource._add_asset(asset)
    return asset


def _release_gx_project(context: Any) -> None:
    """Drop GX's process-global reference to this run's context, if it is still the current one.

    `gx.get_context` stores the context as GX's "project" until the NEXT run replaces it, so a
    run's context — and the engines holding its connections — outlived the run and was collected
    during a later run, after this run had closed those connections: the strict-driver ERROR noise
    of #2141, one run late. A context another run has since installed is left alone.
    """
    from great_expectations.data_context.data_context.context_factory import project_manager

    if getattr(project_manager, "_ProjectManager__project", None) is context:
        project_manager.set_project(None)


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
    validated = spec.validate_config(config)
    if not secret_ref:
        if validated.requires_secret():
            raise ValueError(f"{spec.display_name} connection requires secret_ref for the password")
        return GenericSqlCheckRunner(spec, validated, None)
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
    return [(s, t) for s, t, _ in typed_table_rows(spec, conn, schema=schema, limit=limit)]


def typed_table_rows(
    spec: SqlEngineSpec, conn: Any, *, schema: str | None = None, limit: int | None = None
) -> list[tuple[str, str, str]]:
    """:func:`table_rows` plus each relation's normalized :func:`sql.object_type`."""
    from sqlalchemy import text

    rows = conn.execute(
        text(spec.catalog.tables_sql), {"schema": schema, "lim": _limit(limit)}
    ).all()
    return [(str(s), str(t), object_type(kind)) for s, t, kind in rows if s and t]


# "No limit" as a bind value every engine accepts: Trino refuses an ORDER BY … LIMIT above
# 2^31 - 1 (live-found), so the largest 32-bit signed integer rather than anything wider.
_NO_LIMIT = 2**31 - 1


def _limit(limit: int | None) -> int:
    return _NO_LIMIT if limit is None else int(limit)
