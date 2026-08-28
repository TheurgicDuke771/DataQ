"""The catalog's expectations executed for real through the shared `gx_runner` (#1509).

The contract test beside this one proves a catalog entry CONSTRUCTS under the pinned GX. That is
not the same as running: GX registers metric providers per execution engine, so an expectation can
construct fine and then find no provider on the batch its datasource builds. These tests therefore
run every entry twice — once on a pandas batch (the flat-file / Iceberg / Unity-Catalog shape) and
once on a SQLAlchemy batch (the Snowflake shape, stood up on sqlite: providers are registered
against the engine CLASS, so dialect is irrelevant to whether one exists).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import great_expectations as gx
import pandas as pd
import pytest
import sqlalchemy as sa

from backend.app.datasources import flatfile
from backend.app.datasources.base import CheckOutcome, CheckSpec
from backend.app.datasources.gx_runner import (
    DATAFRAME_ONLY_EXPECTATION_TYPES,
    run_expectations,
)

_FRAME = pd.DataFrame(
    {
        "id": [1, 2, 3, 4, 5],
        "region": ["eu", "eu", "us", "us", "us"],
        "status": ["new", "new", "shipped", "shipped", "cancelled"],
        "started_on": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"],
        "amount": [10, 20, 30, 40, 50],
        # part_a + part_b is 10 on four rows and 9 on the fifth → unexpected_percent 20.0.
        "part_a": [1, 2, 3, 4, 5],
        "part_b": [9, 8, 7, 6, 4],
        # One null in five rows → unexpected_percent 20.0 on a not-null check.
        "note": ["a", None, "c", "d", "e"],
    }
)


def _pandas_outcomes(specs: list[CheckSpec], monkeypatch: pytest.MonkeyPatch) -> list[CheckOutcome]:
    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: _FRAME)
    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(None, 4096))
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    return runner.run_checks(table="data/orders.csv", schema=None, checks=specs).checks


def _sql_outcomes(specs: list[CheckSpec], tmp_path: Path) -> list[CheckOutcome]:
    db = tmp_path / "orders.db"
    url = f"sqlite:///{db}"
    _FRAME.to_sql("orders", sa.create_engine(url), index=False)
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_sqlite(name="sq", connection_string=url)
    asset = source.add_table_asset(name="orders", table_name="orders")
    batch_definition = asset.add_batch_definition_whole_table("bd")
    return run_expectations(
        context, batch_definition=batch_definition, checks=specs, name="catalog"
    ).checks


def _unexpected_percent(outcome: CheckOutcome) -> Any:
    return (outcome.sample_failures or {}).get("unexpected_percent")


# ───────────────────────────── `mostly` tolerance ─────────────────────────────

_NOT_NULL = "expect_column_values_to_not_be_null"


def test_mostly_moves_gx_success_without_moving_the_banded_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADR-0016 claim, executed rather than asserted: `mostly` changes whether GX calls the
    check successful and leaves `unexpected_percent` — the number the severity bands read
    (`services/severity.extract_metric`) — untouched.
    """
    strict, tolerant, tight = _pandas_outcomes(
        [
            CheckSpec(_NOT_NULL, {"column": "note"}),
            CheckSpec(_NOT_NULL, {"column": "note", "mostly": 0.5}),
            CheckSpec(_NOT_NULL, {"column": "note", "mostly": 0.95}),
        ],
        monkeypatch,
    )
    assert (strict.success, tolerant.success, tight.success) == (False, True, False)
    assert _unexpected_percent(strict) == 20.0
    assert _unexpected_percent(tolerant) == 20.0
    assert _unexpected_percent(tight) == 20.0


def test_mostly_runs_on_a_sql_batch_too(tmp_path: Path) -> None:
    (tolerant,) = _sql_outcomes([CheckSpec(_NOT_NULL, {"column": "note", "mostly": 0.5})], tmp_path)
    assert tolerant.success is True
    assert tolerant.errored is False
    assert _unexpected_percent(tolerant) == 20.0


# ───────────────────── every catalog type, on both engines ─────────────────────

#: One runnable spec per GX catalog type, against `_FRAME`. `test_every_catalog_gx_type_has_a_run
#: _sample` below fails when a catalog entry is added without one, so this table cannot silently
#: fall behind the catalog.
_RUN_SAMPLES: dict[str, dict[str, Any]] = {
    "expect_column_values_to_not_be_null": {"column": "id"},
    "expect_column_values_to_be_unique": {"column": "id"},
    "expect_column_values_to_be_between": {"column": "amount", "min_value": 1, "max_value": 100},
    "expect_column_values_to_be_in_set": {
        "column": "status",
        "value_set": ["new", "shipped", "cancelled"],
    },
    "expect_column_value_lengths_to_be_between": {
        "column": "status",
        "min_value": 1,
        "max_value": 20,
    },
    "expect_column_values_to_match_regex": {"column": "status", "regex": "^[a-z]+$"},
    "expect_column_values_to_be_of_type": {"column": "status", "type_": "object"},
    "expect_column_values_to_be_in_type_list": {"column": "id", "type_list": ["int64", "BIGINT"]},
    "expect_compound_columns_to_be_unique": {"column_list": ["status", "started_on"]},
    "expect_column_pair_values_a_to_be_greater_than_b": {
        "column_A": "amount",
        "column_B": "id",
    },
    "expect_multicolumn_sum_to_equal": {"column_list": ["part_a", "part_b"], "sum_total": 10},
    "expect_column_distinct_values_to_be_in_set": {
        "column": "status",
        "value_set": ["new", "shipped", "cancelled"],
    },
    "expect_column_distinct_values_to_contain_set": {"column": "status", "value_set": ["new"]},
    "expect_column_values_to_match_strftime_format": {
        "column": "started_on",
        "strftime_format": "%Y-%m-%d",
    },
    "expect_table_row_count_to_be_between": {"min_value": 1, "max_value": 100},
}


