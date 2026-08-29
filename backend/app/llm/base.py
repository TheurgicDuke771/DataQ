"""`LLMProvider` protocol, result shape, and the seam's error taxonomy (ADR 0042).

Error types are the contract: callers branch on TYPE, never message (the ADR 0039
lesson) — "not configured", "provider outage", and "bad output" are three
different states and none may be folded into another.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import jsonschema

from backend.app.core.errors import DataQError

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_TOKENS = 4096


class LLMNotConfiguredError(DataQError):
    status_code = 409
    code = "llm_not_configured"


class LLMProviderError(DataQError):
    """The provider answered with a non-transport failure (auth, bad model, 4xx)."""

    status_code = 502
    code = "llm_provider_error"


class LLMUnavailableError(DataQError):
    """Transport-level outage (unreachable, timeout, 5xx) — never a config state."""

    status_code = 502
    code = "llm_provider_unavailable"


class LLMOutputInvalidError(DataQError):
    """The model's output failed schema validation after the repair round."""

    status_code = 502
    code = "llm_output_invalid"


class LLMRequestInvalidError(DataQError):
    """The request's own context is unusable (targetless suite, non-SQL
    connection, suite deleted) — the model was never called. Deliberately not
    `LLMOutputInvalidError`: blaming the model for a context defect sends the
    user to the wrong fix.
    """

    status_code = 422
    code = "llm_request_invalid"


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    parsed: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@runtime_checkable
class LLMProvider(Protocol):
    model: str

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult: ...

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult: ...


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object out of model text (bare, fenced, or embedded)."""
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE_RE.findall(text)]
    brace = text.find("{")
    if brace != -1:
        candidates.append(text[brace : text.rfind("}") + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMOutputInvalidError("model output contained no parseable JSON object")


def validate_against_schema(parsed: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as exc:
        raise LLMOutputInvalidError(
            f"model output failed schema validation: {exc.message}"
        ) from exc


def prompt_json_instructions(schema: dict[str, Any]) -> str:
    return (
        "Respond with a single JSON object and nothing else — no prose, no code fences. "
        "It must conform exactly to this JSON schema:\n" + json.dumps(schema, sort_keys=True)
    )


def repair_prompt(schema: dict[str, Any], error: str) -> str:
    return (
        f"Your previous response was not valid against the schema ({error}). "
        "Respond again with ONLY the corrected JSON object.\n" + prompt_json_instructions(schema)
    )
