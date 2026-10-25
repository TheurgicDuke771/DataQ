"""The ODCS → DataQ translation table (ADR 0052 §3): one test per row, plus every unmapped reason.

Every clause ends in exactly one place — a check draft or an `unmapped` entry — so each test
asserts both halves.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from backend.app.contracts import odcs
from backend.app.contracts import translate as tr

_SCHEMA_DRIFT = "monitor:schema_drift"
_WRAPPED_COUNT = "SELECT 1 WHERE NOT COALESCE((SELECT COUNT(*) FROM ORDERS) > 0, FALSE)"


def _translate(
    *,
    props: list[dict[str, Any]] | None = None,
    quality: list[dict[str, Any]] | None = None,
    sla: list[dict[str, Any]] | None = None,
    obj_extra: dict[str, Any] | None = None,
    api: str = "v3.2.0",
    connection_type: str = "postgres",
) -> tuple[tr.TranslatedObject, list[tr.Unmapped]]:
    obj: dict[str, Any] = {"name": "orders", "physicalName": "ORDERS", "properties": props or []}
    if quality:
        obj["quality"] = quality
    obj.update(obj_extra or {})
    doc: dict[str, Any] = {
        "apiVersion": api,
        "kind": "DataContract",
        "id": "c1",
        "version": "1.0.0",
        "status": "active",
        "schema": [obj],
    }
    if sla:
        doc["slaProperties"] = sla
    result = tr.translate(odcs.validate_document(doc).document, connection_type=connection_type)
    return result.objects[0], result.unmapped


def _checks(obj: tr.TranslatedObject) -> list[tuple[str, dict[str, Any]]]:
    return [
        (c.expectation_type, c.config) for c in obj.checks if c.expectation_type != _SCHEMA_DRIFT
    ]


def _reasons(unmapped: list[tr.Unmapped]) -> list[tuple[str, str]]:
    return [(u.field, u.reason) for u in unmapped]


@pytest.mark.parametrize(
    ("props", "expected"),
    [
        (
            [{"name": "a", "required": True}],
            [("expect_column_values_to_not_be_null", {"column": "a"})],
        ),
        ([{"name": "a", "unique": True}], [("expect_column_values_to_be_unique", {"column": "a"})]),
        (
            [{"name": "a", "primaryKey": True}],
            [("expect_column_values_to_be_unique", {"column": "a"})],
        ),
        (
            [
                {"name": "a", "primaryKey": True, "primaryKeyPosition": 1},
                {"name": "b", "primaryKey": True, "primaryKeyPosition": 2},
            ],
            [("expect_compound_columns_to_be_unique", {"column_list": ["a", "b"]})],
        ),
        (
            [
                {
                    "name": "a",
                    "logicalType": "number",
                    "logicalTypeOptions": {"minimum": 0, "maximum": 10},
                }
            ],
            [
                (
                    "expect_column_values_to_be_between",
                    {"column": "a", "min_value": 0, "max_value": 10},
                )
            ],
        ),
        (
            [
                {
                    "name": "a",
                    "logicalType": "string",
                    "logicalTypeOptions": {"minLength": 1, "maxLength": 5},
                }
            ],
            [
                (
                    "expect_column_value_lengths_to_be_between",
                    {"column": "a", "min_value": 1, "max_value": 5},
                )
            ],
        ),
        (
            [{"name": "a", "logicalType": "string", "logicalTypeOptions": {"pattern": "^x$"}}],
            [("expect_column_values_to_match_regex", {"column": "a", "regex": "^x$"})],
        ),
        (
            # ODCS v3.2 `enum` is an array of objects; the `value`s are taken.
            [{"name": "a", "enum": [{"value": "x"}, {"value": "y"}]}],
            [("expect_column_values_to_be_in_set", {"column": "a", "value_set": ["x", "y"]})],
        ),
    ],
    ids=["required", "unique", "primary-key", "compound-key", "range", "length", "pattern", "enum"],
)
def test_schema_fields_map_to_expectations(
    props: list[dict[str, Any]], expected: list[tuple[str, dict[str, Any]]]
) -> None:
    obj, unmapped = _translate(props=props)
    assert _checks(obj) == expected
    assert unmapped == []


def test_exclusive_bounds_are_strict_and_multiple_of_is_unmapped() -> None:
    obj, unmapped = _translate(
        props=[
            {
                "name": "a",
                "logicalType": "number",
                "logicalTypeOptions": {
                    "exclusiveMinimum": 0,
                    "exclusiveMaximum": 10,
                    "multipleOf": 2,
                },
            }
        ]
    )
    assert _checks(obj) == [
        (
            "expect_column_values_to_be_between",
            {
                "column": "a",
                "min_value": 0,
                "strict_min": True,
                "max_value": 10,
                "strict_max": True,
            },
        )
    ]
    assert [u.field for u in unmapped] == ["logicalTypeOptions.multipleOf"]


def test_the_object_property_list_becomes_one_declared_schema_drift_check() -> None:
    obj, _ = _translate(props=[{"name": "a", "physicalType": "integer"}, {"name": "b"}])
    drift = [c for c in obj.checks if c.expectation_type == _SCHEMA_DRIFT]
    assert len(drift) == 1


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (
            {"metric": "invalidValues", "arguments": {"validValues": ["x", "y"]}, "mustBe": 0},
            [("expect_column_values_to_be_in_set", {"column": "a", "value_set": ["x", "y"]})],
        ),
        (
            {"metric": "invalidValues", "arguments": {"pattern": "^x$"}, "mustBe": 0},
            [("expect_column_values_to_match_regex", {"column": "a", "regex": "^x$"})],
        ),
        (
            {
                "metric": "missingValues",
                "arguments": {"missingValues": [None, "", "N/A"]},
                "mustBe": 0,
            },
            [
                (
                    "expect_column_values_to_not_be_in_set",
                    {"column": "a", "value_set": ["", "N/A"]},
                ),
                ("expect_column_values_to_not_be_null", {"column": "a"}),
            ],
        ),
        (
            {"metric": "duplicateValues", "mustBe": 0},
            [("expect_column_values_to_be_unique", {"column": "a"})],
        ),
    ],
    ids=["valid-values", "pattern", "missing-values", "duplicates"],
)
def test_property_library_metrics(rule: dict[str, Any], expected: list[Any]) -> None:
    obj, unmapped = _translate(props=[{"name": "a", "quality": [rule]}])
    assert _checks(obj) == expected
    assert unmapped == []


def test_a_percent_null_bound_becomes_the_fail_threshold() -> None:
    obj, _ = _translate(
        props=[
            {
                "name": "a",
                "quality": [{"metric": "nullValues", "mustBeLessThan": 5, "unit": "percent"}],
            }
        ]
    )
    (check,) = [c for c in obj.checks if c.expectation_type != _SCHEMA_DRIFT]
    assert check.expectation_type == "expect_column_values_to_not_be_null"
    assert check.fail_threshold == Decimal("5")


def test_a_row_count_null_bound_is_unmapped_not_approximated() -> None:
    obj, unmapped = _translate(
        props=[
            {
                "name": "a",
                "quality": [{"metric": "nullValues", "mustBeLessThan": 5, "unit": "rows"}],
            }
        ]
    )
    assert _checks(obj) == []
    assert len(unmapped) == 1 and "percent" in unmapped[0].reason


def test_object_level_duplicates_on_several_properties_are_compound_unique() -> None:
    obj, _ = _translate(
        props=[{"name": "a"}, {"name": "b"}],
        quality=[
            {"metric": "duplicateValues", "arguments": {"properties": ["a", "b"]}, "mustBe": 0}
        ],
    )
    assert _checks(obj) == [("expect_compound_columns_to_be_unique", {"column_list": ["a", "b"]})]


@pytest.mark.parametrize(
    ("rule", "config"),
    [
        # One-sided: never invents a ceiling.
        ({"metric": "rowCount", "mustBeGreaterThan": 1000}, {"min_rows": 1001}),
        ({"metric": "rowCount", "mustBeGreaterOrEqualTo": 1000}, {"min_rows": 1000}),
        ({"metric": "rowCount", "mustBeLessThan": 50}, {"max_rows": 49}),
        ({"metric": "rowCount", "mustBeBetween": [10, 20]}, {"min_rows": 10, "max_rows": 20}),
    ],
    ids=["greater-than", "at-least", "less-than", "between"],
)
def test_row_count_is_a_volume_monitor(rule: dict[str, Any], config: dict[str, int]) -> None:
    obj, unmapped = _translate(quality=[rule])
    assert _checks(obj) == [("monitor:volume", config)]
    assert unmapped == []


def test_a_sql_rule_is_wrapped_so_a_null_scalar_breaches() -> None:
    obj, _ = _translate(
        quality=[{"type": "sql", "query": "SELECT COUNT(*) FROM {object}", "mustBeGreaterThan": 0}]
    )
    assert _checks(obj) == [
        (
            "unexpected_rows_expectation",
            {"unexpected_rows_query": (_WRAPPED_COUNT)},
        )
    ]


def test_a_great_expectations_custom_rule_runs_as_that_expectation() -> None:
    obj, unmapped = _translate(
        props=[{"name": "a"}],
        quality=[
            {
                "type": "custom",
                "engine": "great-expectations",
                "implementation": {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "a"},
                },
            }
        ],
    )
    assert _checks(obj) == [("expect_column_values_to_not_be_null", {"column": "a"})]
    assert unmapped == []


@pytest.mark.parametrize(
    ("rule", "reason"),
    [
        ({"type": "custom", "engine": "soda", "implementation": "checks: []"}, "not executable"),
        ({"type": "text", "description": "be good"}, "prose"),
    ],
    ids=["foreign-engine", "text"],
)
def test_rules_dataq_cannot_execute_are_unmapped(rule: dict[str, Any], reason: str) -> None:
    obj, unmapped = _translate(quality=[rule])
    assert _checks(obj) == []
    assert len(unmapped) == 1 and reason in unmapped[0].reason


def test_severity_maps_to_the_tier_and_defaults_to_fail() -> None:
    rules = [
        {"metric": "nullValues", "mustBe": 0, "severity": s}
        for s in ("info", "warning", "error", "critical", "blocker")
    ] + [{"metric": "nullValues", "mustBe": 0}]
    obj, _ = _translate(props=[{"name": "a", "quality": rules}])
    checks = [c for c in obj.checks if c.expectation_type != _SCHEMA_DRIFT]
    tiers = [
        (
            "warn"
            if c.warn_threshold is not None
            else "critical" if c.critical_threshold is not None else "fail"
        )
        for c in checks
    ]
    assert tiers == ["warn", "warn", "fail", "critical", "critical", "fail"]


def test_dimensions_map_conformity_to_validity_and_leave_coverage_unclassified() -> None:
    rules = [
        {"metric": "nullValues", "mustBe": 0, "dimension": "conformity"},
        {"metric": "nullValues", "mustBe": 0, "dimension": "coverage"},
        {"metric": "nullValues", "mustBe": 0, "dimension": "timeliness"},
    ]
    obj, _ = _translate(props=[{"name": "a", "quality": rules}])
    dims = [c.dimension for c in obj.checks if c.expectation_type != _SCHEMA_DRIFT]
    assert dims == ["validity", None, "timeliness"]


def test_latency_becomes_a_freshness_ceiling_in_hours() -> None:
    obj, unmapped = _translate(
        sla=[{"property": "latency", "value": 2, "unit": "d", "element": "orders.ts"}]
    )
    (check,) = [c for c in obj.checks if c.expectation_type != _SCHEMA_DRIFT]
    assert (check.expectation_type, check.config, check.fail_threshold) == (
        "monitor:freshness",
        {"column": "ts"},
        Decimal("48"),
    )
    assert unmapped == []


def test_latency_without_an_element_on_a_warehouse_is_unmapped() -> None:
    obj, unmapped = _translate(sla=[{"property": "latency", "value": 2, "unit": "d"}])
    assert _checks(obj) == []
    assert len(unmapped) == 1 and "element" in unmapped[0].reason


@pytest.mark.parametrize("prop", ["frequency", "retention", "availability", "timeToRepair"])
def test_sla_properties_that_are_not_a_staleness_ceiling_are_unmapped(prop: str) -> None:
    obj, unmapped = _translate(
        sla=[{"property": prop, "value": 1, "unit": "d", "element": "orders.ts"}]
    )
    assert _checks(obj) == []
    assert [u.field for u in unmapped] == [f"slaProperties.{prop}"]


def test_a_rule_level_schedule_is_unmapped_and_the_rule_still_maps() -> None:
    obj, unmapped = _translate(
        props=[
            {
                "name": "a",
                "quality": [
                    {
                        "metric": "nullValues",
                        "mustBe": 0,
                        "scheduler": "cron",
                        "schedule": "0 2 * * *",
                    }
                ],
            }
        ]
    )
    assert _checks(obj) == [("expect_column_values_to_not_be_null", {"column": "a"})]
    assert [u.field for u in unmapped] == ["schedule"]
    assert obj.schedule_cron is None


@pytest.mark.parametrize(
    "custom",
    [
        [{"property": "dataq.schedule", "value": "0 3 * * *"}],
        [{"vendor": "dataq", "property": "schedule", "value": "0 3 * * *"}],
    ],
    ids=["prefixed", "vendor"],
)
def test_the_object_schedule_comes_from_dataq_custom_properties(
    custom: list[dict[str, Any]],
) -> None:
    obj, _ = _translate(obj_extra={"customProperties": custom})
    assert obj.schedule_cron == "0 3 * * *"


def test_dataq_max_age_hours_becomes_a_freshness_monitor() -> None:
    obj, _ = _translate(
        obj_extra={
            "customProperties": [
                {"property": "dataq.maxAgeHours", "value": 6},
                {"property": "dataq.freshnessColumn", "value": "ts"},
            ]
        }
    )
    (check,) = [c for c in obj.checks if c.expectation_type != _SCHEMA_DRIFT]
    assert (check.config, check.fail_threshold) == ({"column": "ts"}, Decimal("6"))


def test_a_pii_classification_lands_on_the_suite_policy() -> None:
    obj, _ = _translate(
        props=[
            {"name": "email", "classification": "confidential"},
            {"name": "id", "classification": "public"},
        ]
    )
    assert obj.pii_columns == ["email"]


def test_a_v3_0_document_is_normalised_before_translation_and_its_spec_is_untouched() -> None:
    doc: dict[str, Any] = {
        "apiVersion": "v3.0.2",
        "kind": "DataContract",
        "id": "c1",
        "version": "1.0.0",
        "status": "active",
        "schema": [
            {
                "name": "orders",
                "physicalName": "ORDERS",
                "properties": [{"name": "a", "quality": [{"rule": "duplicateCount", "mustBe": 0}]}],
                "quality": [
                    {
                        "type": "sql",
                        "query": "SELECT COUNT(*) FROM ${object}",
                        "mustBeGreaterThan": 0,
                    }
                ],
            }
        ],
    }
    result = tr.translate(odcs.validate_document(doc).document, connection_type="postgres")
    assert _checks(result.objects[0]) == [
        ("expect_column_values_to_be_unique", {"column": "a"}),
        (
            "unexpected_rows_expectation",
            {"unexpected_rows_query": _WRAPPED_COUNT},
        ),
    ]
    assert doc["schema"][0]["properties"][0]["quality"][0] == {
        "rule": "duplicateCount",
        "mustBe": 0,
    }
    assert "${object}" in doc["schema"][0]["quality"][0]["query"]


def test_every_clause_lands_somewhere() -> None:
    """A clause is never silently dropped: one check or one unmapped entry per input."""
    obj, unmapped = _translate(
        props=[{"name": "a", "required": True, "quality": [{"type": "text", "description": "x"}]}],
        sla=[{"property": "retention", "value": 7, "unit": "y"}],
    )
    assert _checks(obj) == [("expect_column_values_to_not_be_null", {"column": "a"})]
    assert sorted(u.field for u in unmapped) == ["quality", "slaProperties.retention"]
