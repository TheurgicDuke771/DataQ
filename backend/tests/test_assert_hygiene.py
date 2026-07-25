"""No side-effecting call may live inside an `assert` (#787).

`python -O` strips assert statements **entirely** — not just the check, but
everything inside them. So

    assert client.post("/api/v1/suites", json=payload).status_code == 201

loses the assertion *and the request*. The test doesn't fail; it stops doing
anything at all, while still reporting green. That is worse than a wrong
assertion, because a wrong assertion eventually fails on something.

This is CodeQL's `py/side-effect-in-assert` shape (cf. #545). It is guarded here
as a plain test rather than by turning on the CodeQL query, for two reasons: it
runs on every `pytest` (so a contributor sees it before pushing, not after), and
it names the exact file and line instead of a security-tab entry.

Deliberately narrow. It flags HTTP verbs that always mutate, and `get`/`request`
only when the first argument looks like a URL path — so `dict.get(...)` and
`Mock.get(...)`, which are pure, don't produce noise that would get the whole
check suppressed. A guard people silence is worse than no guard.
"""

from __future__ import annotations

import ast
import pathlib

_TESTS_ROOT = pathlib.Path(__file__).resolve().parent

# Verbs that mutate whatever they are called on, whatever the receiver.
_MUTATING = frozenset({"post", "put", "patch", "delete"})
# Verbs that are pure on a mapping but I/O on a client — told apart by the argument.
_URL_SHAPED = frozenset({"get", "request"})


def _looks_like_a_url(call: ast.Call) -> bool:
    first = call.args[0] if call.args else None
    return (
        isinstance(first, ast.Constant)
        and isinstance(first.value, str)
        and first.value.startswith("/")
    )


def _is_side_effecting(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr in _MUTATING
            or (node.func.attr in _URL_SHAPED and _looks_like_a_url(node))
        )
    )


def test_no_test_hides_a_request_inside_an_assert() -> None:
    offenders: list[str] = []
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — a broken file fails elsewhere, loudly
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            if any(_is_side_effecting(sub) for sub in ast.walk(node.test)):
                rel = path.relative_to(_TESTS_ROOT.parent.parent)
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "these asserts perform the request they are asserting about, so `python -O` "
        "would delete both and the test would pass while doing nothing:\n  "
        + "\n  ".join(offenders)
        + "\n\nHoist the call: `resp = client.post(...)` then `assert resp.status_code == ...`."
    )
