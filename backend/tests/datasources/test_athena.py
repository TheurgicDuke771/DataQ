"""Amazon Athena on the generic SQL base (#2131) — config, URL and credential handling."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.datasources.athena import ATHENA
from backend.app.datasources.sql_engines import SQL_ENGINES

_KEY_ID = "AKIAEXAMPLEEXAMPLE12"
_SECRET = "not-a-real-secret"  # nosec B105


def _config(**overrides: Any) -> Any:
    raw = {"region": "us-east-2", "access_key_id": _KEY_ID, "schema": "sales"}
    raw.update(overrides)
    return ATHENA.validate_config(raw)


def test_athena_is_a_registered_generic_sql_engine() -> None:
    assert SQL_ENGINES["athena"] is ATHENA


def test_the_endpoint_is_derived_from_the_region() -> None:
    config = _config()
    assert config.host == "athena.us-east-2.amazonaws.com"
    assert config.effective_port == 443


@pytest.mark.parametrize(
    "overrides",
    [
        {"region": "not-a-region"},
        {"region": "us-east-2", "host": "evil.example.com"},
        {"s3_staging_dir": "https://bucket/prefix"},
        {"s3_staging_dir": "s3://user:pass@bucket/prefix"},
        {"work_group": "has spaces"},
        {"access_key_id": "lowercase-not-a-key"},
        {"schema": "Sales"},
        {"unknown_field": "x"},
    ],
)
def test_an_unusable_config_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _config(**overrides)


@pytest.mark.parametrize("blank", ["", "  ", None])
def test_a_cleared_workgroup_or_catalog_falls_back_to_the_default(blank: Any) -> None:
    config = _config(work_group=blank, catalog=blank)
    assert (config.work_group, config.database) == ("primary", "awsdatacatalog")


def test_a_china_region_is_refused_rather_than_sent_to_the_wrong_endpoint() -> None:
    with pytest.raises(ValidationError, match="China"):
        _config(region="cn-north-1")


def test_the_catalog_is_stored_as_the_catalog_reports_it() -> None:
    assert _config(catalog="AwsDataCatalog").database == "awsdatacatalog"
    assert _config().database == "awsdatacatalog"


def test_a_staging_dir_gains_its_trailing_slash() -> None:
    assert _config(s3_staging_dir="s3://bucket/results").s3_staging_dir == "s3://bucket/results/"


def test_the_secret_never_enters_the_url_and_travels_as_connect_args() -> None:
    config = _config(s3_staging_dir="s3://bucket/results/", work_group="analytics")
    url, connect_args = ATHENA.engine_args(config, _SECRET)
    assert _SECRET not in url
    assert url.startswith(f"awsathena+rest://{_KEY_ID}@athena.us-east-2.amazonaws.com:443/sales?")
    assert "work_group=analytics" in url and "catalog_name=awsdatacatalog" in url
    assert connect_args["aws_access_key_id"] == _KEY_ID
    assert connect_args["aws_secret_access_key"] == _SECRET


def test_no_secret_is_refused_rather_than_sent_empty() -> None:
    with pytest.raises(ValueError, match="secret access key"):
        ATHENA.session_connect_args(_config(), None, 10)


def test_a_run_scoped_to_another_database_connects_to_it() -> None:
    assert ATHENA.scoped_config(_config(), "finance").url_database() == "finance"


def test_the_openlineage_identity_follows_the_athena_convention() -> None:
    config = _config()
    assert ATHENA.namespace(config) == "awsathena://athena.us-east-2.amazonaws.com"
    assert ATHENA.asset_name(config, schema="sales", table="orders") == (
        "awsdatacatalog.sales.orders"
    )


def test_where_query_results_land_counts_as_moving_the_credential() -> None:
    assert {"region", "work_group", "s3_staging_dir"} <= set(ATHENA.destination_fields)


def test_regex_expectations_compile_to_regexp_like_on_athena() -> None:
    """GX has no Athena regex branch; every regex expectation errored live (#2131)."""
    import sqlalchemy as sa
    from great_expectations.expectations.metrics.column_map_metrics import (
        column_values_match_regex,
    )
    from pyathena.sqlalchemy.rest import AthenaRestDialect

    import backend.app.datasources.gx_metrics  # noqa: F401  (installs the override)

    dialect = AthenaRestDialect()  # type: ignore[no-untyped-call]
    column: sa.ColumnClause[Any] = sa.column("email")
    compile_regex = column_values_match_regex.get_dialect_regex_expression
    match = compile_regex(column, "@x$", dialect, True)
    miss = compile_regex(column, "@x$", dialect, False)
    assert "regexp_like" in str(match.compile(dialect=dialect)).lower()
    assert str(miss.compile(dialect=dialect)).lower().startswith("not")


def test_a_wrong_secret_key_is_categorised_as_permission() -> None:
    """Live wording for a wrong secret key on Athena (#2131) — S3 says SignatureDoesNotMatch."""
    from backend.app.services.failure_classifier import FailureCategory, classify_failure_category

    exc = RuntimeError(
        "(pyathena.error.DatabaseError) An error occurred (InvalidSignatureException) when calling "
        "the StartQueryExecution operation: The request signature we calculated does not match."
    )
    assert classify_failure_category(exc) is FailureCategory.PERMISSION
