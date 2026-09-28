"""Amazon Redshift on the generic SQL base (#1682) — config, session and identity."""

from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.datasources.redshift import REDSHIFT, amazon_ca_bundle
from backend.app.datasources.sql_engines import SQL_ENGINES

_SERVERLESS = "analytics.123456789012.us-east-2.redshift-serverless.amazonaws.com"
_CLUSTER = "examplecluster.abc123xyz789.us-west-2.redshift.amazonaws.com"
_PASSWORD = "not-a-real-password"  # nosec B105


def _config(**overrides: Any) -> Any:
    raw = {"host": _SERVERLESS, "database": "dev", "user": "dq_reader", "schema": "sales"}
    raw.update(overrides)
    return REDSHIFT.validate_config(raw)


def test_redshift_is_a_registered_generic_sql_engine() -> None:
    assert SQL_ENGINES["redshift"] is REDSHIFT


def test_the_defaults_are_redshifts() -> None:
    config = _config(schema=None)
    assert (config.effective_port, config.default_schema) == (5439, "public")


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "Sales"},
        {"host": "https://cluster.example.com"},
        {"sslmode": "prefer"},
        {"unknown_field": "x"},
    ],
)
def test_an_unusable_config_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _config(**overrides)


def test_the_url_uses_the_dialect_gx_requires() -> None:
    url, _ = REDSHIFT.engine_args(_config(), _PASSWORD)
    assert url.startswith(f"redshift+psycopg2://dq_reader:{_PASSWORD}@{_SERVERLESS}:5439/dev")


def test_the_session_is_read_only_and_scoped_to_the_schema() -> None:
    _, args = REDSHIFT.engine_args(_config(), _PASSWORD)
    assert args["options"] == (
        "-c default_transaction_read_only=on -c search_path=pg_catalog,sales,public"
    )
    _, writable = REDSHIFT.engine_args(_config(schema=None), _PASSWORD, read_only=False)
    assert writable["options"] == "-c search_path=pg_catalog,public"


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_every_tls_mode_verifies_against_amazons_cas(sslmode: str) -> None:
    _, args = REDSHIFT.engine_args(_config(sslmode=sslmode), _PASSWORD)
    assert args["sslmode"] == sslmode
    assert args["sslrootcert"] == amazon_ca_bundle()
    assert os.path.isfile(args["sslrootcert"])


def test_disabled_tls_carries_no_root_file() -> None:
    _, args = REDSHIFT.engine_args(_config(sslmode="disable"), _PASSWORD)
    assert "sslrootcert" not in args


@pytest.mark.parametrize(
    ("host", "authority"),
    [
        (_SERVERLESS, "analytics.us-east-2"),
        (_CLUSTER, "examplecluster.us-west-2"),
        (_CLUSTER.upper(), "examplecluster.us-west-2"),
        ("warehouse.example.com", "warehouse.example.com"),
        ("10.0.0.5", "10.0.0.5"),
    ],
)
def test_the_openlineage_namespace_follows_the_redshift_convention(
    host: str, authority: str
) -> None:
    assert REDSHIFT.namespace(_config(host=host)) == f"redshift://{authority}:5439"


def test_the_asset_name_carries_the_database() -> None:
    assert REDSHIFT.asset_name(_config(), schema="sales", table="orders") == "dev.sales.orders"


def test_only_lower_case_engines_hand_gx_the_schema_alongside_the_session() -> None:
    """GX lower-cases the schema it is given, which would retarget a mixed-case schema."""
    assert REDSHIFT.gx_schema_with_session
    for spec in SQL_ENGINES.values():
        if spec.gx_schema_with_session:
            assert spec.config_model.names_are_lower_case, spec.conn_type
