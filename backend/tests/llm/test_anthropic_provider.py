"""Anthropic provider with a stub SDK client — error mapping + structured
tool-forcing. What the real API returns crosses a driver boundary and is
covered by an operator's own credential, not this suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from backend.app.llm.anthropic_provider import AnthropicProvider
from backend.app.llm.base import (
    LLMOutputInvalidError,
    LLMProviderError,
    LLMUnavailableError,
)

SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
    "additionalProperties": False,
}


class _StubMessages:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _StubClient:
    def __init__(self, outcome: Any) -> None:
        self.messages = _StubMessages(outcome)

    def with_options(self, **_kwargs: Any) -> _StubClient:
        return self


def _provider(outcome: Any) -> tuple[AnthropicProvider, _StubClient]:
    stub = _StubClient(outcome)
    return AnthropicProvider(model="claude-x", api_key="k", client=stub), stub  # type: ignore[arg-type]


def _message(blocks: list[Any], usage: Any = None) -> Any:
    return SimpleNamespace(content=blocks, usage=usage)


def _status_error(status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, json={"error": {"message": "x"}})
    return anthropic.APIStatusError("boom", response=response, body=None)


def test_complete_joins_text_blocks_and_usage() -> None:
    message = _message(
        [
            SimpleNamespace(type="text", text="a"),
            SimpleNamespace(type="tool_use", name="x", input={}),
            SimpleNamespace(type="text", text="b"),
        ],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    )
    provider, stub = _provider(message)
    result = provider.complete("hi", system="sys")
    assert result.text == "ab"
    assert (result.input_tokens, result.output_tokens) == (7, 3)
    assert stub.messages.calls[0]["system"] == "sys"


def test_structured_forces_tool_choice_and_validates() -> None:
    message = _message(
        [SimpleNamespace(type="tool_use", name="emit_result", input={"sql": "SELECT 1"})]
    )
    provider, stub = _provider(message)
    result = provider.complete_structured("gen", schema=SCHEMA)
    assert result.parsed == {"sql": "SELECT 1"}
    call = stub.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "emit_result"}
    assert call["tools"][0]["input_schema"] == SCHEMA


def test_structured_without_tool_block_is_invalid_output() -> None:
    provider, _ = _provider(_message([SimpleNamespace(type="text", text="no tool")]))
    with pytest.raises(LLMOutputInvalidError):
        provider.complete_structured("gen", schema=SCHEMA)


def test_structured_schema_violation_is_invalid_output() -> None:
    provider, _ = _provider(
        _message([SimpleNamespace(type="tool_use", name="emit_result", input={"nope": 1})])
    )
    with pytest.raises(LLMOutputInvalidError):
        provider.complete_structured("gen", schema=SCHEMA)


def test_connection_error_maps_to_unavailable() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    provider, _ = _provider(anthropic.APIConnectionError(request=request))
    with pytest.raises(LLMUnavailableError):
        provider.complete("hi")


@pytest.mark.parametrize(
    "status,exc", [(500, LLMUnavailableError), (429, LLMUnavailableError), (401, LLMProviderError)]
)
def test_status_error_mapping(status: int, exc: type[Exception]) -> None:
    provider, _ = _provider(_status_error(status))
    with pytest.raises(exc):
        provider.complete("hi")
