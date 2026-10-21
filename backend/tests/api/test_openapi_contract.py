"""Every REST operation is described in the OpenAPI spec well enough to be a contract."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.main import app

_METHODS = ("get", "post", "put", "patch", "delete")


def _operations(spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f"{method.upper()} {path}", op)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method in _METHODS
    ]


def _typed_success(op: dict[str, Any]) -> bool:
    for code, response in op.get("responses", {}).items():
        if not code.startswith("2"):
            continue
        if code == "204":
            return True
        content = response.get("content", {})
        if content and all(media.get("schema") for media in content.values()):
            return True
    return False


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return app.openapi()


def test_the_spec_has_operations(spec: dict[str, Any]) -> None:
    assert len(_operations(spec)) > 100


def test_every_operation_has_a_summary(spec: dict[str, Any]) -> None:
    missing = [name for name, op in _operations(spec) if not op.get("summary")]
    assert missing == []


def test_every_operation_declares_a_typed_success_response(spec: dict[str, Any]) -> None:
    untyped = [name for name, op in _operations(spec) if not _typed_success(op)]
    assert untyped == []


def test_an_untyped_json_success_is_refused() -> None:
    op: dict[str, Any] = {"responses": {"200": {"content": {"application/json": {"schema": {}}}}}}
    assert not _typed_success(op)


def test_a_file_download_with_declared_media_types_passes() -> None:
    op = {"responses": {"200": {"content": {"text/csv": {"schema": {"type": "string"}}}}}}
    assert _typed_success(op)
