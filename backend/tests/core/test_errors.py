"""`jsonable_errors` — the validation-envelope sanitizer (#371)."""

import json
from typing import Any

from backend.app.core.errors import jsonable_errors


def _dumps(value: Any) -> str:
    return json.dumps(value)  # raises TypeError if anything non-JSON survives


def test_plain_scalars_pass_through() -> None:
    for value in (None, "s", 7, 1.5, True):
        assert jsonable_errors(value) == value
    _dumps(jsonable_errors({"a": [1, "x", None, False]}))


def test_exception_object_in_ctx_is_stringified() -> None:
    err = {
        "type": "value_error",
        "loc": ("body",),
        "msg": "Value error, NUL not allowed",
        "input": {"name": "evil-\x00"},
        "ctx": {"error": ValueError("NUL not allowed")},
    }
    out = jsonable_errors([err])
    _dumps(out)
    assert out[0]["ctx"]["error"] == "NUL not allowed"
    assert out[0]["loc"] == ["body"]  # tuple → list


def test_sets_and_nonstring_keys_are_coerced() -> None:
    out = jsonable_errors({1: {"s": {frozenset({"a"})}}, "b": (2, 3)})
    _dumps(out)
    assert out["1"]["s"] == [["a"]]
    assert out["b"] == [2, 3]


def test_arbitrary_object_is_stringified() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird!"

    out = jsonable_errors({"x": Weird()})
    _dumps(out)
    assert out == {"x": "weird!"}
