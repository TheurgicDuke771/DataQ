"""Google BigQuery datasource (#1681) — a `SqlEngineSpec` on the generic SQL base (ADR 0045).

The driver is ``google-cloud-bigquery`` (Apache-2.0) under the ``bigquery`` SQLAlchemy dialect of
``sqlalchemy-bigquery`` (MIT), the dialect GX's BigQuery path uses. A connection pins one project,
which is also the project every query job runs (and is billed) in; a dataset is the schema, so a
target is ``dataset.table`` and its asset name ``project.dataset.table``.

BigQuery is a service, not a server DataQ opens a session on, which shapes this spec:

* **The credential is a service-account key file**, validated like the GCS connection's, and
  becomes a ``bigquery.Client`` handed to the dialect as a connect arg — never in the URL.
* **Every query is billed and capped.** The client's default job config carries
  ``maximum_bytes_billed``, so every query DataQ runs (checks, monitors, profiling, custom SQL,
  the connection test) fails *before* running, uncharged, when it would bill more. Catalog reads
  (datasets, tables, columns) are free API calls, not ``INFORMATION_SCHEMA`` queries.
* **No read-only session.** As on Athena, the guarantee a write cannot land is the credential:
  grant the service account ``roles/bigquery.dataViewer`` on the data and
  ``roles/bigquery.jobUser`` on the project, nothing more.
* **Names are case-sensitive** (datasets and tables) and resolved exactly as spelled; ``$`` is
  refused because ``table$20260101`` addresses a partition, not a table.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from backend.app.datasources.gcs import (
    PROJECT_ID,
    GcsKeyInvalidError,
    normalize_endpoint_url,
    parse_service_account_key,
)
from backend.app.datasources.generic_sql import (
    ColumnCaps,
    GenericSqlConfig,
    KnownDatasourceLimitationError,
    SqlCatalog,
    SqlEngineSpec,
)

#: The default cap on the bytes one query may bill: 10 GiB.
DEFAULT_MAXIMUM_BYTES_BILLED = 10 * 1024**3
#: BigQuery bills at least 10 MiB per table a query reads, so a lower cap fails every query.
MINIMUM_BYTES_BILLED = 10 * 1024**2

#: Seconds one API request, and all retries of a failing query, may take.
API_TIMEOUT = 60.0
RETRY_DEADLINE = 120.0

#: Running a query job needs the ``bigquery`` scope; the IAM roles are what keep it read-only.
SCOPE = "https://www.googleapis.com/auth/bigquery"

_GOOGLE_HOST = "bigquery.googleapis.com"
# A multi-region (US, EU) or a region (us-central1, europe-west4, northamerica-northeast1).
_LOCATION = re.compile(r"US|EU|[a-z]+-[a-z]+\d+")
# Every name a BigQuery dataset can have; anything else listed is not a dataset to offer.
_DATASET_NAME = re.compile(r"[A-Za-z0-9_]{1,1024}")


class BigQueryDatasetRequiredError(KnownDatasourceLimitationError, ValueError):
    """A target names no dataset and the connection sets no default one."""


class BigQueryConfig(GenericSqlConfig):
    """Non-secret BigQuery connection config. The secret is the service-account key file."""

    default_port: ClassVar[int] = 443
    max_identifier_length: ClassVar[int] = 1024

    # The project is the base's `database` — the level a connection pins.
    database: str = Field(alias="project_id")
    # A service account authenticates by its key file alone.
    user: str | None = None  # type: ignore[assignment]
    # Where query jobs run. Unset lets BigQuery infer it from the datasets a query reads.
    location: str | None = None
    # An emulator or BigQuery-compatible endpoint. Unset = Google's.
    endpoint_url: str | None = None
    maximum_bytes_billed: int = Field(default=DEFAULT_MAXIMUM_BYTES_BILLED, ge=MINIMUM_BYTES_BILLED)

    @model_validator(mode="before")
    @classmethod
    def _host_from_endpoint(cls, data: Any) -> Any:
        # The host is derived, never configured: it would otherwise disagree with where the
        # client actually sends the bearer.
        if not isinstance(data, dict):
            return data
        endpoint = normalize_endpoint_url(data.get("endpoint_url"))
        host = _GOOGLE_HOST if endpoint is None else urlsplit(endpoint).hostname or ""
        configured = data.get("host")
        if configured is not None and configured != host:
            raise ValueError("a BigQuery connection's host comes from its endpoint_url")
        return {**data, "endpoint_url": endpoint, "host": host}

    @field_validator("database")
    @classmethod
    def _project_id(cls, value: str) -> str:
        value = value.strip()
        if not PROJECT_ID.fullmatch(value):
            raise ValueError(
                "project_id must be a Google Cloud project ID (6-30 lower-case letters, digits "
                "or dashes, starting with a letter)"
            )
        return value

    @field_validator("location", mode="before")
    @classmethod
    def _location(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str) or not _LOCATION.fullmatch(value.strip()):
            raise ValueError("location must be a BigQuery location such as US, EU or us-central1")
        return value.strip()

    @field_validator("port")
    @classmethod
    def _no_port(cls, value: int | None) -> int | None:
        if value is not None:
            raise ValueError("a BigQuery connection has no port (set endpoint_url instead)")
        return value

    @field_validator("sslmode")
    @classmethod
    def _no_sslmode(cls, value: str) -> str:
        if value != "require":
            raise ValueError(
                "sslmode does not apply to BigQuery: the transport follows endpoint_url"
            )
        return value

    @classmethod
    def identifier_problem(cls, value: str) -> str | None:
        if "$" in value:
            return "must not contain '$' (in BigQuery it addresses a partition, not a table)"
        return super().identifier_problem(value)

    def engine_default_schema(self) -> str:
        raise BigQueryDatasetRequiredError(
            "this BigQuery connection sets no default dataset, so the target must name its "
            "dataset (schema)"
        )

    def url_host(self) -> str:
        # The dialect reads the project from the URL's host.
        return self.database

    def url_port(self) -> None:
        return None

    def url_database(self) -> str | None:
        # The dialect reads the default dataset from the URL's database.
        return self.schema_

    def url_query(self) -> dict[str, str]:
        # The client — credential, location, byte cap — comes from `_auth_connect_args`.
        return {"user_supplied_client": "true"}


def bigquery_client(config: BigQueryConfig, secret: str | None) -> Any:
    """The ONE place a BigQuery connection's credential becomes a ``bigquery.Client`` — carrying
    the byte cap and the default dataset on its default job config."""
    if not secret:
        raise ValueError("a BigQuery connection needs its service-account key file")
    from google.api_core.client_options import ClientOptions
    from google.cloud import bigquery
    from google.oauth2 import service_account

    info = parse_service_account_key(secret)
    try:
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
    except (ValueError, TypeError) as exc:
        # google-auth's own message can quote the material it could not parse.
        raise GcsKeyInvalidError(
            "the service-account key file's private_key could not be read"
        ) from exc
    job_config = bigquery.QueryJobConfig(maximum_bytes_billed=config.maximum_bytes_billed)
    if config.schema_ is not None:
        job_config.default_dataset = bigquery.DatasetReference(config.database, config.schema_)
    options = (
        None if config.endpoint_url is None else ClientOptions(api_endpoint=config.endpoint_url)
    )
    return _bounded_client_class()(
        project=config.database,
        credentials=credentials,
        location=config.location,
        client_options=options,
        default_query_job_config=job_config,
    )


def _bounded_client_class() -> Any:
    """A ``bigquery.Client`` whose query retries give up in minutes, not the library's default
    10 (HTTP) and 40 (job) minutes — an unreachable endpoint would otherwise hold a connection
    test, or every check of a run, that long. The DBAPI calls ``query_and_wait`` with no retry
    of its own, so this is the one place to bound it."""
    from google.cloud import bigquery
    from google.cloud.bigquery.retry import DEFAULT_JOB_RETRY, DEFAULT_RETRY

    class _BoundedClient(bigquery.Client):  # type: ignore[misc]
        def query_and_wait(self, query: str, **kwargs: Any) -> Any:
            kwargs.setdefault("api_timeout", API_TIMEOUT)
            kwargs.setdefault("retry", DEFAULT_RETRY.with_timeout(RETRY_DEADLINE))
            kwargs.setdefault("job_retry", DEFAULT_JOB_RETRY.with_timeout(RETRY_DEADLINE))
            return super().query_and_wait(query, **kwargs)

    return _BoundedClient


def _connect_args(
    config: BigQueryConfig, timeout: int | None, *, read_only: bool = True
) -> dict[str, Any]:
    # `read_only` cannot be honoured: BigQuery has no read-only session (module docstring).
    del config, timeout, read_only
    # The Storage Read API client is not installed; results page over REST.
    return {"prefer_bqstorage_client": False}


def _auth_connect_args(config: BigQueryConfig, secret: str | None) -> dict[str, Any]:
    return {"client": bigquery_client(config, secret)}


def _add_gx_datasource(
    context: Any, name: str, connection_string: str, engine_kwargs: dict[str, Any]
) -> Any:
    return context.data_sources.add_bigquery(
        name=name, connection_string=connection_string, kwargs=engine_kwargs
    )


def _namespace(config: BigQueryConfig) -> str:
    """OpenLineage's BigQuery namespace is the bare ``bigquery``; an emulator keeps its own."""
    if config.endpoint_url is None:
        return "bigquery"
    return f"bigquery://{urlsplit(config.endpoint_url).netloc.lower()}"


