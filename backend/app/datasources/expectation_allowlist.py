"""The vetted set of GX expectation types DataQ will author (#1510, Theme 4a).

Until this module existed, `validate_expectation_check` accepted ANY class reachable by
title-casing `expectation_type` into a `great_expectations.expectations` attribute — roughly 56
built-ins, of which the product had vetted 15. The rest were reachable from REST, MCP and suite
import, and several of them save cleanly and then error on every run, or return a result shape the
ADR-0016 severity bands cannot read.

This is scope 4a and deliberately not 4b: the allowlist names GX's OWN built-ins. Loading an
arbitrary `Expectation` subclass is code execution and is not on the table.

Every entry here was executed on both a pandas batch and a SQLAlchemy batch
(`tests/datasources/test_catalog_expectation_runs.py`) rather than read off the GX docs.

Deliberately GX-free so the author-time gate can import it at module scope — importing
`great_expectations` costs seconds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpectationCapability:
    """Per-type backend metadata. One field today; the record exists so the next
    per-type fact (SQL-only, unbandable, …) lands here instead of in a fifth set."""

    #: GX registers no SqlAlchemy metric provider, so this type errors on a SQL batch.
    dataframe_only: bool = False


_ROW_WISE = ExpectationCapability()
_DATAFRAME_ONLY = ExpectationCapability(dataframe_only=True)

#: type → capability. Grouped as the check editor groups them.
ALLOWED_EXPECTATIONS: dict[str, ExpectationCapability] = {
    # ── Column values: presence, uniqueness, range ──
    "expect_column_values_to_not_be_null": _ROW_WISE,
    "expect_column_values_to_be_null": _ROW_WISE,
    "expect_column_values_to_be_unique": _ROW_WISE,
    "expect_column_values_to_be_between": _ROW_WISE,
    # ── Column values: membership ──
    "expect_column_values_to_be_in_set": _ROW_WISE,
    "expect_column_values_to_not_be_in_set": _ROW_WISE,
    "expect_column_distinct_values_to_be_in_set": _ROW_WISE,
    "expect_column_distinct_values_to_contain_set": _ROW_WISE,
    # ── Column values: text shape ──
    "expect_column_values_to_match_regex": _ROW_WISE,
    "expect_column_values_to_not_match_regex": _ROW_WISE,
    "expect_column_values_to_match_regex_list": _ROW_WISE,
    "expect_column_values_to_not_match_regex_list": _ROW_WISE,
    "expect_column_value_lengths_to_be_between": _ROW_WISE,
    "expect_column_value_lengths_to_equal": _ROW_WISE,
    "expect_column_values_to_match_strftime_format": _DATAFRAME_ONLY,
    "expect_column_values_to_be_json_parseable": _DATAFRAME_ONLY,
    # ── Column values: type ──
    "expect_column_values_to_be_of_type": _ROW_WISE,
    "expect_column_values_to_be_in_type_list": _ROW_WISE,
    # ── Cross-column row rules ──
    "expect_compound_columns_to_be_unique": _ROW_WISE,
    "expect_select_column_values_to_be_unique_within_record": _ROW_WISE,
    "expect_column_pair_values_a_to_be_greater_than_b": _ROW_WISE,
    "expect_column_pair_values_to_be_equal": _ROW_WISE,
    # Allowlist-only, and deliberately so (see ALLOWLIST_ONLY_TYPES): its `value_pairs_set` is a
    # list of PAIRS, which the editor's flat comma-separated `list` field cannot express.
    "expect_column_pair_values_to_be_in_set": _ROW_WISE,
    "expect_multicolumn_sum_to_equal": _ROW_WISE,
    # ── Table shape ──
    "expect_table_row_count_to_be_between": _ROW_WISE,
}

ALLOWED_EXPECTATION_TYPES: frozenset[str] = frozenset(ALLOWED_EXPECTATIONS)

#: Allowlisted but NOT offered in the check editor: authorable over REST, MCP and suite import,
#: which hand the backend raw JSON, but with no widget that can express the config. The allowlist
#: is a superset of the catalog by design; this names the difference so it stays a decision rather
#: than drift (`tests/datasources/test_expectation_allowlist.py` asserts the delta is exactly this).
ALLOWLIST_ONLY_TYPES: frozenset[str] = frozenset({"expect_column_pair_values_to_be_in_set"})

DATAFRAME_ONLY_EXPECTATION_TYPES: frozenset[str] = frozenset(
    name for name, capability in ALLOWED_EXPECTATIONS.items() if capability.dataframe_only
)


def is_allowed(expectation_type: str) -> bool:
    """Whether DataQ will author this GX expectation type."""
    return expectation_type in ALLOWED_EXPECTATION_TYPES
