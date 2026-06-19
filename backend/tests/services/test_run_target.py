"""Unit tests for run_target.resolve_target / validate_target / materialize_path.

`resolve_target` / `validate_target` are pure (no DB, no datasource): each
datasource's required field, the targetless / wrong-datasource error paths, the
flat-file path riding the runner's `table` slot, and the flat-file *batch* spec
validation (#122/A4). `materialize_path` is the live step — its batch branch is
exercised with `flatfile.resolve_batch_file` monkeypatched (the listing is the
deferred-smoke seam); the non-batch branch is a pure pass-through.
"""

from typing import Any

import pytest

from backend.app.services import run_target
from backend.app.services.run_target import (
    ResolvedTarget,
    SuiteTargetInvalidError,
    resolve_target,
    validate_target,
)


def test_snowflake_resolves_table_and_optional_schema() -> None:
    r = resolve_target("snowflake", {"table": "ORDERS", "schema": "SALES"})
    assert (r.table, r.schema, r.catalog) == ("ORDERS", "SALES", None)


def test_snowflake_schema_optional() -> None:
    r = resolve_target("snowflake", {"table": "ORDERS"})
    assert (r.table, r.schema, r.catalog) == ("ORDERS", None, None)


def test_unity_catalog_requires_catalog_and_table() -> None:
    r = resolve_target("unity_catalog", {"catalog": "main", "schema": "sales", "table": "orders"})
    assert (r.table, r.schema, r.catalog) == ("orders", "sales", "main")


def test_unity_catalog_missing_catalog_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("unity_catalog", {"table": "orders"})


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_path_rides_table_slot(conn_type: str) -> None:
    # The CheckRunner interface is table-shaped; the file path is the `table`.
    r = resolve_target(conn_type, {"path": "data/orders.csv"})
    assert (r.table, r.schema, r.catalog) == ("data/orders.csv", None, None)


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_missing_path_raises(conn_type: str) -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target(conn_type, {"table": "orders"})  # SQL field, wrong datasource


def test_snowflake_missing_table_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", {"schema": "SALES"})


def test_blank_table_is_rejected() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", {"table": "   "})


@pytest.mark.parametrize("target", [None, {}])
def test_targetless_suite_raises(target: dict[str, str] | None) -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("snowflake", target)


@pytest.mark.parametrize("conn_type", ["adf", "airflow"])
def test_orchestration_types_have_no_run_path(conn_type: str) -> None:
    # ADF / Airflow are orchestration providers, never suite datasources.
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target(conn_type, {"table": "x"})


def test_validate_target_is_resolve_without_return() -> None:
    validate_target("snowflake", {"table": "ORDERS"})  # no raise
    with pytest.raises(SuiteTargetInvalidError):
        validate_target("snowflake", {"schema": "SALES"})


# ───────────────────────── flat-file batch spec (A4) ───────────────


@pytest.mark.parametrize("conn_type", ["adls_gen2", "s3"])
def test_flatfile_batch_latest_default(conn_type: str) -> None:
    r = resolve_target(
        conn_type, {"prefix": "orders/", "pattern": r"orders_(\d{4}-\d{2}-\d{2})\.csv"}
    )
    assert r.table == "" and r.batch is not None
    assert (r.batch.prefix, r.batch.strategy, r.batch.batch) == ("orders/", "latest", None)
    assert r.batch.pattern == r"orders_(\d{4}-\d{2}-\d{2})\.csv"


def test_flatfile_batch_prefix_optional_defaults_empty() -> None:
    r = resolve_target("s3", {"pattern": r"(\d+)\.csv"})
    assert r.batch is not None and r.batch.prefix == ""


def test_flatfile_batch_specific_requires_batch_key() -> None:
    r = resolve_target(
        "s3", {"pattern": r"(\d+)\.csv", "strategy": "specific", "batch": "2026-06-01"}
    )
    assert r.batch is not None and r.batch.strategy == "specific" and r.batch.batch == "2026-06-01"


def test_flatfile_batch_specific_without_batch_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"(\d+)\.csv", "strategy": "specific"})


def test_flatfile_batch_latest_ignores_batch_key() -> None:
    # 'batch' only applies to 'specific'; under 'latest' it is dropped.
    r = resolve_target("s3", {"pattern": r"(\d+)\.csv", "batch": "ignored"})
    assert r.batch is not None and r.batch.batch is None


def test_flatfile_batch_unknown_strategy_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"(\d+)\.csv", "strategy": "newest"})


def test_flatfile_batch_blank_pattern_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": "   "})


def test_flatfile_batch_non_string_prefix_raises() -> None:
    with pytest.raises(SuiteTargetInvalidError):
        resolve_target("s3", {"pattern": r"(\d+)\.csv", "prefix": 123})


def test_validate_target_accepts_batch_spec() -> None:
    validate_target("s3", {"pattern": r"(\d+)\.csv", "strategy": "latest"})  # no raise


# ───────────────────────── materialize_path (A4 live step) ─────────


class _FakeStore:
    def get(self, ref: str) -> str:
        return "secret-value"


def test_materialize_path_passthrough_for_non_batch() -> None:
    # SQL / literal flat-file targets have no batch → table returned unchanged,
    # and the store is never consulted (no listing needed).
    resolved = ResolvedTarget(table="ORDERS", schema="SALES", catalog=None)
    out = run_target.materialize_path(
        "snowflake", {}, resolved, secret_ref=None, secret_store=_FakeStore()
    )
    assert out == "ORDERS"


def test_materialize_path_resolves_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = resolve_target("s3", {"prefix": "orders/", "pattern": r"orders_(\d+)\.csv"})
    captured: dict[str, Any] = {}

    def _fake_resolve(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "orders/orders_20260601.csv"

    monkeypatch.setattr("backend.app.datasources.flatfile.resolve_batch_file", _fake_resolve)
    out = run_target.materialize_path(
        "s3", {"bucket": "b"}, resolved, secret_ref="kv-ref", secret_store=_FakeStore()
    )
    assert out == "orders/orders_20260601.csv"
    # the resolved BatchSpec + resolved secret are threaded to the lister
    assert captured["prefix"] == "orders/" and captured["strategy"] == "latest"
    assert captured["secret"] == "secret-value" and captured["conn_type"] == "s3"


def test_materialize_path_batch_without_secret_raises() -> None:
    resolved = resolve_target("s3", {"pattern": r"(\d+)\.csv"})
    with pytest.raises(SuiteTargetInvalidError):
        run_target.materialize_path("s3", {}, resolved, secret_ref=None, secret_store=_FakeStore())