def _client(conn: Any) -> Any:
    """The ``bigquery.Client`` behind a SQLAlchemy connection (the dialect reads it the same
    way)."""
    return conn.connection.dbapi_connection._client


def _project(conn: Any) -> str:
    return str(conn.dialect.project_id)


def _field_type(field: Any) -> str:
    kind = str(field.field_type)
    return f"ARRAY<{kind}>" if field.mode == "REPEATED" else kind


class _BigQueryCatalog:
    """Datasets, tables and columns from the client API — free, and identical on an emulator."""

    def schema_names(self, conn: Any, *, limit: int) -> list[str]:
        listed = _client(conn).list_datasets(_project(conn))
        return sorted(d.dataset_id for d in listed if _DATASET_NAME.fullmatch(d.dataset_id))[:limit]

    def table_rows(
        self, conn: Any, *, schema: str | None, limit: int
    ) -> list[tuple[str, str, str]]:
        client = _client(conn)
        datasets = [schema] if schema is not None else self.schema_names(conn, limit=limit)
        rows: list[tuple[str, str, str]] = []
        for dataset in datasets:
            tables = client.list_tables(f"{_project(conn)}.{dataset}")
            rows.extend((dataset, t.table_id, str(t.table_type)) for t in tables)
        return sorted(rows)[:limit]

    def columns(self, conn: Any, *, schema: str, table: str) -> list[tuple[str, str]]:
        """``[]`` for a table that does not exist."""
        from google.api_core.exceptions import NotFound

        try:
            fields = _client(conn).get_table(f"{_project(conn)}.{schema}.{table}").schema
        except NotFound:
            return []
        return [(str(field.name), _field_type(field)) for field in fields]


