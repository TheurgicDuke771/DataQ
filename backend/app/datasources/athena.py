"""Amazon Athena datasource (#2131) — a `SqlEngineSpec` on the generic SQL base (ADR 0045).

The driver is ``pyathena`` (MIT) and its ``awsathena+rest`` SQLAlchemy dialect. A connection pins
one data catalog (default ``awsdatacatalog``, the Glue catalog) in one region; a Glue database is
the schema, so a target is ``database.table`` and its asset name ``catalog.database.table``.

Athena is not a server DataQ opens a session on, which shapes this spec:

* **The endpoint is the region.** There is no host or user to configure: the endpoint is
  ``athena.<region>.amazonaws.com`` and the credential is an IAM access key (the key ID is the
  config's non-secret half, the secret access key is the stored secret), sent as connect args and
  never in the URL.
* **Every query is billed.** Athena charges per byte scanned (with a per-query minimum), and a
  check or monitor is a query — the docs say so.
* **No read-only session.** As on Trino, the guarantee a write cannot land is the credential: give
  DataQ an IAM principal allowed to read only (Athena, Glue read, S3 read on the data, and write
  on the query-results location alone).
* **Names are lower case.** The Glue catalog folds every identifier.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import Field, ValidationInfo, field_validator, model_validator

from backend.app.datasources.generic_sql import GenericSqlConfig, SqlCatalog, SqlEngineSpec
from backend.app.datasources.trino import _column_caps

_REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-\d")
_S3_URI = re.compile(r"s3://[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9](/[^\s@?#]*)?")
_WORK_GROUP = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _endpoint(region: str) -> str:
    return f"athena.{region}.amazonaws.com"


class AthenaConfig(GenericSqlConfig):
    """Non-secret Athena connection config. The secret is the IAM secret access key."""

    default_port: ClassVar[int] = 443
    # Glue allows 255; every name must also be lower case (the catalog folds it).
    max_identifier_length: ClassVar[int] = 255
    names_are_lower_case: ClassVar[bool] = True

    region: str
    work_group: str = "primary"
    # Where Athena writes query results. Optional when the workgroup enforces its own location.
    s3_staging_dir: str | None = None
    # The data catalog is the base's `database` — the level a connection pins.
    database: str = Field(default="awsdatacatalog", alias="catalog")
    # The IAM access key ID is the base's `user`: the non-secret half of the credential.
    user: str = Field(alias="access_key_id")

    @model_validator(mode="before")
    @classmethod
    def _endpoint_from_region(cls, data: Any) -> Any:
        # The host is derived, never configured: accepting one would let a config point the
        # credential at any endpoint while the region field said otherwise.
        if not isinstance(data, dict):
            return data
        region = data.get("region")
        if not isinstance(region, str) or not _REGION.fullmatch(region.strip()):
            raise ValueError("region must be an AWS region such as us-east-2")
        region = region.strip()
        if region.startswith("cn-"):
            raise ValueError(
                "AWS China regions (endpoint amazonaws.com.cn) are not supported for Athena"
            )
        host = data.get("host")
        if host is not None and host != _endpoint(region):
            raise ValueError("an Athena connection's endpoint comes from its region")
        return {**data, "region": region, "host": _endpoint(region)}

    @field_validator("work_group", "database", mode="before")
    @classmethod
    def _blank_is_the_default(cls, value: Any, info: ValidationInfo) -> Any:
        # A cleared optional form field arrives as "" — it means "use the default".
        if value is None or (isinstance(value, str) and not value.strip()):
            assert info.field_name is not None  # nosec B101
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("database", mode="before")
    @classmethod
    def _catalog_is_case_insensitive(cls, value: Any) -> Any:
        # Athena resolves a catalog name case-insensitively and lists it in lower case, so the
        # console's "AwsDataCatalog" is accepted and stored the way the catalog reports it.
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("database")
    @classmethod
    def _catalog_identifier(cls, value: str) -> str:
        cls._check_identifier(value, "catalog")
        return value

    @field_validator("work_group")
    @classmethod
    def _plain_work_group(cls, value: str) -> str:
        if not _WORK_GROUP.fullmatch(value):
            raise ValueError("work_group must be 1-128 letters, digits, '.', '_' or '-'")
        return value

    @field_validator("s3_staging_dir", mode="before")
    @classmethod
    def _staging_dir(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str) or not _S3_URI.fullmatch(value.strip()):
            raise ValueError(
                "s3_staging_dir must be an s3://bucket/prefix/ URI (no credentials or query)"
            )
        staging = value.strip()
        return staging if staging.endswith("/") else staging + "/"

    @field_validator("user")
    @classmethod
    def _access_key_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z0-9]{16,128}", value):
            raise ValueError(
                "access_key_id must be an AWS access key ID (upper-case letters and digits)"
            )
        return value

    def engine_default_schema(self) -> str:
        return "default"

    def url_database(self) -> str:
        # The dialect reads the Glue database (the session's default schema) from the URL's
        # database, and derives the region from the host.
        return self.default_schema

    def url_query(self) -> dict[str, str]:
        query = {"catalog_name": self.database, "work_group": self.work_group}
        if self.s3_staging_dir is not None:
            query["s3_staging_dir"] = self.s3_staging_dir
        return query


def _connect_args(
    config: AthenaConfig, timeout: int | None, *, read_only: bool = True
) -> dict[str, Any]:
    # `read_only` cannot be honoured: Athena has no read-only session (module docstring).
    del read_only
    if timeout is None:
        return {}
    from botocore.config import Config

    return {"config": Config(connect_timeout=timeout, retries={"max_attempts": 3})}


def _auth_connect_args(config: AthenaConfig, secret: str | None) -> dict[str, Any]:
    """The IAM key pair as driver args — never in the URL, whose password slot pyathena would
    otherwise read the secret from."""
    if not secret:
        raise ValueError("an Athena connection needs its IAM secret access key")
    return {"aws_access_key_id": config.user, "aws_secret_access_key": secret}


def _add_gx_datasource(
    context: Any, name: str, connection_string: str, engine_kwargs: dict[str, Any]
) -> Any:
    return context.data_sources.add_sql(
        name=name, connection_string=connection_string, kwargs=engine_kwargs
    )


ATHENA = SqlEngineSpec(
    conn_type="athena",
    display_name="Amazon Athena",
    config_model=AthenaConfig,
    drivername="awsathena+rest",
    gx_datasource=_add_gx_datasource,
    connect_args=_connect_args,
    auth_connect_args=_auth_connect_args,
    namespace_scheme="awsathena",
    namespace_includes_port=False,
    database_in_name=True,
    # Query results are written to the staging location and the workgroup can override it: both
    # decide where data read with the stored credential lands.
    destination_fields=("host", "region", "work_group", "s3_staging_dir"),
    credential_noun="secret access key",
    catalog=SqlCatalog(
        # Scoped to the session catalog. Athena's information_schema lists what the IAM principal
        # may see in Glue; each listing is itself a (minimum-billed) query.
        schemas_sql=(
            "SELECT schema_name FROM information_schema.schemata"
            " WHERE schema_name <> 'information_schema'"
            " ORDER BY schema_name LIMIT :lim"
        ),
        tables_sql=(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables"
            " WHERE table_schema <> 'information_schema'"
            " AND table_type IN ('BASE TABLE', 'VIEW')"
            " AND (:schema IS NULL OR table_schema = :schema)"
            " ORDER BY table_schema, table_name LIMIT :lim"
        ),
    ),
    column_caps=_column_caps,
    exact_median=False,
)
