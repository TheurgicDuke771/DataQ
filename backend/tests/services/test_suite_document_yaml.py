"""The YAML suite-document parser (#1688): JSON's scalar rules, and refusal of
everything YAML can express that a JSON document cannot."""

from __future__ import annotations

import pytest
import yaml

from backend.app.services.suite_document_yaml import (
    MAX_YAML_CHARS,
    MAX_YAML_DEPTH,
    SuiteDocumentYamlInvalidError,
    dump_suite_document_yaml,
    parse_suite_document_yaml,
)


def test_parses_a_document_into_the_json_shape() -> None:
    doc = parse_suite_document_yaml("""
version: 1
name: Orders quality
checks:
  - name: id not null
    expectation_type: expect_column_values_to_not_be_null
    config:
      column: id
    fail_threshold: 7.5
""")
    assert doc == {
        "version": 1,
        "name": "Orders quality",
        "checks": [
            {
                "name": "id not null",
                "expectation_type": "expect_column_values_to_not_be_null",
                "config": {"column": "id"},
                "fail_threshold": 7.5,
            }
        ],
    }


@pytest.mark.parametrize(
    ("written", "value"),
    [
        # YAML 1.1 would make each of these a boolean, a date, a base-60 or octal number.
        ("NO", "NO"),
        ("yes", "yes"),
        ("off", "off"),
        ("2026-01-01", "2026-01-01"),
        ("1:30", "1:30"),
        ("010", "010"),
        ("0x1F", "0x1F"),
        (".nan", ".nan"),
        # What JSON types, typed the same way.
        ("true", True),
        ("False", False),
        ("42", 42),
        ("-2", -2),
        ("1.5", 1.5),
        ("1e3", 1000.0),
        ("null", None),
        ("~", None),
        ('"42"', "42"),
    ],
)
def test_scalars_follow_json_rules_not_yaml_1_1(written: str, value: object) -> None:
    assert parse_suite_document_yaml(f"v: {written}") == {"v": value}


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("a: &x [1]\nb: *x", "anchors and aliases"),
        ("a: !!binary aGk=", "bytes value is not supported"),
        ("a: !!timestamp 2026-01-01", "date value is not supported"),
        ("a: !!set {x, y}", "set value is not supported"),
        ("a: !!python/object/apply:os.system ['id']", "not valid YAML"),
        ("- 1\n- 2", "must be a mapping"),
        ("just text", "must be a mapping"),
        ("", "must be a mapping"),
        ("a: [1", "not valid YAML"),
        ("1: x", "mapping keys must be strings"),
        ("a: {2: x}", "mapping keys must be strings"),
        ('a: "x\\0y"', "NUL"),
        ('"k\\0": 1', "NUL"),
        ("a: 1e999", "NaN and infinity"),
    ],
)
def test_refuses_what_json_cannot_express(text: str, fragment: str) -> None:
    with pytest.raises(SuiteDocumentYamlInvalidError) as excinfo:
        parse_suite_document_yaml(text)
    assert fragment in excinfo.value.message
    assert excinfo.value.status_code == 422


def test_an_alias_bomb_is_refused_before_it_is_expanded() -> None:
    levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 30):
        levels.append(f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]")
    with pytest.raises(SuiteDocumentYamlInvalidError, match="anchors and aliases"):
        parse_suite_document_yaml("\n".join(levels))


def test_a_syntax_error_reports_its_position_and_no_content() -> None:
    with pytest.raises(SuiteDocumentYamlInvalidError) as excinfo:
        parse_suite_document_yaml("name: ok\nsecret_value: [unclosed")
    assert excinfo.value.detail == {"line": 2, "column": 24}
    assert "unclosed" not in excinfo.value.message


def test_an_oversized_document_is_refused_unparsed() -> None:
    with pytest.raises(SuiteDocumentYamlInvalidError, match="longer than"):
        parse_suite_document_yaml("a: " + "x" * MAX_YAML_CHARS)


def test_deep_nesting_is_refused_instead_of_exhausting_the_stack() -> None:
    with pytest.raises(SuiteDocumentYamlInvalidError, match="levels deep") as excinfo:
        parse_suite_document_yaml("a: " + "[" * 5000 + "]" * 5000)
    assert excinfo.value.status_code == 422


def test_nesting_a_real_document_needs_is_accepted() -> None:
    nested = "a: " + "[" * (MAX_YAML_DEPTH - 1) + "]" * (MAX_YAML_DEPTH - 1)
    assert "a" in parse_suite_document_yaml(nested)


def test_an_integer_past_the_interpreter_digit_limit_is_refused() -> None:
    with pytest.raises(SuiteDocumentYamlInvalidError, match="too large to read"):
        parse_suite_document_yaml("version: " + "9" * 5000)


def test_a_dumped_document_means_the_same_to_a_stock_yaml_1_1_reader() -> None:
    """Another tool may load and rewrite the file. If `no` or `0x1F` were left bare it
    would hand back `false` and `31`, and the re-import would store the changed value."""
    document = {
        "name": "no",
        "checks": [{"config": {"value_set": ["no", "2026-01-01", "1:30", ".inf", "0x1F", "1_0"]}}],
    }
    dumped = dump_suite_document_yaml(document)

    assert yaml.safe_load(dumped) == document
    assert parse_suite_document_yaml(dumped) == document


def test_dump_then_parse_is_the_identity_on_awkward_strings() -> None:
    document = {
        "version": 1,
        "name": "NO",
        "checks": [
            {
                "config": {
                    "value_set": ["yes", "010", "2026-01-01", "1:30", "null", "", "1e3", "true"],
                    "min_value": 1000.0,
                }
            }
        ],
    }
    assert parse_suite_document_yaml(dump_suite_document_yaml(document)) == document
