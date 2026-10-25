"""ODCS apiVersion policy and schema validation (ADR 0052 §5)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.contracts import odcs


def _doc(api: Any, **extra: Any) -> dict[str, Any]:
    return {
        "apiVersion": api,
        "kind": "DataContract",
        "id": "c1",
        "version": "1.0.0",
        "status": "active",
        **extra,
    }


@pytest.mark.parametrize("api", odcs.KNOWN_VERSIONS)
def test_every_vendored_v3_minor_validates_against_its_own_schema(api: str) -> None:
    result = odcs.validate_document(_doc(api))
    assert result.schema_version == api
    assert result.warnings == []


def test_a_newer_v3_minor_is_accepted_with_a_warning() -> None:
    result = odcs.validate_document(_doc("v3.9.0"))
    assert result.schema_version == odcs.NEWEST_VERSION
    assert len(result.warnings) == 1


@pytest.mark.parametrize("api", ["v2.2.2", "v4.0.0"])
def test_other_majors_are_refused(api: str) -> None:
    with pytest.raises(odcs.ContractApiVersionUnsupportedError):
        odcs.validate_document(_doc(api))


@pytest.mark.parametrize("api", [None, 3, "3.2.0", "v3.2"])
def test_a_malformed_api_version_is_refused(api: Any) -> None:
    with pytest.raises(odcs.ContractApiVersionUnsupportedError):
        odcs.validate_document(_doc(api))


def test_a_schema_violation_names_where() -> None:
    with pytest.raises(odcs.ContractDocumentInvalidError) as excinfo:
        odcs.validate_document({"apiVersion": "v3.2.0", "kind": "DataContract", "version": "1"})
    assert "id" in str(excinfo.value)


def test_yaml_is_parsed_safely() -> None:
    doc = odcs.load_document(
        document=None, document_yaml="apiVersion: v3.2.0\nkind: DataContract\n"
    )
    assert doc["kind"] == "DataContract"
    with pytest.raises(odcs.ContractDocumentInvalidError):
        odcs.load_document(document=None, document_yaml="!!python/object/apply:os.system ['true']")
