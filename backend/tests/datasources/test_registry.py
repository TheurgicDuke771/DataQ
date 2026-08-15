"""Runner-registry dispatch tests (#146).

`build_check_runner` routes by `connection.type` to the right `CheckRunner`
builder, so the worker never branches on the type. Builders are exercised far
enough to return a runner (no live connection — that's lazy), asserting the
concrete runner class per type plus the error paths.
"""

import pytest

from backend.app.datasources.base import SampleSpec, TargetShapeError
from backend.app.datasources.flatfile import FlatFileCheckRunner
from backend.app.datasources.iceberg import IcebergCheckRunner
from backend.app.datasources.registry import (
    SAMPLING_CAPABLE_TYPES,
    UnsupportedConnectionTypeError,
    build_check_runner,
    resolve_target_shape,
)
from backend.app.datasources.snowflake import SnowflakeCheckRunner
from backend.app.datasources.unity_catalog import UnityCatalogCheckRunner
from backend.tests.support.fake_secret_store import FakeSecretStore

_SNOWFLAKE_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}
_UC_CONFIG = {"workspace_url": "https://adb-1234.5.azuredatabricks.net", "warehouse_id": "abc123"}
_S3_CONFIG = {"bucket": "data", "region": "eu-west-1"}
_ICEBERG_CONFIG = {
    "catalog_type": "rest",
    "catalog_uri": "https://catalog.example.com",
    "secret_property": "token",
}


def test_dispatches_snowflake() -> None:
    runner = build_check_runner(
        conn_type="snowflake",
        config=_SNOWFLAKE_CONFIG,
        secret_ref="sf",
        secret_store=FakeSecretStore(default="secret"),
    )
    assert isinstance(runner, SnowflakeCheckRunner)


@pytest.mark.parametrize("conn_type", ["s3", "adls_gen2"])
def test_dispatches_flatfile(conn_type: str) -> None:
    runner = build_check_runner(
        conn_type=conn_type,
        config=_S3_CONFIG,
        secret_ref="ff",
        secret_store=FakeSecretStore(default="secret"),
    )
    assert isinstance(runner, FlatFileCheckRunner)


def test_dispatches_unity_catalog_with_catalog() -> None:
    runner = build_check_runner(
        conn_type="unity_catalog",
        config=_UC_CONFIG,
        secret_ref="pat",
        secret_store=FakeSecretStore(default="secret"),
        catalog="main",
    )
    assert isinstance(runner, UnityCatalogCheckRunner)


def test_dispatches_iceberg() -> None:
    runner = build_check_runner(
        conn_type="iceberg",
        config=_ICEBERG_CONFIG,
        secret_ref="iceberg-cred",
        secret_store=FakeSecretStore(default="secret"),
    )
    assert isinstance(runner, IcebergCheckRunner)


def test_unity_catalog_without_catalog_raises() -> None:
    with pytest.raises(UnsupportedConnectionTypeError):
        build_check_runner(
            conn_type="unity_catalog",
            config=_UC_CONFIG,
            secret_ref="pat",
            secret_store=FakeSecretStore(default="secret"),
        )


@pytest.mark.parametrize("conn_type", ["adf", "airflow", "bogus"])
def test_non_datasource_or_unknown_type_raises(conn_type: str) -> None:
    """Orchestration providers have no runner; nor does an unknown type."""
    with pytest.raises(UnsupportedConnectionTypeError):
        build_check_runner(
            conn_type=conn_type,
            config={},
            secret_ref="x",
            secret_store=FakeSecretStore(default="secret"),
        )


# ── sampling on the run target (#595) ────────────────────────────────────────


@pytest.mark.parametrize("conn_type", sorted(SAMPLING_CAPABLE_TYPES))
def test_a_sampling_block_resolves_on_every_full_load_datasource(
    conn_type: str,
) -> None:
    target = {
        "path": "raw/orders.csv",
        "table": "orders",
        "schema": "sales",
        "catalog": "main",
        "sampling": {"strategy": "random", "rows": 1000, "seed": 3},
    }
    resolved = resolve_target_shape(conn_type, target)
    assert resolved.sampling == SampleSpec(strategy="random", rows=1000, seed=3)


def test_a_target_without_sampling_resolves_to_none() -> None:
    """The default has to stay "read everything" — a silently-applied cap would
    change what every existing suite validates."""
    assert resolve_target_shape("s3", {"path": "raw/orders.csv"}).sampling is None