_CATALOG = _BigQueryCatalog()

# MIN/MAX are undefined over these; equality (COUNT DISTINCT, GROUP BY) too.
_UNAGGREGATABLE_TYPES = frozenset({"RECORD", "STRUCT", "GEOGRAPHY", "JSON", "RANGE"})


def _column_caps(conn: Any, schema: str, table: str) -> dict[str, ColumnCaps]:
    caps: dict[str, ColumnCaps] = {}
    for field in _client(conn).get_table(f"{_project(conn)}.{schema}.{table}").schema:
        plain = field.mode != "REPEATED" and str(field.field_type) not in _UNAGGREGATABLE_TYPES
        caps[str(field.name)] = ColumnCaps(orderable=plain, groupable=plain)
    return caps


BIGQUERY = SqlEngineSpec(
    conn_type="bigquery",
    display_name="Google BigQuery",
    config_model=BigQueryConfig,
    drivername="bigquery",
    gx_datasource=_add_gx_datasource,
    connect_args=_connect_args,
    auth_connect_args=_auth_connect_args,
    namespace_scheme="bigquery",
    namespace_override=_namespace,
    database_in_name=True,
    # The bearer the key file yields is presented to the endpoint.
    destination_fields=("endpoint_url", "host"),
    credential_noun="service-account key file",
    catalog=SqlCatalog(api=_CATALOG),
    column_caps=_column_caps,
    llm_dialect="BigQuery GoogleSQL (backtick-quoted identifiers)",
)
