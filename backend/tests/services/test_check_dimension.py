"""DQ-dimension derivation tests (ADR 0038, #124).

Pure — no DB, no datasource. The map is a judgement call, so these tests are as
much a record of the decision as a regression guard.
"""

from __future__ import annotations

import pytest

from backend.app.db.models import DQ_DIMENSIONS
from backend.app.services.check_dimension import (
    derive_dimension,
    is_valid_dimension,
    resolve_dimension,
)

# ── derivation ──


@pytest.mark.parametrize(
    ("expectation_type", "kind", "expected"),
    [
        ("expect_column_values_to_not_be_null", "expectation", "completeness"),
        ("expect_table_row_count_to_be_between", "expectation", "completeness"),
        ("expect_column_values_to_be_unique", "expectation", "uniqueness"),
        ("expect_column_values_to_be_between", "expectation", "validity"),
        ("expect_column_values_to_be_in_set", "expectation", "validity"),
        ("expect_column_values_to_match_regex", "expectation", "validity"),
        ("expect_column_value_lengths_to_be_between", "expectation", "validity"),
        ("expect_column_values_to_be_of_type", "expectation", "validity"),
        ("monitor:freshness", "freshness", "timeliness"),
        ("monitor:volume", "volume", "completeness"),
        ("monitor:schema_drift", "schema_drift", "consistency"),
        ("comparison:records", "comparison", "consistency"),
        ("comparison:columns", "comparison", "consistency"),
    ],
)
def test_derives_the_documented_dimension(expectation_type: str, kind: str, expected: str) -> None:
    assert derive_dimension(expectation_type=expectation_type, kind=kind) == expected


def test_every_derived_value_is_in_the_canonical_vocabulary() -> None:
    """A derived value outside DQ_DIMENSIONS would violate the table CHECK and
    500 on insert — the map and the column must not drift."""
    types = [
        "expect_column_values_to_not_be_null",
        "expect_table_row_count_to_be_between",
        "expect_column_values_to_be_unique",
        "expect_column_values_to_be_between",
        "monitor:freshness",
        "monitor:volume",
        "monitor:schema_drift",
        "comparison:records",
    ]
    for t in types:
        derived = derive_dimension(expectation_type=t, kind="expectation")
        assert derived is None or derived in DQ_DIMENSIONS


# ── the deliberately-undecidable cases ──


def test_custom_sql_has_no_derived_dimension() -> None:
    """An arbitrary SQL predicate cannot be classified. Returning a plausible
    guess would fill the #889 scorecard with confident nonsense; None renders as
    the coverage gap it actually is."""
    assert (
        derive_dimension(expectation_type="unexpected_rows_expectation", kind="expectation") is None
    )


def test_accuracy_and_integrity_are_never_derived() -> None:
    """ADR 0038 §3. Whether data matches reality, or a relationship holds, is not
    knowable from a rule shape — these two exist only for the author to pick, and
    a map that guessed them would be lying."""
    derived = {
        derive_dimension(expectation_type=t, kind=k)
        for t, k in [
            ("expect_column_values_to_not_be_null", "expectation"),
            ("expect_column_values_to_be_unique", "expectation"),
            ("expect_column_values_to_be_between", "expectation"),
            ("expect_column_values_to_be_in_set", "expectation"),
            ("expect_column_values_to_match_regex", "expectation"),
            ("expect_column_value_lengths_to_be_between", "expectation"),
            ("expect_column_values_to_be_of_type", "expectation"),
            ("expect_table_row_count_to_be_between", "expectation"),
            ("monitor:freshness", "freshness"),
            ("monitor:volume", "volume"),
            ("monitor:schema_drift", "schema_drift"),
            ("comparison:records", "comparison"),
        ]
    }
    assert "accuracy" not in derived
    assert "integrity" not in derived


def test_an_unknown_type_on_a_known_kind_falls_back_to_the_kind() -> None:
    """The map is keyed on expectation_type FIRST but falls back to kind, so a new
    spelling for an existing monitor kind still classifies rather than silently
    landing unclassified."""
    assert derive_dimension(expectation_type="monitor:freshness_v2", kind="freshness") == (
        "timeliness"
    )


def test_an_unknown_type_and_kind_is_none_not_a_default() -> None:
    assert derive_dimension(expectation_type="totally_unknown", kind="expectation") is None


# ── resolve (explicit override wins) ──


def test_an_explicit_dimension_overrides_the_derived_one() -> None:
    """Derivation is a guess about intent, not a fact: the same between-check is
    Validity bounding a percentage and Accuracy asserting a reconciled total."""
    assert (
        resolve_dimension(
            expectation_type="expect_column_values_to_be_between",
            kind="expectation",
            explicit="accuracy",
        )
        == "accuracy"
    )


def test_no_explicit_dimension_falls_back_to_derivation() -> None:
    assert (
        resolve_dimension(
            expectation_type="expect_column_values_to_be_unique",
            kind="expectation",
            explicit=None,
        )
        == "uniqueness"
    )


def test_explicit_none_means_derive_not_clear() -> None:
    """The PATCH convention: None is "not provided", so it must NOT wipe a
    derivable classification back to unclassified."""
    assert (
        resolve_dimension(expectation_type="monitor:freshness", kind="freshness", explicit=None)
        == "timeliness"
    )


# ── validity predicate ──


@pytest.mark.parametrize("value", list(DQ_DIMENSIONS))
def test_every_canonical_dimension_validates(value: str) -> None:
    assert is_valid_dimension(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "Completeness",  # case matters — the column CHECK is exact
        "timeliness ",  # trailing space: the free-text failure mode §1 rejects
        "freshness",  # a KIND, not a dimension — the axes are easy to confuse
        "",
        None,
        123,
        ["completeness"],
    ],
)
def test_non_canonical_values_are_rejected(value: object) -> None:
    assert is_valid_dimension(value) is False
