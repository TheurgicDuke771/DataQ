"""Google BigQuery on the generic SQL base (#1681) — config, URL, client and catalog handling.

The catalog tests hand the code the library's OWN objects (`SchemaField`, `TableListItem`,
`DatasetListItem` built from the REST shape), never a hand-made stand-in, so a field the client
names differently from our model fails here; the emulator lane checks the same against a server.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.datasources import registry
from backend.app.datasources.base import TargetShapeError
from backend.app.datasources.bigquery import (
    BIGQUERY,
    DEFAULT_MAXIMUM_BYTES_BILLED,
    RETRY_DEADLINE,
    SCOPE,
    BigQueryDatasetRequiredError,
)
from backend.app.datasources.gcs import GcsKeyInvalidError
from backend.app.datasources.generic_sql import (
    GenericSqlConnectionAdapter,
    KnownDatasourceLimitationError,
    explained,
    schema_names,
    typed_table_rows,
)
from backend.app.datasources.sql_engines import SQL_ENGINES, authenticates_without_secret
from backend.app.services.asset_identity import resolve_asset_identity
from backend.app.services.failure_classifier import safe_failure_reason

_PROJECT = "acme-analytics"


def _key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    return json.dumps(
        {
            "type": "service_account",
            "project_id": _PROJECT,
            "private_key": pem,
            "client_email": f"dq@{_PROJECT}.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@pytest.fixture(scope="module")
def key() -> str:
    return _key()


def _config(**overrides: Any) -> Any:
    raw: dict[str, Any] = {"project_id": _PROJECT, "schema": "sales"}
    raw.update(overrides)
    return BIGQUERY.validate_config(raw)


def test_bigquery_is_a_registered_generic_sql_engine() -> None:
    assert SQL_ENGINES["bigquery"] is BIGQUERY
    assert not authenticates_without_secret("bigquery", {"project_id": _PROJECT})


def test_the_host_is_google_s_unless_an_endpoint_is_set() -> None:
    assert _config().host == "bigquery.googleapis.com"
    emulated = _config(endpoint_url="http://127.0.0.1:9050/")
    assert (emulated.host, emulated.endpoint_url) == ("127.0.0.1", "http://127.0.0.1:9050")


def test_the_safe_defaults() -> None:
    config = _config()
    assert config.maximum_bytes_billed == DEFAULT_MAXIMUM_BYTES_BILLED == 10 * 1024**3
    assert (config.location, config.user, config.endpoint_url) == (None, None, None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": "Acme"},
        {"project_id": "example.com:acme-analytics"},
        {"project_id": "ab"},
        {"project_id": "acme analytics"},
        {"host": "evil.example.com"},
        {"endpoint_url": "http://127.0.0.1:9050", "host": "bigquery.googleapis.com"},
        {"endpoint_url": "ftp://emulator"},
        {"endpoint_url": "http://user:pass@emulator:9050"},
        {"port": 443},
        {"sslmode": "disable"},
        {"location": "the moon"},
        {"location": "US-CENTRAL1"},
        {"location": 7},
        {"endpoint_url": 123},
        {"endpoint_url": "http://"},
        {"maximum_bytes_billed": 1024},
        {"maximum_bytes_billed": 0},
        {"schema": "sales$20260101"},
        {"schema": "sa-les"},
        {"schema": "a\x00b"},
        {"unknown_field": "x"},
    ],
)
def test_an_unusable_config_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _config(**overrides)


@pytest.mark.parametrize(
    ("location", "stored"),
    [
        ("US", "US"),
        ("eu", "EU"),
        ("us-central1", "us-central1"),
        ("northamerica-northeast1", "northamerica-northeast1"),
        ("aws-us-east-1", "aws-us-east-1"),
        ("azure-eastus2", "azure-eastus2"),
    ],
)
def test_a_bigquery_location_is_accepted(location: str, stored: str) -> None:
    assert _config(location=location).location == stored


@pytest.mark.parametrize("blank", ["", "  ", None])
def test_a_cleared_location_or_dataset_means_unset(blank: Any) -> None:
    config = _config(location=blank, schema=blank)
    assert (config.location, config.schema_) == (None, None)


def test_dataset_and_table_names_keep_their_case() -> None:
    assert _config(schema="Sales").default_schema == "Sales"
    resolved = registry.resolve_target_shape("bigquery", {"table": "CustomerEvents"})
    assert resolved.table == "CustomerEvents"


def test_a_partition_decorator_is_refused_as_a_target() -> None:
    with pytest.raises(TargetShapeError, match="partition"):
        registry.resolve_target_shape("bigquery", {"table": "orders$20261019"})


def test_the_url_carries_project_and_dataset_but_never_the_key(key: str) -> None:
    url, connect_args = BIGQUERY.engine_args(_config(), key)
    assert url == f"bigquery://{_PROJECT}/sales?user_supplied_client=true"
    assert "private_key" not in url
    assert connect_args["prefer_bqstorage_client"] is False
    connect_args["client"].close()


def test_without_a_dataset_the_url_names_only_the_project(key: str) -> None:
    url, connect_args = BIGQUERY.engine_args(_config(schema=None), key)
    assert url == f"bigquery://{_PROJECT}?user_supplied_client=true"
    connect_args["client"].close()


def test_the_client_carries_the_byte_cap_dataset_location_and_scope(key: str) -> None:
    config = _config(location="EU", maximum_bytes_billed=50 * 1024**2)
    client = BIGQUERY.session_connect_args(config, key, 10)["client"]
    try:
        assert client.project == _PROJECT
        assert client.location == "EU"
        job_config = client.default_query_job_config
        assert job_config.maximum_bytes_billed == 50 * 1024**2
        assert (job_config.default_dataset.project, job_config.default_dataset.dataset_id) == (
            _PROJECT,
            "sales",
        )
        assert client._credentials.scopes == [SCOPE]
        assert client._connection.API_BASE_URL == "https://bigquery.googleapis.com"
    finally:
        client.close()


def test_an_emulator_endpoint_is_where_the_client_sends_requests(key: str) -> None:
    config = _config(endpoint_url="http://127.0.0.1:9050", schema=None)
    client = BIGQUERY.session_connect_args(config, key, None)["client"]
    try:
        assert client._connection.API_BASE_URL == "http://127.0.0.1:9050"
        assert client.default_query_job_config.default_dataset is None
    finally:
        client.close()


def test_a_run_scoped_to_another_dataset_defaults_its_queries_there(key: str) -> None:
    scoped = BIGQUERY.scoped_config(_config(), "Finance")
    client = BIGQUERY.session_connect_args(scoped, key, None)["client"]
    try:
        assert client.default_query_job_config.default_dataset.dataset_id == "Finance"
    finally:
        client.close()


def test_every_query_gives_up_retrying_in_minutes(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The library retries a failing query for 10/40 minutes by default; the DBAPI passes no
    retry of its own, so the client bounds it."""
    from google.cloud import bigquery

    seen: dict[str, Any] = {}

    def capture(self: Any, query: str, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "rows"

    monkeypatch.setattr(bigquery.Client, "query_and_wait", capture)
    client = BIGQUERY.session_connect_args(_config(), key, None)["client"]
    try:
        assert client.query_and_wait("SELECT 1", page_size=10) == "rows"
    finally:
        client.close()
    assert seen["retry"].timeout == RETRY_DEADLINE == seen["job_retry"].timeout
    assert seen["api_timeout"] == 60.0 and seen["page_size"] == 10


def test_no_key_is_refused_rather_than_falling_back_to_ambient_credentials() -> None:
    with pytest.raises(ValueError, match="service-account key file"):
        BIGQUERY.session_connect_args(_config(), None, 10)
    with pytest.raises(ValueError, match="service-account key file"):
        GenericSqlConnectionAdapter(BIGQUERY).test({"project_id": _PROJECT}, None)


@pytest.mark.parametrize(
    "secret",
    [
        "not json",
        "[]",
        '{"type": "authorized_user"}',
        '{"type": "service_account", "client_email": "a@b"}',
    ],
)
def test_a_secret_that_is_not_a_key_file_is_refused(secret: str) -> None:
    with pytest.raises(GcsKeyInvalidError):
        BIGQUERY.session_connect_args(_config(), secret, 10)


def test_an_unreadable_private_key_is_refused_without_echoing_it() -> None:
    material = "MIIEvQ-not-really-a-key"
    secret = json.dumps(
        {
            "type": "service_account",
            "client_email": "a@b.iam.gserviceaccount.com",
            "private_key": material,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    with pytest.raises(GcsKeyInvalidError) as exc:
        BIGQUERY.session_connect_args(_config(), secret, 10)
    assert material not in str(exc.value)


def test_moving_the_endpoint_counts_as_moving_the_credential() -> None:
    assert "endpoint_url" in registry.destination_fields("bigquery")["secret"]


def test_the_openlineage_identity_is_the_bigquery_convention() -> None:
    config = _config()
    assert BIGQUERY.namespace(config) == "bigquery"
    assert BIGQUERY.namespace(_config(endpoint_url="http://LocalHost:9050")) == (
        "bigquery://localhost:9050"
    )
    identity = resolve_asset_identity(
        "bigquery", {"project_id": _PROJECT, "schema": "sales"}, {"table": "Orders"}
    )
    assert (identity.namespace, identity.name) == ("bigquery", f"{_PROJECT}.sales.Orders")
    qualified = resolve_asset_identity(
        "bigquery", {"project_id": _PROJECT}, {"table": "orders", "schema": "Finance"}
    )
    assert qualified.name == f"{_PROJECT}.Finance.orders"


def test_an_unqualified_target_without_a_default_dataset_is_explained() -> None:
    config = _config(schema=None)
    with pytest.raises(BigQueryDatasetRequiredError) as exc:
        _ = config.default_schema
    assert isinstance(exc.value, ValueError)
    assert "dataset" in safe_failure_reason(exc.value)
    with pytest.raises(ValueError, match="dataset"):
        resolve_asset_identity("bigquery", {"project_id": _PROJECT}, {"table": "orders"})


def test_the_byte_cap_refusing_a_query_is_explained_not_called_a_permission_problem() -> None:
    config = _config(maximum_bytes_billed=20 * 1024**2)
    refused = RuntimeError(
        "(google.cloud.bigquery.dbapi.exceptions.DatabaseError) 403 POST https://bigquery"
        ".googleapis.com/bigquery/v2/projects/acme-analytics/queries: Query exceeded limit for "
        "bytes billed: 20971520. 41943040 or higher required."
    )
    reason = explained(BIGQUERY, config, refused)
    assert isinstance(reason, KnownDatasourceLimitationError)
    assert "20,971,520 bytes" in str(reason) and "not charged" in str(reason)
    other = RuntimeError("403 Access Denied: Table acme-analytics:sales.orders")
    assert explained(BIGQUERY, config, other) is other


@pytest.mark.parametrize(
    ("aggregate", "expected"),
    [
        ("mean", "AVG(CAST(`orders`.`amount` AS FLOAT64))"),
        ("stdev", "STDDEV_SAMP(CAST(`orders`.`amount` AS FLOAT64))"),
    ],
)
def test_aggregates_compile_to_googlesql(aggregate: str, expected: str) -> None:
    import sqlalchemy as sa
    from sqlalchemy_bigquery import BigQueryDialect

    from backend.app.datasources.aggregate_sql import aggregate_statement

    target = sa.table("orders", sa.column("amount"), schema="Sales")
    sql = str(
        aggregate_statement(target, aggregate, target.c.amount).compile(dialect=BigQueryDialect())
    )
    assert expected in sql
    assert "FROM `Sales`.`orders`" in sql


def test_the_median_is_refused_because_the_exact_one_runs_on_one_worker() -> None:
    import sqlalchemy as sa
    from sqlalchemy.exc import CompileError
    from sqlalchemy_bigquery import BigQueryDialect

    from backend.app.datasources.aggregate_sql import aggregate_statement
    from backend.app.services.check_service import (
        CheckConfigInvalidError,
        _validate_aggregate_check,
    )

    assert not BIGQUERY.exact_median
    target = sa.table("orders", sa.column("amount"))
    with pytest.raises(CompileError):
        aggregate_statement(target, "median", target.c.amount).compile(dialect=BigQueryDialect())
    with pytest.raises(CheckConfigInvalidError, match="Google BigQuery"):
        _validate_aggregate_check(
            {"aggregate": "median", "column": "amount", "min_value": 0},
            connection_type="bigquery",
            thresholds=(None, None, None),
        )


def test_without_a_default_dataset_the_shared_resolvers_ask_for_a_schema() -> None:
    """Profile, columns, drift, anomaly and comparison reads resolve the schema here: a
    connection with no default dataset gets the usual 422, not an unhandled error."""
    from backend.app.db.models import Connection
    from backend.app.services.profile_service import (
        ProfileIdentifierInvalidError,
        resolve_effective_schema,
    )

    connection = Connection(type="bigquery", config={"project_id": _PROJECT})
    with pytest.raises(ProfileIdentifierInvalidError):
        resolve_effective_schema(connection, None)
    assert resolve_effective_schema(connection, "Sales") == "Sales"


def test_a_run_on_an_unqualified_target_without_a_default_dataset_says_so(key: str) -> None:
    from backend.app.datasources.base import CheckSpec
    from backend.app.datasources.generic_sql import GenericSqlCheckRunner

    runner = GenericSqlCheckRunner(BIGQUERY, _config(schema=None), key)
    with pytest.raises(BigQueryDatasetRequiredError):
        runner.run_checks(
            table="orders",
            schema=None,
            checks=[CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0})],
        )
    with pytest.raises(BigQueryDatasetRequiredError):
        runner.run_monitors(table="orders", schema=None, monitors=[])


# ───────────────────────────── catalog (client API) ─────────────────────────────


class _FakeClient:
    """Answers the three catalog calls with the library's own objects, from the REST shape."""

    def __init__(self) -> None:
        from google.cloud import bigquery
        from google.cloud.bigquery.dataset import DatasetListItem
        from google.cloud.bigquery.table import TableListItem

        self.datasets = [
            DatasetListItem({"datasetReference": {"projectId": _PROJECT, "datasetId": name}})
            for name in ("retail", "Sales", "b4ef7581-3e43-4e45-a5b0-7dbcfa7f1549")
        ]
        self.tables = {
            "retail": [
                TableListItem(
                    {
                        "tableReference": {
                            "projectId": _PROJECT,
                            "datasetId": "retail",
                            "tableId": table_id,
                        },
                        "type": kind,
                    }
                )
                for table_id, kind in (
                    ("orders", "TABLE"),
                    ("big_orders", "VIEW"),
                    ("daily_mv", "MATERIALIZED_VIEW"),
                    ("raw_ext", "EXTERNAL"),
                )
            ],
            "Sales": [
                TableListItem(
                    {
                        "tableReference": {
                            "projectId": _PROJECT,
                            "datasetId": "Sales",
                            "tableId": "Daily",
                        },
                        "type": "TABLE",
                    }
                )
            ],
        }
        self.schema = [
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("tags", "STRING", mode="REPEATED"),
            bigquery.SchemaField(
                "address", "RECORD", fields=[bigquery.SchemaField("city", "STRING")]
            ),
            bigquery.SchemaField("where", "GEOGRAPHY"),
            bigquery.SchemaField("doc", "JSON"),
            bigquery.SchemaField("placed_at", "TIMESTAMP"),
        ]
        self.calls: list[tuple[str, str]] = []

    def list_datasets(self, project: str) -> list[Any]:
        self.calls.append(("list_datasets", project))
        return self.datasets

    def list_tables(self, dataset: str) -> list[Any]:
        from google.api_core.exceptions import NotFound

        self.calls.append(("list_tables", dataset))
        name = dataset.split(".", 1)[1]
        if name not in self.tables:
            raise NotFound(f"Not found: Dataset {dataset}")
        return self.tables[name]

    def get_table(self, ref: str) -> Any:
        from google.api_core.exceptions import NotFound

        self.calls.append(("get_table", ref))
        if not ref.endswith(".orders"):
            raise NotFound("Not found: Table")
        return SimpleNamespace(schema=self.schema)


def _conn(client: _FakeClient) -> Any:
    return SimpleNamespace(
        connection=SimpleNamespace(dbapi_connection=SimpleNamespace(_client=client)),
        dialect=SimpleNamespace(project_id=_PROJECT),
    )


def test_datasets_list_in_name_order_without_names_bigquery_cannot_have() -> None:
    client = _FakeClient()
    assert schema_names(BIGQUERY, _conn(client), limit=None) == ["Sales", "retail"]
    assert schema_names(BIGQUERY, _conn(client), limit=1) == ["Sales"]
    assert client.calls[0] == ("list_datasets", _PROJECT)


def test_tables_list_with_their_kind_one_dataset_or_all() -> None:
    client = _FakeClient()
    assert typed_table_rows(BIGQUERY, _conn(client), schema="retail", limit=None) == [
        ("retail", "big_orders", "view"),
        ("retail", "daily_mv", "materialized_view"),
        ("retail", "orders", "table"),
        ("retail", "raw_ext", "table"),
    ]
    every = typed_table_rows(BIGQUERY, _conn(client), limit=None)
    assert ("Sales", "Daily", "table") in every and len(every) == 5
    assert typed_table_rows(BIGQUERY, _conn(client), limit=2) == every[:2]
    assert ("list_tables", f"{_PROJECT}.retail") in client.calls


def test_columns_come_from_the_table_schema_and_a_missing_table_has_none() -> None:
    assert BIGQUERY.catalog.api is not None
    client = _FakeClient()
    assert BIGQUERY.catalog.api.columns(_conn(client), schema="retail", table="orders") == [
        ("id", "INT64"),
        ("tags", "ARRAY<STRING>"),
        ("address", "STRUCT<city STRING>"),
        ("where", "GEOGRAPHY"),
        ("doc", "JSON"),
        ("placed_at", "TIMESTAMP"),
    ]
    assert BIGQUERY.catalog.api.columns(_conn(client), schema="retail", table="nope") == []
    assert ("get_table", f"{_PROJECT}.retail.orders") in client.calls


def test_the_profiler_skips_aggregates_googlesql_does_not_define() -> None:
    assert BIGQUERY.column_caps is not None
    caps = BIGQUERY.column_caps(_conn(_FakeClient()), "retail", "orders")
    plain = {name for name, cap in caps.items() if cap.orderable and cap.groupable}
    assert plain == {"id", "placed_at"}
    assert not any(caps[name].orderable or caps[name].groupable for name in caps.keys() - plain)


def test_a_type_reads_the_same_whichever_name_the_api_reports_it_by() -> None:
    """The REST API reports legacy names (INTEGER, RECORD) or GoogleSQL ones (INT64, STRUCT)
    depending on how the table was made; a drift baseline must not see a change between them."""
    from google.cloud import bigquery

    def columns(*kinds: str) -> list[tuple[str, str]]:
        client = _FakeClient()
        client.schema = [
            (
                bigquery.SchemaField(f"c{i}", kind, fields=[bigquery.SchemaField("x", "STRING")])
                if kind in ("RECORD", "STRUCT")
                else bigquery.SchemaField(f"c{i}", kind)
            )
            for i, kind in enumerate(kinds)
        ]
        assert BIGQUERY.catalog.api is not None
        return BIGQUERY.catalog.api.columns(_conn(client), schema="retail", table="orders")

    legacy = columns("INTEGER", "FLOAT", "BOOLEAN", "RECORD", "DECIMAL", "BIGDECIMAL")
    standard = columns("INT64", "FLOAT64", "BOOL", "STRUCT", "NUMERIC", "BIGNUMERIC")
    assert legacy == standard
    assert [kind for _, kind in standard] == [
        "INT64",
        "FLOAT64",
        "BOOL",
        "STRUCT<x STRING>",
        "NUMERIC",
        "BIGNUMERIC",
    ]


def test_a_dataset_that_does_not_exist_lists_no_tables() -> None:
    assert typed_table_rows(BIGQUERY, _conn(_FakeClient()), schema="gone", limit=None) == []


def test_the_profiler_matches_a_column_however_the_caller_cased_it() -> None:
    """BigQuery column names resolve whatever their case, so `ADDRESS` is the STRUCT column."""
    from backend.app.db.models import Connection
    from backend.app.services.profile_service import _column_caps

    connection = Connection(type="bigquery", config={"project_id": _PROJECT})
    caps = _column_caps(
        connection,
        _conn(_FakeClient()),
        schema="retail",
        table="orders",
        columns=["ADDRESS", "Id", "missing"],
    )
    assert set(caps) == {"ADDRESS", "Id"}
    assert not caps["ADDRESS"].orderable and caps["Id"].orderable


def test_drift_sees_a_change_inside_a_struct_and_a_type_parameter() -> None:
    from google.cloud import bigquery

    def columns(*fields: Any) -> list[tuple[str, str]]:
        client = _FakeClient()
        client.schema = list(fields)
        assert BIGQUERY.catalog.api is not None
        return BIGQUERY.catalog.api.columns(_conn(client), schema="retail", table="orders")

    before = columns(
        bigquery.SchemaField("address", "RECORD", fields=[bigquery.SchemaField("city", "STRING")]),
        bigquery.SchemaField("code", "STRING", max_length=20),
        bigquery.SchemaField("amount", "NUMERIC", precision=10, scale=2),
        bigquery.SchemaField(
            "lines", "RECORD", mode="REPEATED", fields=[bigquery.SchemaField("sku", "INTEGER")]
        ),
    )
    assert before == [
        ("address", "STRUCT<city STRING>"),
        ("code", "STRING(20)"),
        ("amount", "NUMERIC(10, 2)"),
        ("lines", "ARRAY<STRUCT<sku INT64>>"),
    ]
    after = columns(
        bigquery.SchemaField("address", "STRUCT", fields=[bigquery.SchemaField("city", "INT64")]),
        bigquery.SchemaField("code", "STRING", max_length=40),
        bigquery.SchemaField("amount", "NUMERIC", precision=12, scale=2),
        bigquery.SchemaField(
            "lines", "RECORD", mode="REPEATED", fields=[bigquery.SchemaField("sku", "INT64")]
        ),
    )
    changed = {name for (name, kind), (_, was) in zip(after, before, strict=True) if kind != was}
    assert changed == {"address", "code", "amount"}


def test_listing_every_dataset_skips_empty_ones_without_losing_rows() -> None:
    from google.cloud.bigquery.dataset import DatasetListItem

    client = _FakeClient()
    client.datasets = [
        DatasetListItem({"datasetReference": {"projectId": _PROJECT, "datasetId": name}})
        for name in ("aa_empty", "ab_empty", "retail", "Sales")
    ]
    client.tables["aa_empty"] = []
    client.tables["ab_empty"] = []
    rows = typed_table_rows(BIGQUERY, _conn(client), limit=3)
    assert rows == [
        ("Sales", "Daily", "table"),
        ("retail", "big_orders", "view"),
        ("retail", "daily_mv", "materialized_view"),
    ]


def test_catalog_reads_are_bounded_like_queries(key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from google.cloud import bigquery

    seen: dict[str, dict[str, Any]] = {}
    for name in ("list_datasets", "list_tables", "get_table"):

        def capture(self: Any, *args: Any, _name: str = name, **kwargs: Any) -> list[Any]:
            seen[_name] = kwargs
            return []

        monkeypatch.setattr(bigquery.Client, name, capture)
    client = BIGQUERY.session_connect_args(_config(), key, None)["client"]
    try:
        client.list_datasets(_PROJECT)
        client.list_tables(f"{_PROJECT}.sales")
        client.get_table(f"{_PROJECT}.sales.orders")
    finally:
        client.close()
    for kwargs in seen.values():
        assert kwargs["timeout"] == 60.0 and kwargs["retry"].timeout == RETRY_DEADLINE
    assert set(seen) == {"list_datasets", "list_tables", "get_table"}
