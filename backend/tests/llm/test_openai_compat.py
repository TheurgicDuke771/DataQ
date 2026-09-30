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
from backend.app.llm.openai_compat import OpenAICompatProvider, strict_schema

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
        assert body["response_format"]["json_schema"]["schema"] == strict_schema(SCHEMA)
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
    # The cost record must carry BOTH paid rounds, not just the repair.
    assert (result.input_tokens, result.output_tokens) == (20, 10)

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


# The exact shape gpt-oss returns on Databricks model serving (captured live, 2026-09-28).
_REASONING_PARTS = [
    {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": 'The user wants reply exactly "OK".'}],
    },
    {"type": "text", "text": "OK"},
]


def _parts_response(content: Any) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": {}}


def test_a_content_part_list_returns_only_the_text_parts() -> None:
    """Reasoning models return typed parts; the reasoning must never read as the answer."""
    result = _provider(
        lambda _r: httpx.Response(200, json=_parts_response(_REASONING_PARTS))
    ).complete("hi")
    assert result.text == "OK"


def test_structured_output_parses_from_the_text_part() -> None:
    parts = [
        {"type": "reasoning", "summary": []},
        {"type": "text", "text": '{"sql": "SELECT 1"}'},
    ]
    result = _provider(
        lambda _r: httpx.Response(200, json=_parts_response(parts))
    ).complete_structured("q", schema=SCHEMA)
    assert result.parsed == {"sql": "SELECT 1"}


def test_reasoning_with_no_answer_is_a_retryable_output_error() -> None:
    """A budget spent thinking is a bad OUTPUT, not a broken provider — `_prompt_json` repairs
    on `LLMOutputInvalidError`, and the admin must not be told the endpoint is misconfigured."""
    body = _parts_response([{"type": "reasoning", "summary": []}])
    body["choices"][0]["finish_reason"] = "length"
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(LLMOutputInvalidError, match="ran out while reasoning"):
        provider.complete("hi")


@pytest.mark.parametrize("content", [{"text": "not a list"}, 42])
def test_content_of_an_unknown_shape_is_a_provider_error(content: Any) -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=_parts_response(content)))
    with pytest.raises(LLMProviderError):
        provider.complete("hi")


# The exact shapes gpt-oss returns on Bedrock's /openai/v1 (captured live, 2026-09-29): the
# reasoning is inline in the string, and it drafts the JSON before the real answer.
_BEDROCK_PLAIN = '<reasoning>User says: "Reply with ok". Probably just respond "ok".</reasoning>ok'
_BEDROCK_JSON = (
    '<reasoning>We should produce:\n\n{\n  "sql": "SELECT 0"\n}\n\n'
    'Just output that JSON.</reasoning>{"sql":"SELECT 1"}'
)
_BEDROCK_FENCED = '<reasoning>Answer with JSON.</reasoning>```{\n  "sql": "SELECT 1"\n}'


def test_inline_reasoning_is_never_returned_as_the_answer() -> None:
    result = _provider(
        lambda _r: httpx.Response(200, json=_chat_response(_BEDROCK_PLAIN))
    ).complete("hi")
    assert result.text == "ok"


@pytest.mark.parametrize("content", [_BEDROCK_JSON, _BEDROCK_FENCED])
def test_structured_output_parses_the_answer_after_inline_reasoning(content: str) -> None:
    result = _provider(
        lambda _r: httpx.Response(200, json=_chat_response(content))
    ).complete_structured("q", schema=SCHEMA)
    assert result.parsed == {"sql": "SELECT 1"}


@pytest.mark.parametrize(
    "content", ["<reasoning>thinking, and cut off", "<reasoning>done thinking</reasoning>  "]
)
def test_inline_reasoning_with_no_answer_is_a_retryable_output_error(content: str) -> None:
    body = _chat_response(content)
    body["choices"][0]["finish_reason"] = "length"
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(LLMOutputInvalidError, match="ran out while reasoning"):
        provider.complete("hi")


# ── strict structured output (#2295) ─────────────────────────────────────────


def _strict_violations(schema: Any, where: str = "$") -> list[str]:
    """OpenAI strict mode's structural rules: an object lists every property in `required` and
    forbids extras; every array item, and every property, has a `type`."""
    found: list[str] = []
    if not isinstance(schema, dict):
        return found
    if "type" not in schema:
        found.append(f"{where}: no type")
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        if set(schema.get("required", [])) != set(props):
            found.append(f"{where}: required != properties")
        if schema.get("additionalProperties") is not False:
            found.append(f"{where}: additionalProperties not false")
        for key, sub in props.items():
            found += _strict_violations(sub, f"{where}.{key}")
    if schema.get("type") == "array" or "items" in schema:
        found += _strict_violations(schema.get("items"), f"{where}[]")
    return found


def _feature_schemas() -> dict[str, dict[str, Any]]:
    from backend.app.services import llm_checksuggest, llm_rca, llm_sqlgen

    return {
        "sqlgen": llm_sqlgen.SQLGEN_SCHEMA,
        "checksuggest": llm_checksuggest.CHECKSUGGEST_SCHEMA,
        "checksuggest+freshness": llm_checksuggest._build_schema(include_freshness=True),
        "rca": llm_rca.RCA_SCHEMA,
    }


@pytest.mark.parametrize("name", sorted(_feature_schemas()))
def test_every_feature_schema_is_sent_strict_mode_valid(name: str) -> None:
    """Azure OpenAI 400'd check suggestions and RCA in native mode: `value_set.items` had no
    type, and RCA's `required` omitted `suggested_next_checks`."""
    assert _strict_violations(strict_schema(_feature_schemas()[name])) == []


def test_native_mode_sends_the_strict_schema_and_drops_the_nulls_it_forces() -> None:
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "next": {"type": "array", "items": {}}},
        "required": ["summary"],
        "additionalProperties": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        assert sent == strict_schema(schema)
        assert sent["required"] == ["summary", "next"]
        assert sent["properties"]["next"]["type"] == ["array", "null"]
        return httpx.Response(200, json=_chat_response('{"summary": "s", "next": null}'))

    result = _provider(handler).complete_structured("rca", schema=schema)
    assert result.parsed == {"summary": "s"}


def test_a_provider_refusal_names_the_providers_reason() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "Invalid schema for response_format 'result'"}}
        )

    with pytest.raises(LLMProviderError, match=r"\(400\): Invalid schema for response_format"):
        _provider(handler).complete_structured("gen", schema=SCHEMA)
