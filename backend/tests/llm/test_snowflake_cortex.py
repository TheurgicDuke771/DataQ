"""Snowflake Cortex provider (#1655) — the COMPLETE wire shape and its error taxonomy."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import exc as sa_exc

from backend.app.llm.base import LLMOutputInvalidError, LLMProviderError, LLMUnavailableError
from backend.app.llm.snowflake_cortex import CortexProvider

_SCHEMA = {
    "type": "object",
    "properties": {"column": {"type": "string"}},
    "required": ["column"],
    "additionalProperties": False,
}


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeConn:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(clause), dict(params or {})))
        if "ALTER SESSION" in str(clause):
            return _Result(None)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Result(answer)


def _provider(answers: list[Any], mode: str = "native") -> tuple[CortexProvider, _FakeConn]:
    conn = _FakeConn(answers)

    @contextlib.contextmanager
    def _open() -> Iterator[_FakeConn]:
        yield conn

    return CortexProvider(model="llama3.1-70b", open_connection=_open, structured_output=mode), conn


def _plain(text: str) -> str:
    return json.dumps(
        {"choices": [{"messages": text}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
    )


def _completes(conn: _FakeConn) -> list[dict[str, Any]]:
    return [params for sql, params in conn.calls if "CORTEX.COMPLETE" in sql]


def test_complete_binds_model_messages_and_options_and_caps_the_statement() -> None:
    provider, conn = _provider([_plain("OK")])
    result = provider.complete("hi", system="be terse", max_tokens=16, timeout=20.9)

    assert (result.text, result.input_tokens, result.output_tokens) == ("OK", 7, 2)
    assert conn.calls[0][0] == "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 20"
    sql, params = conn.calls[1]
    assert "PARSE_JSON(:messages)" in sql and "hi" not in sql
    assert params["model"] == "llama3.1-70b"
    assert json.loads(params["messages"]) == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]
    assert json.loads(params["options"]) == {"max_tokens": 16}


def test_native_structured_output_asks_for_the_schema_and_validates_it() -> None:
    answer = {
        "structured_output": [{"raw_message": {"column": "email"}, "type": "json"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 4},
    }
    provider, conn = _provider([json.dumps(answer)])
    result = provider.complete_structured("which?", schema=_SCHEMA)

    assert result.parsed == {"column": "email"}
    assert (result.input_tokens, result.output_tokens) == (5, 4)
    options = json.loads(_completes(conn)[0]["options"])
    assert options["response_format"] == {"type": "json", "schema": _SCHEMA}


@pytest.mark.parametrize(
    "answer",
    [
        {"structured_output": [{"raw_message": {"col": "x"}}]},  # schema violation
        {"choices": [{"messages": "no structure"}]},  # no structured_output at all
    ],
)
def test_native_structured_output_that_misses_the_schema_is_invalid_output(
    answer: dict[str, Any],
) -> None:
    provider, _ = _provider([json.dumps(answer)])
    with pytest.raises(LLMOutputInvalidError):
        provider.complete_structured("which?", schema=_SCHEMA)


def test_prompt_json_mode_repairs_once_through_plain_completions() -> None:
    provider, conn = _provider([_plain("not json"), _plain('{"column": "email"}')], "prompt_json")
    result = provider.complete_structured("which?", schema=_SCHEMA)

    assert result.parsed == {"column": "email"}
    assert result.input_tokens == 14  # both paid rounds
    assert all("response_format" not in json.loads(p["options"]) for p in _completes(conn))


def _db_error(kind: type[sa_exc.DBAPIError], message: str, errno: int | None = None) -> Exception:
    orig = Exception(message)
    orig.errno = errno  # type: ignore[attr-defined]
    return kind("SELECT …", {}, orig)


@pytest.mark.parametrize(
    ("error", "expected", "fragment"),
    [
        (
            _db_error(
                sa_exc.ProgrammingError,
                "512513 (P0000): Request failed for external function COMPLETE with remote "
                "service error: '400 'unknown model \"nope\"''; requests batch-id: 01c7",
            ),
            LLMProviderError,
            'Cortex refused the request (400): unknown model "nope"',
        ),
        (
            _db_error(
                sa_exc.ProgrammingError,
                "remote service error: '503 'overloaded''; requests batch-id: 1",
            ),
            LLMUnavailableError,
            "Cortex returned 503",
        ),
        (
            _db_error(
                sa_exc.ProgrammingError,
                "remote service error: '429 'too many requests''; requests batch-id: 1",
            ),
            LLMUnavailableError,
            "Cortex returned 429",
        ),
        (
            _db_error(sa_exc.ProgrammingError, "000630: Statement reached its timeout", 630),
            LLMUnavailableError,
            "timed out",
        ),
        (
            _db_error(sa_exc.OperationalError, "could not connect to host.internal"),
            LLMUnavailableError,
            "Snowflake unreachable",
        ),
        (
            _db_error(sa_exc.ProgrammingError, "002003: Object 'SNOWFLAKE.CORTEX' does not exist"),
            LLMProviderError,
            "Snowflake refused the Cortex call",
        ),
    ],
)
def test_driver_errors_are_classified_without_leaking_the_driver_message(
    error: Exception, expected: type[Exception], fragment: str
) -> None:
    provider, _ = _provider([error])
    with pytest.raises(expected) as exc_info:
        provider.complete("hi")
    message = str(exc_info.value)
    assert fragment in message
    assert "host.internal" not in message and "batch-id" not in message
    assert "does not exist" not in message


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", None])
def test_a_malformed_response_is_a_provider_error(raw: Any) -> None:
    provider, _ = _provider([raw])
    with pytest.raises(LLMProviderError):
        provider.complete("hi")