def _gx_catalog_types() -> list[str]:
    """The catalog's GX expectation types, read off the same fixture the contract test uses."""
    from backend.app.datasources.snowflake_dmf import DMF_EXPECTATION_TYPES
    from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE

    with (Path(__file__).parent.parent / "fixtures" / "expectation_catalog.json").open() as f:
        catalog = json.load(f)
    return [
        entry["type"]
        for entry in catalog
        if entry["kind"] == "expectation" and entry["type"] not in DMF_EXPECTATION_TYPES
        # Custom SQL needs a `{batch}` query rendered by its own runner path, covered elsewhere.
        and entry["type"] != CUSTOM_SQL_EXPECTATION_TYPE
    ]


def test_every_catalog_gx_type_has_a_run_sample() -> None:
    """Without this, adding a catalog entry silently skips the two sweeps below."""
    # An empty list would make both sweeps pass vacuously.
    assert len(_gx_catalog_types()) >= len(_RUN_SAMPLES)
    missing = [t for t in _gx_catalog_types() if t not in _RUN_SAMPLES]
    assert not missing, f"add a `_RUN_SAMPLES` entry for {missing} so it is actually run"


def test_every_catalog_type_runs_on_a_pandas_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    types = _gx_catalog_types()
    outcomes = _pandas_outcomes([CheckSpec(t, dict(_RUN_SAMPLES[t])) for t in types], monkeypatch)
    errored = {t: o.error_message for t, o in zip(types, outcomes, strict=True) if o.errored}
    assert not errored


def test_every_sql_capable_catalog_type_runs_on_a_sql_batch(tmp_path: Path) -> None:
    """The Snowflake shape, minus the types the runner declares DataFrame-only."""
    types = [t for t in _gx_catalog_types() if t not in DATAFRAME_ONLY_EXPECTATION_TYPES]
    assert types  # an over-broad DataFrame-only set would empty this sweep
    outcomes = _sql_outcomes([CheckSpec(t, dict(_RUN_SAMPLES[t])) for t in types], tmp_path)
    errored = {t: o.error_message for t, o in zip(types, outcomes, strict=True) if o.errored}
    assert not errored


@pytest.mark.parametrize("expectation_type", sorted(DATAFRAME_ONLY_EXPECTATION_TYPES))
def test_a_dataframe_only_type_really_does_error_on_a_sql_batch(
    expectation_type: str, tmp_path: Path
) -> None:
    """The reason `_reject_dataframe_only_expectation` 422s at author time. Without this the
    exclusion above would look like caution rather than a measured fact — and a later GX release
    that DOES add a SqlAlchemy provider should retire the gate, which only a failing test surfaces.
    """
    (outcome,) = _sql_outcomes(
        [CheckSpec(expectation_type, dict(_RUN_SAMPLES[expectation_type]))], tmp_path
    )
    assert outcome.errored is True
    assert "No provider found" in (outcome.error_message or "")


# ─────────────────── cross-column + set-relation result shapes ────────────────


def test_cross_column_types_report_the_banded_unexpected_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-column types are row-wise, so they DO feed the ADR-0016 bands."""
    compound, pair, multisum = _pandas_outcomes(
        [
            # (status, region) repeats on four of five rows.
            CheckSpec(
                "expect_compound_columns_to_be_unique", {"column_list": ["status", "region"]}
            ),
            CheckSpec(
                "expect_column_pair_values_a_to_be_greater_than_b",
                {"column_A": "id", "column_B": "amount"},
            ),
            CheckSpec(
                "expect_multicolumn_sum_to_equal",
                {"column_list": ["part_a", "part_b"], "sum_total": 10},
            ),
        ],
        monkeypatch,
    )
    assert (compound.success, pair.success, multisum.success) == (False, False, False)
    assert _unexpected_percent(compound) == 80.0
    assert _unexpected_percent(pair) == 100.0
    assert _unexpected_percent(multisum) == 20.0


def test_or_equal_widens_the_pair_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    strict, allowing_equal = _pandas_outcomes(
        [
            CheckSpec(
                "expect_column_pair_values_a_to_be_greater_than_b",
                {"column_A": "id", "column_B": "id"},
            ),
            CheckSpec(
                "expect_column_pair_values_a_to_be_greater_than_b",
                {"column_A": "id", "column_B": "id", "or_equal": True},
            ),
        ],
        monkeypatch,
    )
    assert strict.success is False
    assert allowing_equal.success is True


def test_distinct_value_set_relations_report_no_unexpected_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why the catalog tells the author their severity bands are inert on these two: they compare
    a SET, so there is no `unexpected_percent` for `severity.extract_metric` to band.
    """
    in_set, contain_set = _pandas_outcomes(
        [
            CheckSpec(
                "expect_column_distinct_values_to_be_in_set",
                {"column": "status", "value_set": ["new", "shipped"]},
            ),
            CheckSpec(
                "expect_column_distinct_values_to_contain_set",
                {"column": "status", "value_set": ["new", "returned"]},
            ),
        ],
        monkeypatch,
    )
    assert (in_set.success, contain_set.success) == (False, False)
    assert _unexpected_percent(in_set) is None
    assert _unexpected_percent(contain_set) is None
