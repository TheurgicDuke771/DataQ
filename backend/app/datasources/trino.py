"""Trino datasource (#1685) — one engine-generic adapter for any Trino (or Starburst) cluster,
and through it every store the cluster federates. A `SqlEngineSpec` on the generic SQL base
(ADR 0045).

The driver is the ``trino`` Python client (Apache-2.0) and its SQLAlchemy dialect. A connection
pins one Trino **catalog** — the analogue of a PostgreSQL database — so a target is
``schema.table`` inside it and its asset name is ``catalog.schema.table``.

Two engine facts shape this spec:

* **No read-only session.** Trino has no session- or transaction-level read-only switch a client
  can set, so — unlike PostgreSQL and MySQL — DataQ cannot make the SERVER refuse a write. Custom
  SQL still passes the ADR 0019 validator, but the guarantee a write cannot land rests on the
  credential: give DataQ a Trino user the cluster's access control allows to read only.
* **Names are lower case.** Trino folds every identifier (quoted or not) to lower case and its
  catalogs report them that way, so a configured or targeted name must be lower case — anything
  else would never join the enumerated asset (ADR 0040) or the schema-drift introspection.
"""

from __future__ import annotations

import hashlib
import os
import re
import ssl
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.datasources.generic_sql import (
    ColumnCaps,
    GenericSqlConfig,
    SqlCatalog,
    SqlEngineSpec,
)

#: How a request authenticates. ``password`` is HTTP basic (Trino's PASSWORD authenticator: a
#: file, LDAP, …), ``jwt`` a bearer token, ``none`` a cluster that trusts the user name it is sent.
TrinoAuth = Literal["password", "jwt", "none"]

#: Trino's TLS modes in the shared vocabulary. There is no ``require``/``verify-ca``: the client
#: always verifies the server certificate and its host name when TLS is on.
TrinoSslMode = Literal["verify-full", "disable"]

_MAX_CA_BUNDLE = 64 * 1024
_PEM_CERT = "-----BEGIN CERTIFICATE-----"


class TrinoConfig(GenericSqlConfig):
    """Non-secret Trino connection config. The secret is the password (``auth_type: password``)
    or the bearer token (``jwt``); ``none`` stores no secret.
    """

    default_port: ClassVar[int] = 443
    # Trino itself sets no limit; connectors do (PostgreSQL 63, Hive 128). 128 is the widest a
    # common connector accepts.
    max_identifier_length: ClassVar[int] = 128
    names_are_lower_case: ClassVar[bool] = True
    secret_optional: ClassVar[bool] = True

    # The catalog is the base's `database` — the level a connection pins.
    database: str = Field(alias="catalog")
    auth_type: TrinoAuth = "password"
    sslmode: TrinoSslMode = "verify-full"
    # A PEM bundle of the CA(s) the server certificate chains to — for a private CA. When set it
    # REPLACES the system trust store for this connection; verification is never turned off.
    ca_bundle: str | None = None

    @field_validator("sslmode", mode="before")
    @classmethod
    def _blank_sslmode_is_the_default(cls, value: Any) -> Any:
        return (
            "verify-full"
            if value is None or (isinstance(value, str) and not value.strip())
            else value
        )

    @field_validator("auth_type", mode="before")
    @classmethod
    def _blank_auth_is_the_default(cls, value: Any) -> Any:
        return (
            "password" if value is None or (isinstance(value, str) and not value.strip()) else value
        )

    @field_validator("database")
    @classmethod
    def _catalog_identifier(cls, value: str) -> str:
        cls._check_identifier(value, "catalog")
        return value

    @field_validator("ca_bundle", mode="before")
    @classmethod
    def _ca_bundle_parses(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str) or len(value) > _MAX_CA_BUNDLE or _PEM_CERT not in value:
            raise ValueError(
                "ca_bundle must be one or more PEM certificates (-----BEGIN CERTIFICATE-----), "
                f"at most {_MAX_CA_BUNDLE // 1024} KB"
            )
        try:
            ssl.create_default_context(cadata=value)
        except (ssl.SSLError, ValueError) as exc:
            raise ValueError("ca_bundle is not a readable PEM certificate bundle") from exc
        return value.strip() + "\n"

    @model_validator(mode="after")
    def _credentials_travel_over_tls(self) -> TrinoConfig:
        if self.auth_type != "none" and self.sslmode == "disable":
            raise ValueError(
                f"auth_type {self.auth_type!r} sends a credential with every request, so it "
                "needs TLS — set sslmode to verify-full (or auth_type none for a cluster "
                "without authentication)"
            )
        if self.ca_bundle is not None and self.sslmode == "disable":
            raise ValueError("ca_bundle applies to TLS only — sslmode is disable")
        return self

    @property
    def effective_port(self) -> int:
        if self.port is not None:
            return self.port
        return 8080 if self.sslmode == "disable" else self.default_port

    def engine_default_schema(self) -> str:
        return "default"

    def url_database(self) -> str:
        # The trino dialect reads `catalog/schema` from the URL's database: the session's
        # default catalog and schema, which is how a run is scoped to its target.
        return f"{self.database}/{self.default_schema}"

    def requires_secret(self) -> bool:
        return self.auth_type != "none"


