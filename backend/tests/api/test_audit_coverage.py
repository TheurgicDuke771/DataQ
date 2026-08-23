"""The route-table coverage guard — ADR 0041 §2.8 (#1318)."""

from __future__ import annotations

from typing import Final

from backend.app.main import app
from backend.tests.support.audit_coverage import AUDITED, EXEMPT

_MUTATING: Final[frozenset[str]] = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _served_mutating_routes() -> set[tuple[str, str]]:
    """Every mutating `/api/v1` route FastAPI serves, from the generated OpenAPI document."""
    spec = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method.upper() in _MUTATING and path.startswith("/api/v1")
    }


def test_the_guard_actually_sees_routes() -> None:
    """The guard's own guard."""
    assert len(_served_mutating_routes()) >= 40


def test_every_mutating_route_has_a_declared_audit_disposition() -> None:
    """A new mutating endpoint must be either audited or explicitly exempted."""
    declared = set(AUDITED) | set(EXEMPT)
    undeclared = _served_mutating_routes() - declared
    assert not undeclared, (
        "these mutating /api/v1 routes have no declared audit disposition: "
        f"{sorted(undeclared)} — add each to AUDITED (with its `entity.verb` action) "
        "or to EXEMPT (with the reason it is not a config mutation) in "
        "backend/tests/support/audit_coverage.py. ADR 0041 §2.8."
    )


def test_the_declaration_carries_no_routes_that_no_longer_exist() -> None:
    """The other direction, and it matters as much."""
    served = _served_mutating_routes()
    stale = (set(AUDITED) | set(EXEMPT)) - served
    assert not stale, (
        f"these declared routes are no longer served: {sorted(stale)} — remove or "
        "rename them in backend/tests/support/audit_coverage.py"
    )


def test_no_route_is_both_audited_and_exempt() -> None:
    """Two dispositions for one route is not a merge conflict a reader would spot;
    whichever dict is read last would silently win.
    """
    both = set(AUDITED) & set(EXEMPT)
    assert not both, f"declared both AUDITED and EXEMPT: {sorted(both)}"


def test_every_exemption_states_a_reason() -> None:
    """An unexplained exemption is indistinguishable from an oversight, which is
    exactly what this guard exists to make impossible. A bare truthy check would
    accept `"x"`, so the bar is a sentence.
    """
    thin = {route: reason for route, reason in EXEMPT.items() if len(reason.strip()) < 25}
    assert not thin, f"exemptions with no substantive reason: {sorted(thin)}"


def test_every_audited_route_declares_an_entity_dot_verb_action() -> None:
    """The action vocabulary is `entity.verb`, and this table doubles as the
    vocabulary — so a verb invented at a call site and not declared here shows up
    as a mismatch rather than as a new, undocumented action string in production
    data.
    """
    malformed = {
        route: action
        for route, action in AUDITED.items()
        if action.count(".") != 1 or not all(part.isidentifier() for part in action.split("."))
    }
    assert not malformed, f"actions that are not `entity.verb`: {sorted(malformed.items())}"
