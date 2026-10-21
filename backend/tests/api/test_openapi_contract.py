"""Every success response in the OpenAPI spec is typed well enough for the generated client.

Summaries and tags are held by ``test_main.py::test_every_api_endpoint_has_summary_and_tags``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.app.main import app

_METHODS = ("get", "post", "put", "patch", "delete")
_CLIENT_CONFIG = Path(__file__).resolve().parents[3] / "packages/dataq-client/openapi-config.yaml"
_CONSTRAINING_KEYS = ("type", "$ref", "allOf", "anyOf", "oneOf", "enum", "const")
_PARSED_AS_JSON = "application/json"


def _operations(spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{method.upper()} {path}", op)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method in _METHODS
    ]


def _constrains(schema: dict[str, Any] | None) -> bool:
    return schema is not None and any(key in schema for key in _CONSTRAINING_KEYS)


def _untyped_successes(op: dict[str, Any]) -> list[str]:
    bad = []
    for code, response in op.get("responses", {}).items():
        if not code.startswith("2") or code == "204":
            continue
        content = response.get("content") or {}
        if not content:
            bad.append(f"{code}: no content")
        bad.extend(
            f"{code} {media}: schema constrains nothing"
            for media, body in content.items()
            if not _constrains(body.get("schema"))
        )
    return bad


def _parseable(media: str, overrides: dict[str, str]) -> bool:
    target = overrides.get(media, media)
    return target in (_PARSED_AS_JSON, "application/octet-stream") or target.startswith("text/")


def _unmapped_media(op: dict[str, Any], overrides: dict[str, str]) -> list[str]:
    """Success media the generated client would mis-read: a binary body it would decode as text
    (``BytesIO(response.text)`` raises), or a response with no media it can parse at all."""
    bad: list[str] = []
    for code, response in op.get("responses", {}).items():
        content = response.get("content") or {}
        if not code.startswith("2") or not content:
            continue
        bad.extend(
            media
            for media, body in content.items()
            if (body.get("schema") or {}).get("format") == "binary"
            and overrides.get(media) != "application/octet-stream"
        )
        if not any(_parseable(media, overrides) for media in content):
            bad.extend(content)
    return bad


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return app.openapi()


@pytest.fixture(scope="module")
def overrides() -> dict[str, str]:
    config = yaml.safe_load(_CLIENT_CONFIG.read_text())
    return dict(config.get("content_type_overrides") or {})


def test_the_spec_has_operations(spec: dict[str, Any]) -> None:
    assert len(_operations(spec)) > 100


def test_every_success_response_is_typed(spec: dict[str, Any]) -> None:
    untyped = {name: bad for name, op in _operations(spec) if (bad := _untyped_successes(op))}
    assert untyped == {}


def test_every_file_download_is_mapped_for_the_client(
    spec: dict[str, Any], overrides: dict[str, str]
) -> None:
    unmapped = {
        name: bad for name, op in _operations(spec) if (bad := _unmapped_media(op, overrides))
    }
    assert unmapped == {}


@pytest.mark.parametrize(
    "op",
    [
        {"responses": {"200": {"content": {"application/json": {"schema": {}}}}}},
        {"responses": {"200": {"content": {"application/json": {"schema": {"title": "R"}}}}}},
        {
            "responses": {
                "201": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "200": {"content": {"application/json": {"schema": {}}}},
            }
        },
        {"responses": {"200": {"description": "ok"}}},
    ],
)
def test_an_untyped_success_is_refused(op: dict[str, Any]) -> None:
    assert _untyped_successes(op)


def test_a_binary_body_the_client_would_read_as_text_is_refused() -> None:
    binary = {"schema": {"type": "string", "format": "binary"}}
    op = {"responses": {"200": {"content": {"text/csv": binary}}}}
    assert _unmapped_media(op, {}) == ["text/csv"]
    assert _unmapped_media(op, {"text/csv": "application/octet-stream"}) == []


def test_a_response_with_no_parseable_media_is_refused() -> None:
    op = {"responses": {"200": {"content": {"application/pdf": {"schema": {"type": "string"}}}}}}
    assert _unmapped_media(op, {}) == ["application/pdf"]


def test_a_text_alternate_beside_json_passes() -> None:
    op = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {"schema": {"type": "object"}},
                    "application/yaml": {"schema": {"type": "string"}},
                }
            }
        }
    }
    assert _unmapped_media(op, {}) == []
