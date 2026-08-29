"""OpenAI-compatible chat-completions provider — Azure OpenAI, Bedrock, and any
local server (Ollama / vLLM / TGI). Deliberately raw `httpx`, no vendor SDK
(ADR 0042): the wire format is three fields and the SDK would be the lock-in.
"""

from __future__ import annotations

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
            raise LLMProviderError(f"LLM endpoint refused the request ({response.status_code})")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMProviderError("LLM endpoint returned non-JSON") from exc
        if not isinstance(data, dict):
            raise LLMProviderError("LLM endpoint returned a non-object body")
        return data

    def _result(self, data: dict[str, Any]) -> LLMResult:
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("LLM response missing choices[0].message.content") from exc
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
                    "json_schema": {"name": "result", "schema": schema, "strict": True},
                },
            }
            result = self._result(self._post(payload, timeout))
            parsed = base.extract_json_object(result.text)
            base.validate_against_schema(parsed, schema)
            return LLMResult(
                text=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                parsed=parsed,
                raw=result.raw,
            )
        return self._prompt_json(
            prompt, schema=schema, system=system, max_tokens=max_tokens, timeout=timeout
        )

    def _prompt_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str | None,
        max_tokens: int,
        timeout: float,
    ) -> LLMResult:
        first = self.complete(
            f"{prompt}\n\n{base.prompt_json_instructions(schema)}",
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        try:
            parsed = base.extract_json_object(first.text)
            base.validate_against_schema(parsed, schema)
        except LLMOutputInvalidError as exc:
            second = self.complete(
                base.repair_prompt(schema, str(exc)),
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            parsed = base.extract_json_object(second.text)
            base.validate_against_schema(parsed, schema)
            return LLMResult(
                text=second.text,
                input_tokens=second.input_tokens,
                output_tokens=second.output_tokens,
                parsed=parsed,
                raw=second.raw,
            )
        return LLMResult(
            text=first.text,
            input_tokens=first.input_tokens,
            output_tokens=first.output_tokens,
            parsed=parsed,
            raw=first.raw,
        )
