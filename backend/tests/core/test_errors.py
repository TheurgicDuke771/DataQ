"""`_jsonable` — the validation-envelope sanitizer (#371).

Pydantic's `exc.errors()` is not JSON-clean: `ctx` carries the live exception a
model_validator raised and `input` echoes the raw payload. Before the fix, a
model-validator `ValueError` made the 422 handler itself crash
(`TypeError: Object of type ValueError is not JSON serializable` → 500). The
contract: whatever shape `errors()` takes, the result of `_jsonable` must
survive `json.dumps` unchanged in structure, with plain scalars passed through
and everything else stringified.
"""

import json
from typing import Any

from backend.app.core.errors import _jsonable


def _dumps(value: Any) -> str:
    return json.dumps(value)  # raises TypeError if anything non-JSON survives


def test_plain_scalars_pass_through() -> None:
    for value in (None, "s", 7, 1.5, True):
        assert _jsonable(value) == value
    _dumps(_jsonable({"a": [1, "x", None, False]}))


def test_exception_object_in_ctx_is_stringified() -> None:
    err = {
        "type": "value_error",
        "loc": ("body",),
        "msg": "Value error, NUL not allowed",
        "input": {"name": "evil-\x00"},
        "ctx": {"error": ValueError("NUL not allowed")},
    }
    out = _jsonable([err])
    _dumps(out)
    assert out[0]["ctx"]["error"] == "NUL not allowed"
    assert out[0]["loc"] == ["body"]  # tuple → list


def test_sets_and_nonstring_keys_are_coerced() -> None:
    out = _jsonable({1: {"s": {frozenset({"a"})}}, "b": (2, 3)})
    _dumps(out)
    assert out["1"]["s"] == [["a"]]
    assert out["b"] == [2, 3]


def test_arbitrary_object_is_stringified() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird!"

    out = _jsonable({"x": Weird()})
    _dumps(out)
    assert out == {"x": "weird!"}
