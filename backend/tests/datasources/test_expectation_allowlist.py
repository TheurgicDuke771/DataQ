"""The expectation allowlist and its contract with the catalog + GX (#1510)."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import great_expectations.expectations as gxe
import pytest
from great_expectations.expectations.expectation import Expectation

from backend.app.datasources.expectation_allowlist import (
    ALLOWED_EXPECTATION_TYPES,
    ALLOWED_EXPECTATIONS,
    ALLOWLIST_ONLY_TYPES,
    DATAFRAME_ONLY_EXPECTATION_TYPES,
    is_allowed,
)
from backend.app.datasources.gx_runner import _expectation_class_name
from backend.app.datasources.snowflake_dmf import DMF_EXPECTATION_TYPES
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "expectation_catalog.json"


@functools.cache
def _catalog_gx_types() -> set[str]:
    with _FIXTURE.open() as f:
        catalog: list[dict[str, Any]] = json.load(f)
    return {
        entry["type"]
        for entry in catalog
        if entry["kind"] == "expectation"
        and entry["type"] not in DMF_EXPECTATION_TYPES
        and entry["type"] != CUSTOM_SQL_EXPECTATION_TYPE
    }


def test_the_allowlist_is_not_empty_and_is_not_all_of_gx() -> None:
    """Both failure modes at once: a gutted allowlist would vacuously pass the sweeps below, and
    an allowlist that had grown to every built-in would be the ungated state #1510 removed.
    """
    all_gx = {n for n in dir(gxe) if n.startswith("Expect") and n != "Expectation"}
    assert 20 <= len(ALLOWED_EXPECTATION_TYPES) < len(all_gx)


@pytest.mark.parametrize("expectation_type", sorted(ALLOWED_EXPECTATION_TYPES))
def test_every_allowlisted_type_resolves_to_a_gx_expectation(expectation_type: str) -> None:
    """An allowlisted type that no longer resolves is a GX rename; `validate_expectation_check`
    would 422 every author attempt on it with a message nobody could act on.
    """
    cls = getattr(gxe, _expectation_class_name(expectation_type), None)
    assert cls is not None and issubclass(cls, Expectation)


def test_the_catalog_is_a_subset_of_the_allowlist() -> None:
    """The direction that breaks users: a catalog entry outside the allowlist is offered in the
    editor and then 422s on save.
    """
    assert _catalog_gx_types() <= ALLOWED_EXPECTATION_TYPES


def test_the_allowlist_superset_is_exactly_the_declared_delta() -> None:
    """The allowlist may exceed the catalog — REST/MCP/import take raw JSON and the editor's
    widgets cannot express every config — but each extra type is a decision, named in
    `ALLOWLIST_ONLY_TYPES`, not drift.
    """
    assert ALLOWED_EXPECTATION_TYPES - _catalog_gx_types() == ALLOWLIST_ONLY_TYPES


def test_dataframe_only_flags_are_derived_from_the_capability_records() -> None:
    assert DATAFRAME_ONLY_EXPECTATION_TYPES == {
        name for name, capability in ALLOWED_EXPECTATIONS.items() if capability.dataframe_only
    }
    assert DATAFRAME_ONLY_EXPECTATION_TYPES <= ALLOWED_EXPECTATION_TYPES


def test_is_allowed_rejects_a_real_gx_expectation_outside_the_set() -> None:
    """The whole point of 4a: `expect_column_max_to_be_between` is a genuine GX built-in and is
    deliberately NOT enabled (#1602 — a scalar aggregate belongs on the monitor `metric_value`
    path, where it gets trends and anomaly baselines, not on the GX-banded one).
    """
    assert hasattr(gxe, "ExpectColumnMaxToBeBetween")
    assert not is_allowed("expect_column_max_to_be_between")
    assert is_allowed("expect_column_values_to_not_be_null")


def test_unbanded_set_is_exactly_the_two_distinct_value_relations() -> None:
    """Live-verified on Snowflake: pass and fail runs emit no unexpected_percent."""
    from backend.app.datasources.expectation_allowlist import UNBANDED_EXPECTATION_TYPES

    assert UNBANDED_EXPECTATION_TYPES == {
        "expect_column_distinct_values_to_be_in_set",
        "expect_column_distinct_values_to_contain_set",
    }
    assert UNBANDED_EXPECTATION_TYPES <= ALLOWED_EXPECTATION_TYPES
