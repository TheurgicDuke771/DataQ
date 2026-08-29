"""Anthropic Messages provider via the official SDK (ADR 0042).

Structured output uses forced tool-choice — one synthetic tool whose
`input_schema` IS the caller's schema — which works uniformly across Claude
model generations, unlike the newer response-format surface.
"""

from __future__ import annotations

from typing import Any

import anthropic

from backend.app.llm import base
from backend.app.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    LLMOutputInvalidError,
    LLMProviderError,
    LLMResult,
    LLMUnavailableError,
)

_STRUCTURED_TOOL_NAME = "emit_result"


class AnthropicProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self._client = client or anthropic.Anthropic(
            api_key=api_key, base_url=base_url, max_retries=1
        )

    def _create(self, timeout: float, **kwargs: Any) -> anthropic.types.Message:
        try:
            message: anthropic.types.Message = self._client.with_options(
                timeout=timeout
            ).messages.create(**kwargs)
            return message
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise LLMUnavailableError(
                f"Anthropic API unreachable: {exc.__class__.__name__}"
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 429:
                raise LLMUnavailableError(f"Anthropic API returned {exc.status_code}") from exc
            raise LLMProviderError(
                f"Anthropic API refused the request ({exc.status_code})"
            ) from exc

    @staticmethod
    def _usage(message: anthropic.types.Message) -> tuple[int | None, int | None]:
        usage = getattr(message, "usage", None)
        if usage is None:
            return None, None
        return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        message = self._create(timeout, **kwargs)
        text = "".join(block.text for block in message.content if block.type == "text")
        input_tokens, output_tokens = self._usage(message)
        return LLMResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "name": _STRUCTURED_TOOL_NAME,
                    "description": "Emit the final structured result.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": _STRUCTURED_TOOL_NAME},
        }
        if system:
            kwargs["system"] = system
        message = self._create(timeout, **kwargs)
        parsed: dict[str, Any] | None = None
        for block in message.content:
            if block.type == "tool_use" and block.name == _STRUCTURED_TOOL_NAME:
                if isinstance(block.input, dict):
                    parsed = block.input
                break
        if parsed is None:
            raise LLMOutputInvalidError("model returned no structured tool output")
        base.validate_against_schema(parsed, schema)
        input_tokens, output_tokens = self._usage(message)
        return LLMResult(
            text="", input_tokens=input_tokens, output_tokens=output_tokens, parsed=parsed
        )