@pytest.mark.parametrize("conn_type", ["snowflake", "iceberg"])
def test_sampling_is_REFUSED_on_a_datasource_that_cannot_honour_it(
    conn_type: str,
) -> None:
    """Refused at SAVE time, not ignored at run time. A silently-dropped sampling
    block leaves an author believing their nightly 100M-row suite is bounded when
    it is not, and the first evidence would be the OOM this feature prevents.

    Snowflake is excluded because it pushes every expectation down and never
    materialises rows (200M rows, worker flat — docs/perf-baseline.md); Iceberg
    because its sampled read is not built yet. Both would be the same lie."""
    target = {
        "table": "orders",
        "schema": "sales",
        "sampling": {"strategy": "head", "rows": 10},
    }
    with pytest.raises(TargetShapeError, match="sampling"):
        resolve_target_shape(conn_type, target)


def test_a_malformed_sampling_block_is_a_target_shape_error() -> None:
    """`SamplingConfigError` is translated to the target-shape error the service
    layer maps to a 422 — so a bad spec is an author-time complaint, not a run
    that fails every night."""
    with pytest.raises(TargetShapeError, match="strategy"):
        resolve_target_shape("s3", {"path": "a.csv", "sampling": {"strategy": "tail", "rows": 1}})


def test_the_sampling_spec_reaches_the_flat_file_runner() -> None:
    """The seam is only worth anything if the builder passes it through — a spec
    that resolves but never reaches the runner is the silent-drop failure with
    extra steps."""
    sample = SampleSpec(strategy="head", rows=25)
    runner = build_check_runner(
        conn_type="s3",
        config=_S3_CONFIG,
        secret_ref="ff",
        secret_store=FakeSecretStore(default="secret"),
        sampling=sample,
    )
    assert isinstance(runner, FlatFileCheckRunner)
    assert runner._sampling == sample


def test_the_sampling_spec_reaches_the_unity_catalog_runner() -> None:
    sample = SampleSpec(strategy="random", rows=25, seed=2)
    runner = build_check_runner(
        conn_type="unity_catalog",
        config=_UC_CONFIG,
        secret_ref="pat",
        secret_store=FakeSecretStore(default="secret"),
        catalog="main",
        sampling=sample,
    )
    assert isinstance(runner, UnityCatalogCheckRunner)
    assert runner._sampling == sample


def test_a_pushdown_runner_ignores_a_sampling_spec_rather_than_crashing() -> None:
    """Belt over the braces: `SAMPLING_CAPABLE_TYPES` already refuses this at save
    time, so it is unreachable through the normal path — but a builder that raised
    on an unexpected keyword would turn a stale target into a failed run instead of
    an inert field."""
    runner = build_check_runner(
        conn_type="snowflake",
        config=_SNOWFLAKE_CONFIG,
        secret_ref="sf",
        secret_store=FakeSecretStore(default="secret"),
        sampling=SampleSpec(strategy="head", rows=10),
    )
    assert isinstance(runner, SnowflakeCheckRunner)


def test_every_sampling_capable_type_actually_threads_the_spec() -> None:
    """J5. `SAMPLING_CAPABLE_TYPES` (which decides what SAVES) is a different
    thing from whether a builder actually passes `sampling` to its runner — and
    the pushdown builders swallow the kwarg via `**_`. Adding a type to the set
    and forgetting the builder would therefore produce exactly the silent-drop
    this feature refuses at save time: accepted, persisted, never honoured.

    Asserted over the SET rather than per type, so a new entry is covered the
    moment it is added rather than when someone remembers to write its test.
    """
    spec = SampleSpec(strategy="head", rows=7)
    config_by_type = {
        "s3": (_S3_CONFIG, None),
        "adls_gen2": (_S3_CONFIG, None),
        "unity_catalog": (_UC_CONFIG, "main"),
    }
    assert (
        set(config_by_type) == SAMPLING_CAPABLE_TYPES
    ), "a sampling-capable type has no coverage here — add it to config_by_type"
    for conn_type, (config, catalog) in config_by_type.items():
        runner = build_check_runner(
            conn_type=conn_type,
            config=config,
            secret_ref="ref",
            secret_store=FakeSecretStore(default="secret"),
            catalog=catalog,
            sampling=spec,
        )
        assert (
            getattr(runner, "_sampling", "MISSING") == spec
        ), f"{conn_type} is declared sampling-capable but its builder drops the spec"
