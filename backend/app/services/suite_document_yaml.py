"""A suite document written as YAML (#1688) — parsed into the same plain mapping the
JSON document is, then validated by the same models. Only what JSON can express is
accepted, so a YAML document and its JSON twin always mean the same thing.
"""

from __future__ import annotations

import math
import re
from typing import Any

import yaml

from backend.app.core.errors import DataQError

#: Characters, not bytes. Far above any real suite document and small enough that
#: parsing one cannot hold a worker.
MAX_YAML_CHARS = 1_000_000


class SuiteDocumentYamlInvalidError(DataQError):
    status_code = 422
    code = "suite_document_yaml_invalid"


_BOOL, _INT, _FLOAT = (f"tag:yaml.org,2002:{t}" for t in ("bool", "int", "float"))
_KEPT_TAGS = frozenset({"tag:yaml.org,2002:null"})


def _json_scalar_resolvers() -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    kept = {
        first: [(tag, pattern) for tag, pattern in resolvers if tag in _KEPT_TAGS]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    typed = (
        (_BOOL, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), "tTfF"),
        (_INT, re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"), "-+0123456789"),
        # No leading zeros: `010` is an identifier someone wrote, not ten.
        (
            _FLOAT,
            re.compile(r"^[-+]?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$"),
            "-+0123456789.",
        ),
    )
    for tag, pattern, first_chars in typed:
        for first in first_chars:
            kept.setdefault(first, []).append((tag, pattern))
    return kept


class _DocumentLoader(yaml.SafeLoader):
    """`SafeLoader` with JSON's scalar rules instead of YAML 1.1's. Under 1.1 an unquoted
    `NO` or `off` is a boolean, `2026-01-01` a date and `1:30` the integer 90 — each a
    value the author did not write. Here only `true`/`false`, plain decimal numbers and
    `null` are typed; everything else is text.
    """

    # PyYAML's own class-level registry; replaced here, never mutated in place.
    yaml_implicit_resolvers = _json_scalar_resolvers()


class _DocumentDumper(yaml.SafeDumper):
    """Quotes exactly the strings `_DocumentLoader` would otherwise type, so a dumped
    document reads back unchanged (stock `SafeDumper` leaves the string `1e3` bare).
    """

    yaml_implicit_resolvers = _json_scalar_resolvers()


def _position(exc: yaml.YAMLError) -> dict[str, int]:
    mark = getattr(exc, "problem_mark", None)
    return {"line": mark.line + 1, "column": mark.column + 1} if mark is not None else {}


def _reject_aliases(text: str) -> None:
    # `safe_load` expands aliases, so a few nested anchors can describe gigabytes. A suite
    # document has no use for them.
    for event in yaml.parse(text, Loader=_DocumentLoader):
        if isinstance(event, yaml.AliasEvent):
            mark = event.start_mark
            raise SuiteDocumentYamlInvalidError(
                "YAML anchors and aliases (&name / *name) are not supported in a suite document",
                detail={"line": mark.line + 1, "column": mark.column + 1} if mark else {},
            )


def _check_json_compatible(value: Any, path: str) -> None:
    """Refuse what YAML can still express and JSON cannot: an explicit tag
    (`!!binary`, `!!set`, `!!timestamp`), a non-string key, a number too large to be
    finite, a NUL inside a string.
    """
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SuiteDocumentYamlInvalidError(
                f"{path}: NaN and infinity are not allowed", detail={"path": path}
            )
        return
    if isinstance(value, str):
        # A "\0" escape in a quoted scalar becomes a real NUL here, after the request
        # body's own NUL check has already run.
        if "\x00" in value:
            raise SuiteDocumentYamlInvalidError(
                f"{path}: NUL (\\x00) characters are not allowed", detail={"path": path}
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_json_compatible(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SuiteDocumentYamlInvalidError(
                    f"{path or 'document'}: mapping keys must be strings (quote {key!r})",
                    detail={"path": path},
                )
            _check_json_compatible(key, path)
            _check_json_compatible(item, f"{path}.{key}" if path else key)
        return
    raise SuiteDocumentYamlInvalidError(
        f"{path}: a {type(value).__name__} value is not supported — quote it to pass it as text",
        detail={"path": path},
    )


def parse_suite_document_yaml(text: str) -> dict[str, Any]:
    """YAML text -> the plain mapping a JSON suite document would be. Raises
    `SuiteDocumentYamlInvalidError` (422); never echoes document content.
    """
    if len(text) > MAX_YAML_CHARS:
        raise SuiteDocumentYamlInvalidError(
            f"the YAML document is longer than {MAX_YAML_CHARS} characters",
            detail={"max_chars": MAX_YAML_CHARS},
        )
    try:
        _reject_aliases(text)
        # Bandit B506 flags any explicit Loader; this one subclasses SafeLoader.
        document = yaml.load(text, Loader=_DocumentLoader)  # noqa: S506  # nosec B506
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or "could not be parsed"
        raise SuiteDocumentYamlInvalidError(
            f"the document is not valid YAML: {problem}", detail=_position(exc)
        ) from exc
    if not isinstance(document, dict):
        raise SuiteDocumentYamlInvalidError(
            "the YAML document must be a mapping with name, version and checks"
        )
    _check_json_compatible(document, "")
    return document


def dump_suite_document_yaml(document: dict[str, Any]) -> str:
    """A JSON-shaped suite document as YAML, keys in the document's own order."""
    return yaml.dump(
        document,
        Dumper=_DocumentDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