def _ca_bundle_path(bundle: str) -> str:
    """A file holding ``bundle``, for the client's ``verify`` (requests takes a path, not PEM).

    Named by content hash in the temp dir and written atomically, so every session of every
    connection with the same bundle shares one file and a half-written one is never read. A CA
    certificate is public, so the file carries nothing secret.
    """
    digest = hashlib.sha256(bundle.encode()).hexdigest()[:32]
    path = Path(tempfile.gettempdir()) / f"dataq-trino-ca-{digest}.pem"
    # Re-checked, not trusted by name: a file at this predictable path with other contents
    # (planted, or truncated by a crash) is replaced rather than used as the trust anchor.
    if not path.exists() or path.read_text() != bundle:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dataq-trino-ca-")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(bundle)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return str(path)


def _connect_args(
    config: TrinoConfig, timeout: int | None, *, read_only: bool = True
) -> dict[str, Any]:
    # `read_only` cannot be honoured: Trino has no session read-only switch (module docstring).
    del read_only
    tls = config.sslmode != "disable"
    args: dict[str, Any] = {
        "http_scheme": "https" if tls else "http",
        # A `timestamp` (no zone) comes back naive in the SESSION zone, which the client sets to
        # the worker's local zone unless told otherwise; the freshness math reads naive as UTC.
        "timezone": "UTC",
        "source": "dataq",
    }
    if tls:
        args["verify"] = _ca_bundle_path(config.ca_bundle) if config.ca_bundle else True
    if timeout is not None:
        args["request_timeout"] = timeout
    return args


def _auth_connect_args(config: TrinoConfig, secret: str | None) -> dict[str, Any]:
    """The client's ``auth`` object. Never in the URL: a URL's query (where the dialect reads a
    JWT) is not masked the way its password is.
    """
    if config.auth_type == "none":
        return {}
    if not secret:
        raise ValueError(f"a Trino connection with auth_type {config.auth_type!r} needs its secret")
    from trino.auth import BasicAuthentication, JWTAuthentication

    if config.auth_type == "jwt":
        return {"auth": JWTAuthentication(secret)}
    return {"auth": BasicAuthentication(config.user, secret)}


def _credential_expiry(config: TrinoConfig, secret: str | None) -> datetime | None:
    """A JWT's ``exp`` — read, never verified (the cluster verifies it). ``None`` otherwise."""
    if config.auth_type != "jwt" or not secret:
        return None
    import jwt

    claims = jwt.decode(secret, options={"verify_signature": False})
    exp = claims.get("exp")
    return datetime.fromtimestamp(exp, tz=UTC) if isinstance(exp, int | float) else None


def _add_gx_datasource(
    context: Any, name: str, connection_string: str, engine_kwargs: dict[str, Any]
) -> Any:
    return context.data_sources.add_sql(
        name=name, connection_string=connection_string, kwargs=engine_kwargs
    )


# Types Trino cannot ORDER BY or MIN/MAX — anywhere in the type, since `array(json)` or a `row`
# with a `map` field inherits it (live-probed). The digests and sketches are not even comparable.
# Such a column is profiled as neither orderable NOR groupable: the profiler's top-values query
# breaks ties by ordering on the value, so a column Trino can group but not order (json, map)
# would fail the whole profile there. Its distinct count is reported unavailable instead.
_UNORDERABLE_TYPE = re.compile(
    r"\b(json|map|hyperloglog|p4hyperloglog|qdigest|tdigest|setdigest|geometry"
    r"|sphericalgeography|bingtile|kdbtree)\b"
)

_COLUMN_TYPES_SQL = (
    "SELECT column_name, data_type FROM information_schema.columns"
    " WHERE table_schema = :schema AND table_name = :table"
)


def _column_caps(conn: Any, schema: str, table: str) -> dict[str, ColumnCaps]:
    from sqlalchemy import text

    caps: dict[str, ColumnCaps] = {}
    for name, data_type in conn.execute(
        text(_COLUMN_TYPES_SQL), {"schema": schema, "table": table}
    ).all():
        aggregates = _UNORDERABLE_TYPE.search(str(data_type).lower()) is None
        caps[str(name)] = ColumnCaps(orderable=aggregates, groupable=aggregates)
    return caps


TRINO = SqlEngineSpec(
    conn_type="trino",
    display_name="Trino",
    config_model=TrinoConfig,
    drivername="trino",
    gx_datasource=_add_gx_datasource,
    connect_args=_connect_args,
    auth_connect_args=_auth_connect_args,
    credential_expiry=_credential_expiry,
    namespace_scheme="trino",
    database_in_name=True,
    # `auth_type` too: a stored password must not be re-sent as a bearer token (or vice versa).
    destination_fields=("host", "port", "sslmode", "ca_bundle", "auth_type"),
    catalog=SqlCatalog(
        # Scoped to the session catalog (the connection's). Trino filters information_schema by
        # the user's access control, so what is listed is what the credential may see.
        schemas_sql=(
            "SELECT schema_name FROM information_schema.schemata"
            " WHERE schema_name <> 'information_schema'"
            " ORDER BY schema_name LIMIT :lim"
        ),
        tables_sql=(
            "SELECT table_schema, table_name FROM information_schema.tables"
            " WHERE table_schema <> 'information_schema'"
            " AND table_type IN ('BASE TABLE', 'VIEW')"
            " AND (:schema IS NULL OR table_schema = :schema)"
            " ORDER BY table_schema, table_name LIMIT :lim"
        ),
    ),
    column_caps=_column_caps,
)
