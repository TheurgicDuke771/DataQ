"""DQ-dimension derivation (ADR 0038) — `expectation_type`/`kind` → dimension.

The single place that guesses *what quality aspect* a check measures, so the
guess can be argued with in one file instead of being scattered across the
authoring path, the importer and the MCP tool.

Derivation is **deliberately partial**. Two dimensions are never derived:

* ``accuracy`` — whether data matches reality is not knowable from a rule shape.
* ``integrity`` — whether a relationship holds needs the relationship, not the
  predicate.

and custom SQL (ADR 0019) is an arbitrary predicate with no derivable answer at
all. Returning a plausible-looking dimension for those would fill the #889
scorecard with confident nonsense; an honest ``None`` renders as a coverage gap,
which is the thing the scorecard exists to show.

DB-free and import-cheap on purpose: the authoring path, `suite_io_service` and
the MCP tool all call it, and none of them should pull the datasource layer in
just to classify a string.
"""

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
}

# Fallback for the non-GX kinds, whose `expectation_type` is the generated
# `monitor:<kind>` / `comparison:<shape>` string. Keyed on `kind` so a new
# expectation_type spelling for an existing kind still classifies.
_BY_KIND: dict[str, str] = {
    "freshness": TIMELINESS,
    "volume": COMPLETENESS,  # a short load is missing data
    "schema_drift": CONSISTENCY,  # structural stability over time
    "comparison": CONSISTENCY,  # cross-dataset agreement (ADR 0015)
}


def derive_dimension(*, expectation_type: str, kind: str) -> str | None:
    """The default DQ dimension for a check, or ``None`` when undecidable.

    ``None`` is a real answer, not a failure — see the module docstring. Callers
    must not substitute a placeholder for it.
    """
    derived = _BY_EXPECTATION_TYPE.get(expectation_type)
    if derived is not None:
        return derived
    return _BY_KIND.get(kind)


def resolve_dimension(*, expectation_type: str, kind: str, explicit: str | None) -> str | None:
    """The dimension to store: the author's choice if given, else the derived one.

    Note the asymmetry — an ``explicit`` value wins, but an explicit ``None``
    does *not* mean "clear it", it means "not specified, please derive". Clearing
    a dimension back to unclassified is therefore not expressible on create; that
    is intentional, since the only way to reach `None` deliberately is to author a
    check whose type has no derivation. Validation of the value itself belongs to
    the caller (each has its own error shape).
    """
    if explicit is not None:
        return explicit
    return derive_dimension(expectation_type=expectation_type, kind=kind)


def is_valid_dimension(value: object) -> bool:
    """Whether ``value`` is one of the seven canonical dimensions (ADR 0038)."""
    return isinstance(value, str) and value in DQ_DIMENSIONS
