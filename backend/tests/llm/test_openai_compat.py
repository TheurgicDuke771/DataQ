"""OpenAI-compat provider against a mock transport (ADR 0042). The live Ollama
lane (#1631) is the driver-boundary evidence; these prove OUR handling of each
response class.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from backend.app.llm.base import (
    LLMOutputInvalidError,
    LLMProviderError,
    LLMUnavailableError,
    extract_json_object,
)
from backend.app.llm.openai_compat import OpenAICompatProvider

SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
    "additionalProperties": False,
}


def _chat_response(
    content: str, *, prompt_tokens: int = 10, completion_tokens: int = 5
) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _provider(
    handler: Any, *, api_key: str | None = None, structured_output: str = "native"
) -> OpenAICompatProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(
        base_url="http://llm.local/v1",
        model="test-model",
        api_key=api_key,
        structured_output=structured_output,
        client=client,
    )


def test_complete_returns_text_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["messages"][-1] == {"role": "user", "content": "hi"}
        return httpx.Response(200, json=_chat_response("hello"))

    result = _provider(handler).complete("hi")
    assert result.text == "hello"
    assert result.input_tokens == 10
    assert result.output_tokens == 5


def test_api_key_sent_as_both_bearer_and_azure_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        seen["api-key"] = request.headers.get("api-key", "")
        return httpx.Response(200, json=_chat_response("ok"))

    _provider(handler, api_key="sk-test").complete("hi")
    assert seen["authorization"] == "Bearer sk-test"
    assert seen["api-key"] == "sk-test"


def test_no_key_sends_no_auth_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json=_chat_response("ok"))

    _provider(handler).complete("hi")


def test_5xx_maps_to_unavailable_and_4xx_to_provider_error() -> None:
    with pytest.raises(LLMUnavailableError):
        _provider(lambda _r: httpx.Response(503, text="down")).complete("hi")
    with pytest.raises(LLMProviderError):
        _provider(lambda _r: httpx.Response(401, text="bad key")).complete("hi")


def test_connect_error_maps_to_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailableError):
        _provider(handler).complete("hi")


def test_structured_native_sends_response_format_and_validates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"] == SCHEMA
        return httpx.Response(200, json=_chat_response('{"sql": "SELECT 1"}'))

    result = _provider(handler).complete_structured("gen", schema=SCHEMA)
    assert result.parsed == {"sql": "SELECT 1"}


def test_structured_native_rejects_schema_violating_output() -> None:
    handler = lambda _r: httpx.Response(200, json=_chat_response('{"nope": 1}'))  # noqa: E731
    with pytest.raises(LLMOutputInvalidError):
        _provider(handler).complete_structured("gen", schema=SCHEMA)


def test_prompt_json_mode_embeds_schema_and_parses_fenced_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "JSON schema" in body["messages"][-1]["content"]
        assert "response_format" not in body
        return httpx.Response(200, json=_chat_response('```json\n{"sql": "SELECT 1"}\n```'))

    result = _provider(handler, structured_output="prompt_json").complete_structured(
        "gen", schema=SCHEMA
    )
    assert result.parsed == {"sql": "SELECT 1"}


def test_prompt_json_repairs_once_then_fails() -> None:
    calls: list[str] = []

    def repairing(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["messages"][-1]["content"])
        if len(calls) == 1:
            return httpx.Response(200, json=_chat_response("not json at all"))
        return httpx.Response(200, json=_chat_response('{"sql": "SELECT 2"}'))

    result = _provider(repairing, structured_output="prompt_json").complete_structured(
        "gen", schema=SCHEMA
    )
    assert result.parsed == {"sql": "SELECT 2"}
    assert len(calls) == 2
    assert "not valid against the schema" in calls[1]

    def always_bad(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response("still not json"))

    with pytest.raises(LLMOutputInvalidError):
        _provider(always_bad, structured_output="prompt_json").complete_structured(
            "gen", schema=SCHEMA
        )


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here it is: {"a": 1} — done.', {"a": 1}),
    ],
)
def test_extract_json_object_shapes(text: str, expected: dict[str, Any]) -> None:
    assert extract_json_object(text) == expected


def test_extract_json_object_rejects_non_object() -> None:
    with pytest.raises(LLMOutputInvalidError):
        extract_json_object("[1, 2]")
