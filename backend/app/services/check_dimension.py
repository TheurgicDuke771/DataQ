"""DQ-dimension derivation (ADR 0038) — `expectation_type`/`kind` → dimension."""

from __future__ import annotations

from backend.app.db.models import DQ_DIMENSIONS

ACCURACY = "accuracy"
COMPLETENESS = "completeness"
CONSISTENCY = "consistency"
INTEGRITY = "integrity"
TIMELINESS = "timeliness"
UNIQUENESS = "uniqueness"
VALIDITY = "validity"

# Keyed on `expectation_type` FIRST because `kind='expectation'` spans a dozen
# different dimensions — keying on kind alone would collapse them all into one.
_BY_EXPECTATION_TYPE: dict[str, str] = {
    # Completeness — "is all the data here"
    "expect_column_values_to_not_be_null": COMPLETENESS,
    "expect_table_row_count_to_be_between": COMPLETENESS,
    # Uniqueness
    "expect_column_values_to_be_unique": UNIQUENESS,
    # Validity — "does it conform to the rule"
    "expect_column_values_to_be_between": VALIDITY,
    "expect_column_values_to_be_in_set": VALIDITY,
    "expect_column_values_to_match_regex": VALIDITY,
    "expect_column_value_lengths_to_be_between": VALIDITY,
    "expect_column_values_to_be_of_type": VALIDITY,
    # Snowflake DMF column metrics (ADR 0036 slice 2).
    "dmf:null_count": COMPLETENESS,
    "dmf:null_percent": COMPLETENESS,
    "dmf:duplicate_count": UNIQUENESS,
    "dmf:unique_count": UNIQUENESS,
}

# Fallback for the non-GX kinds, whose `expectation_type` is the generated `monitor:<kind>` /
# `comparison:<shape>` string.
_BY_KIND: dict[str, str] = {
    "freshness": TIMELINESS,
    "volume": COMPLETENESS,  # a short load is missing data
    "schema_drift": CONSISTENCY,  # structural stability over time
    "comparison": CONSISTENCY,  # cross-dataset agreement (ADR 0015)
}


def derive_dimension(*, expectation_type: str, kind: str) -> str | None:
    """The default DQ dimension for a check, or ``None`` when undecidable."""
    derived = _BY_EXPECTATION_TYPE.get(expectation_type)
    if derived is not None:
        return derived
    return _BY_KIND.get(kind)


def resolve_dimension(*, expectation_type: str, kind: str, explicit: str | None) -> str | None:
    """The dimension to store: the author's choice if given, else the derived one."""
    if explicit is not None:
        return explicit
    return derive_dimension(expectation_type=expectation_type, kind=kind)


def is_valid_dimension(value: object) -> bool:
    """Whether ``value`` is one of the seven canonical dimensions (ADR 0038)."""
    return isinstance(value, str) and value in DQ_DIMENSIONS
