"""Load and validate an ODCS document; the `apiVersion` policy (ADR 0052 §5)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft201909Validator

from backend.app.core.errors import DataQError
from backend.app.services.suite_document_yaml import (
    SuiteDocumentYamlInvalidError,
    parse_suite_document_yaml,
)

SCHEMA_DIR = Path(__file__).parent / "schema"
#: Vendored ODCS minors, oldest first. Bumping = add the file + a tuple entry (README there).
KNOWN_VERSIONS: tuple[str, ...] = ("v3.0.0", "v3.0.1", "v3.0.2", "v3.1.0", "v3.2.0")
NEWEST_VERSION = KNOWN_VERSIONS[-1]
#: Where Bitol documents the v2 → v3 field moves.
MIGRATION_NOTES_URL = "https://bitol-io.github.io/open-data-contract-standard/latest/changelog/"

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
#: Problems reported per refused document — enough to fix, never the whole echo.
_MAX_PROBLEMS = 20


class ContractDocumentInvalidError(DataQError):
    status_code = 422
    code = "contract_document_invalid"


class ContractApiVersionUnsupportedError(DataQError):
    status_code = 422
    code = "contract_api_version_unsupported"


@dataclass(frozen=True)
class ValidatedDocument:
    """A parsed document that passed the schema for its `apiVersion`."""

    document: dict[str, Any]
    api_version: str
    #: The vendored schema it was validated against (differs from `api_version` on a newer minor).
    schema_version: str
    warnings: list[str] = field(default_factory=list)


def load_document(*, document: dict[str, Any] | None, document_yaml: str | None) -> dict[str, Any]:
    """The plain mapping, from JSON (already a dict) or YAML text — exactly one of the two."""
    if (document is None) == (document_yaml is None):
        raise ContractDocumentInvalidError("send exactly one of document or document_yaml")
    if document is not None:
        return document
    try:
        return parse_suite_document_yaml(document_yaml or "")
    except SuiteDocumentYamlInvalidError as exc:
        raise ContractDocumentInvalidError(exc.message, detail=exc.detail) from exc


def parse_version(api_version: Any) -> tuple[int, int, int]:
    if not isinstance(api_version, str) or (m := _VERSION_RE.match(api_version)) is None:
        raise ContractApiVersionUnsupportedError(
            "apiVersion must be an ODCS version string such as 'v3.2.0'",
            detail={"api_version": api_version if isinstance(api_version, str) else None},
        )
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def schema_version_for(api_version: str) -> tuple[str, str | None]:
    """`(vendored schema version, warning)` for a document's `apiVersion`, or raise.

    v3.0.0 … the newest vendored minor validate against their own file; a newer v3 minor
    against the newest file with a warning (ODCS minors are additive); v2 and v4+ are refused.
    """
    major, minor, patch = parse_version(api_version)
    if major < 3:
        raise ContractApiVersionUnsupportedError(
            f"apiVersion {api_version} is an ODCS v2 document; DataQ reads v3.0.0 and later — "
            f"see Bitol's migration notes: {MIGRATION_NOTES_URL}",
            detail={"api_version": api_version, "supported": list(KNOWN_VERSIONS)},
        )
    if major > 3:
        raise ContractApiVersionUnsupportedError(
            f"apiVersion {api_version} is a newer ODCS major than DataQ's mapping was written "
            f"for (v3); it is refused until the mapping is reviewed against it",
            detail={"api_version": api_version, "supported": list(KNOWN_VERSIONS)},
        )
    if api_version in KNOWN_VERSIONS:
        return api_version, None
    newest = parse_version(NEWEST_VERSION)
    if (major, minor, patch) > newest:
        return NEWEST_VERSION, (
            f"apiVersion {api_version} is newer than the newest ODCS schema DataQ ships "
            f"({NEWEST_VERSION}); validated against {NEWEST_VERSION} — fields it does not know "
            "are preserved but not enforced"
        )
    # A v3 patch between two vendored files (none exists today): the next known minor up.
    for known in KNOWN_VERSIONS:
        if parse_version(known) > (major, minor, patch):
            return known, f"apiVersion {api_version} validated against {known}"
    return NEWEST_VERSION, None  # pragma: no cover — every branch above returns


@cache
def _validator(schema_version: str) -> Draft201909Validator:
    with (SCHEMA_DIR / f"odcs-json-schema-{schema_version}.json").open(encoding="utf-8") as fh:
        schema = json.load(fh)
    Draft201909Validator.check_schema(schema)
    return Draft201909Validator(schema)


def _path(error: jsonschema.ValidationError) -> str:
    out = "document"
    for part in error.absolute_path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def validate_document(document: dict[str, Any]) -> ValidatedDocument:
    """Validate against the vendored schema for the document's `apiVersion` (422 on failure)."""
    if not isinstance(document, dict):
        raise ContractDocumentInvalidError("the contract document must be a mapping")
    api_version = document.get("apiVersion")
    parse_version(api_version)  # a non-string or malformed apiVersion is refused here
    assert isinstance(api_version, str)
    schema_version, warning = schema_version_for(api_version)
    # The schema's own `apiVersion` enum stops at the version it ships with; a newer minor is
    # validated with that one field substituted, which is exactly the leniency the policy grants.
    candidate = (
        document if schema_version == api_version else {**document, "apiVersion": schema_version}
    )
    errors = sorted(_validator(schema_version).iter_errors(candidate), key=lambda e: e.path)
    if errors:
        problems = [{"path": _path(e), "message": e.message[:300]} for e in errors[:_MAX_PROBLEMS]]
        raise ContractDocumentInvalidError(
            f"the document is not a valid ODCS {schema_version} contract — "
            f"{problems[0]['path']}: {problems[0]['message']}",
            detail={
                "schema_version": schema_version,
                "problem_count": len(errors),
                "problems": problems,
            },
        )
    return ValidatedDocument(
        document=document,
        api_version=api_version,
        schema_version=schema_version,
        warnings=[warning] if warning else [],
    )
