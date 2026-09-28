"""Snowflake Cortex provider — ``SNOWFLAKE.CORTEX.COMPLETE`` run over a Snowflake connection.

The model runs inside the Snowflake account the data already lives in, under that
connection's own credential: no API key of its own, and nothing sent anywhere the
connection does not already reach. SQL rather than the Cortex REST API because
Snowflake gates REST per account while the SQL function is available wherever
Cortex is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import exc as sa_exc
from sqlalchemy import text

from backend.app.llm import base
from backend.app.llm.base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    LLMOutputInvalidError,
    LLMProviderError,
    LLMResult,
    LLMUnavailableError,
)

_COMPLETE = text(
    "SELECT SNOWFLAKE.CORTEX.COMPLETE(:model, PARSE_JSON(:messages), PARSE_JSON(:options))"
)
#: `Request failed for external function COMPLETE with remote service error: '400 'unknown model'`
_REMOTE_ERROR_RE = re.compile(r"remote service error: '(\d{3}) '(.{1,200}?)''")
_STATEMENT_TIMEOUT_ERRNO = 630
_REMOTE_MESSAGE_MAX = 200


class CortexProvider:
    def __init__(
        self,
        *,
        model: str,
        open_connection: Callable[[], AbstractContextManager[Any]],
        structured_output: str = "native",
    ) -> None:
        self.model = model
        self._open_connection = open_connection
        self._structured_output = structured_output

    def _call(self, messages: list[dict[str, str]], options: dict[str, Any], timeout: float) -> Any:
        try:
            with self._open_connection() as conn:
                # A literal int: ALTER SESSION takes no bind.
                conn.execute(
                    text(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {max(1, int(timeout))}")
                )
                raw = conn.execute(
                    _COMPLETE,
                    {
                        "model": self.model,
                        "messages": json.dumps(messages),
                        "options": json.dumps(options),
                    },
                ).scalar()
        except sa_exc.DBAPIError as exc:
            raise _classify(exc) from exc
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise LLMProviderError("Cortex returned non-JSON") from exc
        if not isinstance(raw, dict):
            raise LLMProviderError("Cortex returned a non-object response")
        return raw

    @staticmethod
    def _messages(prompt: str, system: str | None) -> list[dict[str, str]]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _usage(data: dict[str, Any]) -> tuple[int | None, int | None]:
        usage = data.get("usage") or {}
        return usage.get("prompt_tokens"), usage.get("completion_tokens")

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult:
        data = self._call(self._messages(prompt, system), {"max_tokens": max_tokens}, timeout)
        try:
            answer = data["choices"][0]["messages"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Cortex response missing choices[0].messages") from exc
        if not isinstance(answer, str):
            raise LLMProviderError("Cortex response text is not a string")
        input_tokens, output_tokens = self._usage(data)
        return LLMResult(
            text=answer, input_tokens=input_tokens, output_tokens=output_tokens, raw=data
        )

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResult:
        if self._structured_output != "native":
            return base.complete_with_prompt_json(
                self, prompt, schema=schema, system=system, max_tokens=max_tokens, timeout=timeout
            )
        options = {"max_tokens": max_tokens, "response_format": {"type": "json", "schema": schema}}
        data = self._call(self._messages(prompt, system), options, timeout)
        try:
            parsed = data["structured_output"][0]["raw_message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMOutputInvalidError("Cortex returned no structured output") from exc
        if isinstance(parsed, str):
            parsed = base.extract_json_object(parsed)
        if not isinstance(parsed, dict):
            raise LLMOutputInvalidError("Cortex structured output is not a JSON object")
        base.validate_against_schema(parsed, schema)
        input_tokens, output_tokens = self._usage(data)
        return LLMResult(
            text=json.dumps(parsed),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            parsed=parsed,
            raw=data,
        )


def _classify(exc: sa_exc.DBAPIError) -> Exception:
    """Outage vs refusal, never the driver's message — except Cortex's own short reason."""
    orig = exc.orig
    remote = _REMOTE_ERROR_RE.search(str(orig))
    if remote:
        status, reason = int(remote.group(1)), remote.group(2)[:_REMOTE_MESSAGE_MAX]
        if status >= 500:
            return LLMUnavailableError(f"Cortex returned {status}")
        return LLMProviderError(f"Cortex refused the request ({status}): {reason}")
    if getattr(orig, "errno", None) == _STATEMENT_TIMEOUT_ERRNO:
        return LLMUnavailableError("Cortex call timed out")
    if isinstance(exc, sa_exc.OperationalError | sa_exc.InterfaceError):
        return LLMUnavailableError(f"Snowflake unreachable: {type(orig).__name__}")
    return LLMProviderError(f"Snowflake refused the Cortex call: {type(orig).__name__}")
