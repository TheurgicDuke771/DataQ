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

Narrow, but not naive — the distinction cost a review round. It flags HTTP verbs
that always mutate, and `get`/`request` only when the first argument is
**URL-shaped**, which means all three of the ways this suite spells a URL:

    client.get("/api/v1/suites")          # a literal
    client.get(f"/api/v1/suites/{sid}")   # an f-string
    limiter.get(PROBE)                    # a module-level string constant

The first cut recognised only the literal, so it passed green while 59 real
instances — more than the 83 it had just fixed — sat untouched in the same files,
some of them the sibling lines of calls that *were* hoisted. A detector that
matches only the shape you happened to think of reports "clean" for the same
reason it found nothing.

It stays narrow where narrowness is correct: `dict.get(key)` and
`session.get(Model, pk)` are pure, and their first argument resolves to no URL,
so neither is flagged. A guard that cries wolf is a guard someone eventually
silences.
"""

from __future__ import annotations

import ast
import pathlib

_TESTS_ROOT = pathlib.Path(__file__).resolve().parent

# Verbs that mutate whatever they are called on, whatever the receiver.
_MUTATING = frozenset({"post", "put", "patch", "delete"})
# Verbs that are pure on a mapping but I/O on a client — told apart by the argument.
_URL_SHAPED = frozenset({"get", "request"})


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "…"`` bindings, so `limiter.get(PROBE)` resolves."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = node.value.value
    return out


def _starts_a_path(value: object) -> bool:
    return isinstance(value, str) and value.startswith("/")


def _looks_like_a_url(call: ast.Call, constants: dict[str, str]) -> bool:
    first = call.args[0] if call.args else None
    if isinstance(first, ast.Constant):
        return _starts_a_path(first.value)
    if isinstance(first, ast.JoinedStr) and first.values:
        # An f-string carries its path prefix in the leading literal chunk.
        head = first.values[0]
        return isinstance(head, ast.Constant) and _starts_a_path(head.value)
    if isinstance(first, ast.Name):
        return _starts_a_path(constants.get(first.id))
    return False


def _is_side_effecting(node: ast.AST, constants: dict[str, str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr in _MUTATING
            or (node.func.attr in _URL_SHAPED and _looks_like_a_url(node, constants))
        )
    )


def test_no_test_hides_a_request_inside_an_assert() -> None:
    offenders: list[str] = []
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — a broken file fails elsewhere, loudly
            continue
        constants = _module_string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            if any(_is_side_effecting(sub, constants) for sub in ast.walk(node.test)):
                rel = path.relative_to(_TESTS_ROOT.parent.parent)
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "these asserts perform the request they are asserting about, so `python -O` "
        "would delete both and the test would pass while doing nothing:\n  "
        + "\n  ".join(offenders)
        + "\n\nHoist the call: `resp = client.post(...)` then `assert resp.status_code == ...`."
    )
