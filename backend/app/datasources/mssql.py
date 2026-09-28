"""SQL Server / T-SQL datasource (#1679, ADR 0044) — one engine-generic adapter for anything that
speaks SQL Server's TDS protocol: SQL Server itself, Azure SQL Database, Synapse dedicated pools,
and Microsoft Fabric SQL endpoints. A `SqlEngineSpec` on the generic SQL base (ADR 0045).

Two driver lanes, chosen per connection (``driver``):

* **``python-tds``** (default) — ``python-tds`` + ``sqlalchemy-pytds``, both MIT and pure Python,
  shipped in the image. TLS is ALWAYS on: a CA bundle (certifi's, or the connection's own private
  CA) is always passed, and hostname verification always runs through DataQ's own validator
  (`mssql_tds`, which also carries the named-instance routing fix). Fabric SQL endpoints do not
  work on this lane yet (#2126) — a failure there is reported as that known limitation.
* **``odbc``** — ``mssql+pyodbc`` over Microsoft ODBC Driver 18, for an operator who installed the
  driver and ``pyodbc`` in their own derived image. DataQ never ships either (the driver is under
  a proprietary EULA — ADR 0044); when the lane is chosen but absent, the connection says so.

Auth: ``sql`` (``user`` + password secret) or ``entra_service_principal`` (``tenant_id`` +
``client_id`` + client-secret secret; an access token for ``database.windows.net``).

TDS has no session-level read-only setting, so unlike PostgreSQL and MySQL DataQ cannot make the
server refuse a write: the guards are the custom-SQL validator (ADR 0019, T-SQL-aware) and a
least-privileged login (``db_datareader``), which the docs require.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import field_validator, model_validator

from backend.app.datasources.generic_sql import (
    ColumnCaps,
    GenericSqlConfig,
    KnownDatasourceLimitationError,
    SqlCatalog,
    SqlEngineSpec,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import URL

#: The Entra ID resource every TDS endpoint (Azure SQL, Synapse, Fabric SQL) accepts tokens for.
ENTRA_SCOPE = "https://database.windows.net/.default"
DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"

# Fabric Warehouse / Lakehouse SQL analytics endpoint, and SQL database in Fabric.
_FABRIC_SUFFIXES = (".datawarehouse.fabric.microsoft.com", ".database.fabric.microsoft.com")
_GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
# A tenant is a GUID or a verified domain (contoso.onmicrosoft.com); it lands in the token
# endpoint's URL path, so nothing that could leave that path segment.
_TENANT_DOMAIN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})+"
)
_ODBC_DRIVER_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,64}")
_MAX_CA_PEM = 64 * 1024

AuthType = Literal["sql", "entra_service_principal"]
DriverLane = Literal["python-tds", "odbc"]


def is_fabric_host(host: str) -> bool:
    return host.lower().rstrip(".").endswith(_FABRIC_SUFFIXES)


class MssqlConfig(GenericSqlConfig):
    """Non-secret SQL Server connection config (the password / client secret comes from secrets)."""

    default_port: ClassVar[int] = 1433
    # sysname is nvarchar(128).
    max_identifier_length: ClassVar[int] = 128

    # Required for SQL auth only; an Entra service principal logs in as `client_id`.
    user: str | None = None  # type: ignore[assignment]
    auth_type: AuthType = "sql"
    tenant_id: str | None = None
    client_id: str | None = None
    driver: DriverLane = "python-tds"
    odbc_driver: str = DEFAULT_ODBC_DRIVER
    #: A private CA bundle (PEM) the server's certificate must chain to, instead of the public
    #: roots — python-tds lane only (the ODBC driver reads the OS trust store).
    ca_bundle: str | None = None

    #: TLS with certificate AND hostname verification is the only mode (ADR 0044 §3) — the base's
    #: `require` / `disable` are refused rather than silently ignored.
    sslmode: Literal["verify-full"] = "verify-full"

    @field_validator("sslmode", mode="before")
    @classmethod
    def _blank_sslmode_is_the_default(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and value.strip() in ("", "verify-full")):
            return "verify-full"
        raise ValueError(
            "a SQL Server connection always verifies the server certificate and hostname; "
            "sslmode can only be verify-full"
        )

    @field_validator("tenant_id", "client_id", "ca_bundle", "user", mode="before")
    @classmethod
    def _blank_optional_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("driver", "odbc_driver", "auth_type", mode="before")
    @classmethod
    def _blank_choice_is_the_default(cls, value: Any, info: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return {"driver": "python-tds", "odbc_driver": DEFAULT_ODBC_DRIVER}.get(
                info.field_name, "sql"
            )
        return value

    @field_validator("client_id")
    @classmethod
    def _client_is_a_guid(cls, value: str | None) -> str | None:
        if value is not None and not _GUID.fullmatch(value.strip()):
            raise ValueError("client_id must be the application (client) ID GUID")
        return None if value is None else value.strip()

    @field_validator("tenant_id")
    @classmethod
    def _tenant_is_a_guid_or_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        tenant = value.strip()
        if not (_GUID.fullmatch(tenant) or _TENANT_DOMAIN.fullmatch(tenant)):
            raise ValueError("tenant_id must be the directory (tenant) ID GUID or a tenant domain")
        return tenant

    @field_validator("odbc_driver")
    @classmethod
    def _odbc_driver_name(cls, value: str) -> str:
        if not _ODBC_DRIVER_NAME.fullmatch(value):
            raise ValueError("odbc_driver must be an installed ODBC driver's name")
        return value

    @field_validator("ca_bundle")
    @classmethod
    def _ca_is_pem_certificates(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from cryptography import x509

        if len(value) > _MAX_CA_PEM:
            raise ValueError("ca_bundle is too large for a CA bundle")
        try:
            certificates = x509.load_pem_x509_certificates(value.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            raise ValueError("ca_bundle must be one or more PEM certificates") from None
        if not certificates:
            raise ValueError("ca_bundle must be one or more PEM certificates")
        return value

    @model_validator(mode="after")
    def _auth_fields_match_the_mode(self) -> MssqlConfig:
        if self.auth_type == "sql":
            if self.user is None:
                raise ValueError("SQL authentication needs a user")
            if self.tenant_id is not None or self.client_id is not None:
                raise ValueError(
                    "tenant_id and client_id are for entra_service_principal auth, not sql"
                )
        else:
            if self.tenant_id is None or self.client_id is None:
                raise ValueError("Entra service-principal auth needs tenant_id and client_id")
            if self.user is not None:
                raise ValueError("a service principal logs in as its client_id — leave user empty")
        if self.driver == "odbc" and self.ca_bundle is not None:
            raise ValueError(
                "ca_bundle applies to the python-tds driver only; on the ODBC lane install "
                "the CA in the image's trust store"
            )
        return self

    def engine_default_schema(self) -> str:
        return "dbo"


# ───────────────────────────── TLS ─────────────────────────────

_ca_lock = threading.Lock()
_ca_dir: str | None = None


def _ca_file(config: MssqlConfig) -> str:
    """The CA bundle python-tds must verify against — never ``None``, which would turn TLS off.

    A private CA is written into a directory this process created (``mkdtemp``: mode 0700, a
    fresh name), never a predictable path in the shared temp dir, where another local user
    could plant a file of their own CA first and have it trusted.
    """
    global _ca_dir
    if config.ca_bundle is None:
        import certifi

        return str(certifi.where())
    pem = config.ca_bundle.encode("ascii")
    with _ca_lock:
        if _ca_dir is None:
            _ca_dir = tempfile.mkdtemp(prefix="dataq-mssql-ca-")
        path = os.path.join(_ca_dir, f"{hashlib.sha256(pem).hexdigest()[:32]}.pem")
        if not os.path.exists(path):
            # Keyed by content; the atomic rename means a reader never sees a partial file.
            fd, tmp = tempfile.mkstemp(dir=_ca_dir, suffix=".pem")
            with os.fdopen(fd, "wb") as handle:
                handle.write(pem)
            os.replace(tmp, path)
    return path


# ───────────────────────────── driver lanes ─────────────────────────────


def odbc_lane_problem(config: MssqlConfig) -> str | None:
    """Why the ODBC lane cannot run in this process, or ``None`` when it can."""
    fix = (
        "DataQ does not ship the Microsoft ODBC driver (its licence forbids redistribution in "
        "our image — ADR 0044). Build a derived worker/API image that installs "
        f"'{config.odbc_driver}' (msodbcsql18) and pyodbc, or set this connection's driver to "
        "python-tds."
    )
    try:
        import pyodbc
    except ImportError:
        return f"This connection uses the ODBC driver lane, but pyodbc is not installed. {fix}"
    if config.odbc_driver not in pyodbc.drivers():
        return (
            f"This connection uses the ODBC driver lane, but the ODBC driver "
            f"'{config.odbc_driver}' is not installed. {fix}"
        )
    return None


FABRIC_PYTDS_LIMITATION = (
    "Microsoft Fabric SQL endpoints cannot be reached with the default python-tds driver yet: "
    "Fabric rejects its routed login (a known incompatibility in that driver). Use the ODBC "
    "driver lane instead — install Microsoft ODBC Driver 18 and pyodbc in a derived DataQ image "
    "and set this connection's driver to odbc (see the SQL Server section of the datasources "
    "guide). Azure SQL Database, Synapse and SQL Server work on python-tds."
)

FABRIC_LOGIN_PREREQUISITES = (
    "Fabric refused the service principal's login. Check the two Fabric-side prerequisites: the "
    "tenant setting 'Service principals can use Fabric APIs' must be enabled for it, and it "
    "needs a workspace role (or item permission) on this warehouse or SQL endpoint."
)


#: Expectation types whose GX SQL Server metrics build `#temp` tables, which a Fabric SQL
#: endpoint refuses ("The query references an object that is not supported in distributed
#: processing mode", 15816 — live-verified on a Warehouse and a Lakehouse SQL endpoint).
FABRIC_TEMP_TABLE_TYPES = frozenset(
    {
        "expect_column_values_to_be_unique",
        "expect_compound_columns_to_be_unique",
        "expect_select_column_values_to_be_unique_within_record",
        "expect_column_pair_values_a_to_be_greater_than_b",
        "expect_column_pair_values_to_be_equal",
        "expect_column_pair_values_to_be_in_set",
        "expect_multicolumn_sum_to_equal",
    }
)
FABRIC_TEMP_TABLE_REASON = (
    "Great Expectations evaluates it on SQL Server through a temporary table, which a Microsoft "
    "Fabric SQL endpoint does not support. Use a custom-SQL check instead (for uniqueness: "
    "SELECT col FROM {batch} GROUP BY col HAVING COUNT(*) > 1)."
)


def _config_unsupported(config: GenericSqlConfig) -> tuple[frozenset[str], str]:
    assert isinstance(config, MssqlConfig)
    if is_fabric_host(config.host):
        return FABRIC_TEMP_TABLE_TYPES, FABRIC_TEMP_TABLE_REASON
    return frozenset(), ""


def _explain_failure(config: GenericSqlConfig, exc: BaseException) -> str | None:
    assert isinstance(config, MssqlConfig)
    if not is_fabric_host(config.host):
        return None
    if config.driver == "python-tds":
        return FABRIC_PYTDS_LIMITATION
    text = f"{exc} {exc.__cause__ or ''}".lower()
    if config.auth_type == "entra_service_principal" and (
        "18456" in text or "login failed" in text
    ):
        return FABRIC_LOGIN_PREREQUISITES
    return None


def _connect_args(config: MssqlConfig, timeout: int | None, **_: Any) -> dict[str, Any]:
    if config.driver == "odbc":
        # pyodbc's `timeout` is the login timeout; a run waits out a slow first login (a Fabric
        # SQL endpoint or a paused serverless database — live-found), a Test does not.
        return {"timeout": 60 if timeout is None else timeout}
    from backend.app.datasources import mssql_tds

    mssql_tds.install()
    return {
        # A cafile is what turns TLS on in python-tds; `validate_host` stays on (and runs
        # DataQ's own validator — see `mssql_tds`).
        "cafile": _ca_file(config),
        "validate_host": True,
        "appname": "dataq",
        # An Azure SQL serverless database resumes from auto-pause on the first login, which
        # takes up to a minute; a run waits for it, a Test Connection does not.
        "login_timeout": 60 if timeout is None else timeout,
    }


def _odbc_value(value: str) -> str:
    """An ODBC connection-string value, braced so `;` / `=` / `{` inside it stay literal."""
    return "{" + value.replace("}", "}}") + "}"


#: msodbcsql's pre-connect attribute for an Entra access token (`msodbcsql.h`).
SQL_COPT_SS_ACCESS_TOKEN = 1256


def odbc_access_token(token: str) -> bytes:
    """An access token in the shape msodbcsql expects: UTF-16-LE bytes behind a 4-byte
    little-endian length.
    """
    encoded = token.encode("utf-16-le")
    return struct.pack(f"<I{len(encoded)}s", len(encoded), encoded)


def _token_callable(config: MssqlConfig, secret: str) -> Any:
    from azure.identity import ClientSecretCredential

    assert config.tenant_id is not None and config.client_id is not None
    credential = ClientSecretCredential(config.tenant_id, config.client_id, secret)

    def _token() -> str:
        # python-tds calls this once per physical login; azure-identity caches the token and
        # refreshes it before expiry.
        return str(credential.get_token(ENTRA_SCOPE).token)

    return _token


@dataclass(frozen=True)
class MssqlEngineSpec(SqlEngineSpec):
    """The generic spec plus the two things a TDS connection does differently: which DBAPI
    lane it rides, and an Entra token in place of a password.
    """

    def url(self, config: GenericSqlConfig, secret: str | None) -> URL:
        from sqlalchemy.engine import URL

        assert isinstance(config, MssqlConfig)
        entra = config.auth_type == "entra_service_principal"
        if config.driver == "odbc":
            query = {
                "driver": config.odbc_driver,
                "Encrypt": "yes",
                "TrustServerCertificate": "no",
            }
            # A service principal logs in with a token DataQ mints (`engine_args`), exactly as
            # on the python-tds lane — not the driver's own `ActiveDirectoryServicePrincipal`,
            # which on a wrong client secret hangs until the login timeout (live-found) instead
            # of failing with Entra's own AADSTS error. So no credential rides this URL — and it
            # is spelled as a raw `odbc_connect` string, because SQLAlchemy adds
            # `Trusted_Connection=Yes` to a user-less pyodbc URL, which the driver refuses beside
            # an access token (FA005, live-found).
            if entra:
                odbc = ";".join(
                    f"{key}={_odbc_value(value)}"
                    for key, value in (
                        ("Driver", config.odbc_driver),
                        ("Server", f"{config.host},{config.effective_port}"),
                        ("Database", config.database),
                        ("Encrypt", "yes"),
                        ("TrustServerCertificate", "no"),
                    )
                )
                return URL.create("mssql+pyodbc", query={"odbc_connect": odbc})
            return URL.create(
                "mssql+pyodbc",
                username=config.user,
                password=secret,
                host=config.host,
                port=config.effective_port,
                database=config.database,
                query=query,
            )
        if entra:
            # The client secret never enters the URL (GX keeps a copy): python-tds gets a token.
            return URL.create(
                self.drivername,
                host=config.host,
                port=config.effective_port,
                database=config.database,
            )
        return super().url(config, secret)

    def engine_args(
        self, config: GenericSqlConfig, secret: str | None, **kwargs: Any
    ) -> tuple[str, dict[str, Any]]:
        assert isinstance(config, MssqlConfig)
        if config.driver == "odbc":
            problem = odbc_lane_problem(config)
            if problem is not None:
                raise KnownDatasourceLimitationError(problem)
        url, connect_args = super().engine_args(config, secret, **kwargs)
        if config.auth_type == "entra_service_principal":
            assert secret is not None  # `requires_secret`: every SQL Server mode has one
            token = _token_callable(config, secret)
            if config.driver == "python-tds":
                # Called once per physical login: every new connection gets a fresh token.
                connect_args = {**connect_args, "access_token_callable": token}
            else:
                # pyodbc takes the token as a pre-connect attribute, fixed for the engine's
                # life. DataQ's engines live for one run / test / profile, well inside a token's
                # lifetime; minting here also fails a dead secret fast, with Entra's AADSTS text.
                connect_args = {
                    **connect_args,
                    "attrs_before": {SQL_COPT_SS_ACCESS_TOKEN: odbc_access_token(token())},
                }
        return url, connect_args


def _add_gx_datasource(
    context: Any, name: str, connection_string: str, engine_kwargs: dict[str, Any]
) -> Any:
    # The generic builder — GX's own `add_sql_server` / `add_fabric` accept pyodbc URLs only.
    return context.data_sources.add_sql(
        name=name, connection_string=connection_string, kwargs=engine_kwargs
    )


# Types SQL Server has no MIN/MAX for, and the subset it cannot COUNT(DISTINCT)/GROUP BY either —
# each live-verified on Azure SQL (bit, xml, geography, geometry, ntext, image, json).
_UNORDERABLE_TYPES = frozenset(
    {"bit", "xml", "geography", "geometry", "text", "ntext", "image", "json", "vector"}
)
_UNGROUPABLE_TYPES = _UNORDERABLE_TYPES - {"bit"}

# `user_type_id` names a CLR type (geography) or an alias; `system_type_id` names what an alias
# is over — a column is only as aggregatable as the weaker of the two.
_COLUMN_TYPES_SQL = (
    "SELECT c.name, TYPE_NAME(c.user_type_id), TYPE_NAME(c.system_type_id) FROM sys.columns c"
    " WHERE c.object_id = OBJECT_ID(QUOTENAME(:schema) + N'.' + QUOTENAME(:table))"
)


def _column_caps(conn: Any, schema: str, table: str) -> dict[str, ColumnCaps]:
    from sqlalchemy import text

    caps: dict[str, ColumnCaps] = {}
    for name, user_type, system_type in conn.execute(
        text(_COLUMN_TYPES_SQL), {"schema": schema, "table": table}
    ).all():
        types = {str(user_type or "").lower(), str(system_type or "").lower()}
        caps[str(name)] = ColumnCaps(
            orderable=not types & _UNORDERABLE_TYPES,
            groupable=not types & _UNGROUPABLE_TYPES,
        )
    return caps


# INFORMATION_SCHEMA is metadata-visibility filtered; HAS_PERMS_BY_NAME narrows that to what the
# credential can actually SELECT. A schema is listed when it holds such a table, so the browser
# never offers the fixed-role schemas (db_datareader, …) every database carries.
_READABLE = (
    # `queryinsights` is Fabric's own query-history schema (live-found on a Fabric Warehouse and
    # Lakehouse SQL endpoint): system views, never a user's table.
    "TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA', 'queryinsights')"
    " AND HAS_PERMS_BY_NAME(QUOTENAME(TABLE_SCHEMA) + N'.' + QUOTENAME(TABLE_NAME),"
    " 'OBJECT', 'SELECT') = 1"
)

MSSQL = MssqlEngineSpec(
    conn_type="mssql",
    display_name="SQL Server",
    config_model=MssqlConfig,
    drivername="mssql+pytds",
    gx_datasource=_add_gx_datasource,
    connect_args=_connect_args,
    namespace_scheme="mssql",
    database_in_name=True,
    catalog=SqlCatalog(
        # T-SQL has no LIMIT: `TOP (:lim)` takes the same bound parameter.
        schemas_sql=(
            "SELECT DISTINCT TOP (:lim) TABLE_SCHEMA"  # noqa: S608  # nosec B608
            f" FROM INFORMATION_SCHEMA.TABLES WHERE {_READABLE}"
            " ORDER BY TABLE_SCHEMA"
        ),
        tables_sql=(
            "SELECT TOP (:lim) TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE"  # noqa: S608  # nosec B608
            f" FROM INFORMATION_SCHEMA.TABLES WHERE {_READABLE}"
            " AND (:schema IS NULL OR TABLE_SCHEMA = :schema)"
            " ORDER BY TABLE_SCHEMA, TABLE_NAME"
        ),
    ),
    column_caps=_column_caps,
    session_schema=False,
    # #1401: the password (or the token minted from the client secret) goes to host:port; the
    # client secret itself goes to tenant_id's token endpoint as client_id; auth_type decides
    # which kind of credential the one stored secret is; a private CA decides whose certificate
    # counts as that host; and the driver lane decides which client presents it.
    destination_fields=(
        "host",
        "port",
        "auth_type",
        "tenant_id",
        "client_id",
        "ca_bundle",
        "driver",
    ),
    credential_noun="password or client secret",
    # T-SQL has no regular-expression operator GX can translate to (live-verified: all four
    # error on every run on Azure SQL).
    unsupported_expectation_types=frozenset(
        {
            "expect_column_values_to_match_regex",
            "expect_column_values_to_not_match_regex",
            "expect_column_values_to_match_regex_list",
            "expect_column_values_to_not_match_regex_list",
        }
    ),
    config_unsupported_expectation_types=_config_unsupported,
    unsupported_reason=(
        "T-SQL has no regular-expression operator Great Expectations can translate to. Use a "
        "custom-SQL check with LIKE or PATINDEX instead."
    ),
    llm_dialect=(
        "T-SQL (SQL Server / Azure SQL / Fabric: TOP n instead of LIMIT, [bracket] or "
        '"double-quote" identifiers)'
    ),
    explain_failure=_explain_failure,
)
