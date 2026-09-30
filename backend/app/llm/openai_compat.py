"""OpenAI-compatible chat-completions provider — Azure OpenAI, Bedrock, and any
local server (Ollama / vLLM / TGI). Deliberately raw `httpx`, no vendor SDK
(ADR 0042): the wire format is three fields and the SDK would be the lock-in.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from backend.app.llm import base
from backend.app.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    LLMOutputInvalidError,
    LLMProviderError,
    LLMResult,
    LLMUnavailableError,
)

_CONNECT_TIMEOUT_SECONDS = 10.0
_INLINE_REASONING_RE = re.compile(r"\A\s*<reasoning>.*?</reasoning>", re.DOTALL)


#: What an untyped array item (`{}`) may be under strict mode, which requires a `type` everywhere.
_ANY_SCALAR = ["string", "number", "integer", "boolean"]
_PROVIDER_ERROR_MAX_CHARS = 300


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` rewritten for OpenAI strict structured output (#2295): every object lists all of
    its properties in `required`, the originally optional ones become nullable, and every array
    item has a `type`. `drop_strict_nulls` undoes the nullability on the way back.
    """
    out = dict(schema)
    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        required = set(out.get("required", []))
        props: dict[str, Any] = {}
        for key, sub in out["properties"].items():
            converted = strict_schema(sub)
            props[key] = converted if key in required else _nullable(converted)
        out["properties"] = props
        out["required"] = list(props)
        out["additionalProperties"] = False
    if out.get("type") == "array":
        items = out.get("items")
        out["items"] = strict_schema(items) if items else {"type": _ANY_SCALAR}
    return out


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    out = dict(schema)
    kind = out.get("type")
    if isinstance(kind, list):
        out["type"] = [*kind, "null"] if "null" not in kind else kind
    elif kind is not None:
        out["type"] = [kind, "null"]
    if "enum" in out and None not in out["enum"]:
        out["enum"] = [*out["enum"], None]
    return out


def drop_strict_nulls(value: Any, schema: dict[str, Any]) -> Any:
    """Remove the nulls strict mode made the model emit for fields `schema` leaves optional."""
    if isinstance(value, dict) and schema.get("type") == "object":
        props = schema.get("properties") or {}
        required = set(schema.get("required", []))
        return {
            k: drop_strict_nulls(v, props.get(k, {}))
            for k, v in value.items()
            if not (v is None and k not in required)
        }
    if isinstance(value, list) and schema.get("type") == "array":
        return [drop_strict_nulls(v, schema.get("items") or {}) for v in value]
    return value


def _provider_error(response: httpx.Response) -> str:
    """The provider's own reason for a 4xx: it describes our request (e.g. a schema it rejects),
    and a bare status code made #2295 diagnosable only outside DataQ."""
    try:
        detail = response.json().get("error", {}).get("message")
    except (ValueError, AttributeError):
        detail = None
    suffix = f": {str(detail)[:_PROVIDER_ERROR_MAX_CHARS]}" if detail else ""
    return f"LLM endpoint refused the request ({response.status_code}){suffix}"


def _no_answer(finish_reason: Any) -> LLMOutputInvalidError:
    # Reasoning with no answer is a bad OUTPUT, not a broken provider: retryable, and usually
    # a token budget spent thinking.
    cut = " — the token budget ran out while reasoning" if finish_reason == "length" else ""
    return LLMOutputInvalidError(f"LLM response has no answer text{cut}")


def _content_text(content: Any, *, finish_reason: Any = None) -> str:
    """The answer text of ``message.content``: a string, or a list of typed parts.

    Reasoning models (gpt-oss on Databricks model serving, and OpenAI's own newer formats)
    return ``[{"type": "reasoning", ...}, {"type": "text", "text": "..."}]``; only the ``text``
    parts are the answer — the reasoning is never returned as if it were. Bedrock's gpt-oss
    puts it inline instead, as ``<reasoning>...</reasoning>answer``.
    """
    if isinstance(content, str):
        if not content.lstrip().startswith("<reasoning>"):
            return content
        answer = _INLINE_REASONING_RE.sub("", content, count=1)
        if answer == content or not answer.strip():
            raise _no_answer(finish_reason)
        return answer.lstrip()
    if isinstance(content, list):
        parts = [
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ]
        if parts:
            return "".join(parts)
        raise _no_answer(finish_reason)
    raise LLMProviderError("LLM response content is neither text nor a list of parts")


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        structured_output: str = "native",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._structured_output = structured_output
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
            # Azure OpenAI ignores Authorization and reads api-key; sending both is harmless
            # to every other server and spares a per-vendor auth knob.
            headers["api-key"] = self._api_key
        return headers

    def _post(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        timeouts = httpx.Timeout(timeout, connect=_CONNECT_TIMEOUT_SECONDS)
        try:
            if self._client is not None:
                response = self._client.post(
                    url, json=payload, headers=self._headers(), timeout=timeouts
                )
            else:
                with httpx.Client(timeout=timeouts) as client:
                    response = client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"LLM endpoint unreachable: {exc.__class__.__name__}"
            ) from exc
        if response.status_code >= 500:
            raise LLMUnavailableError(f"LLM endpoint returned {response.status_code}")
        if response.status_code >= 400:
            raise LLMProviderError(_provider_error(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMProviderError("LLM endpoint returned non-JSON") from exc
        if not isinstance(data, dict):
            raise LLMProviderError("LLM endpoint returned a non-object body")
        return data

    def _result(self, data: dict[str, Any]) -> LLMResult:
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("LLM response missing choices[0].message.content") from exc
        text = _content_text(content, finish_reason=choice.get("finish_reason"))
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw=data,
        )

    @staticmethod
    def _messages(prompt: str, system: str | None) -> list[dict[str, str]]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": self._messages(prompt, system),
        }
        return self._result(self._post(payload, timeout))

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult:
        if self._structured_output == "native":
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": self._messages(prompt, system),
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "result",
                        "schema": strict_schema(schema),
                        "strict": True,
                    },
                },
            }
            result = self._result(self._post(payload, timeout))
            parsed = drop_strict_nulls(base.extract_json_object(result.text), schema)
            base.validate_against_schema(parsed, schema)
            return LLMResult(
                text=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                parsed=parsed,
                raw=result.raw,
            )
        return base.complete_with_prompt_json(
            self, prompt, schema=schema, system=system, max_tokens=max_tokens, timeout=timeout
        )
