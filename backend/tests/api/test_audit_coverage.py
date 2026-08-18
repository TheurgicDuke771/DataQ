"""The route-table coverage guard — ADR 0041 §2.8 (#1318).

An explicit `audit_service` call at each mutation is the right mechanism, but a
new endpoint that forgets it is invisible. This test makes that invisibility
impossible: it enumerates what FastAPI **actually serves** and asserts every
mutating `/api/v1` route has a declared disposition in
`tests/support/audit_coverage`.

Enumerating the served surface rather than an audit registry is the load-bearing
part. ADR 0039's orphan-secret sweep shipped an introspection guard that iterated
*the models already registered with it* — so a new model was invisible to the very
check meant to catch it. A route appears in the served surface whether or not
anyone remembered the audit, so a new endpoint arrives here as a failing test
demanding a decision.
"""

from __future__ import annotations

from typing import Final

from backend.app.main import app
from backend.tests.support.audit_coverage import AUDITED, EXEMPT

_MUTATING: Final[frozenset[str]] = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _served_mutating_routes() -> set[tuple[str, str]]:
    """Every mutating `/api/v1` route FastAPI serves, from the generated OpenAPI
    document.

    The OpenAPI document is used rather than `app.routes` because the app mounts
    its routers lazily — `app.routes` carries wrapper objects, not the endpoints —
    so walking it would have quietly returned an EMPTY set, and a guard that
    enumerates nothing passes every assertion in this file. That failure mode was
    hit while writing this test, which is why it is written down.
    """
    spec = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method.upper() in _MUTATING and path.startswith("/api/v1")
    }


def test_the_guard_actually_sees_routes() -> None:
    """The guard's own guard.

    Every other assertion in this file is vacuously true against an empty set, so
    the enumeration is checked before it is trusted. The floor is deliberately a
    round number well below the real count rather than the exact one, so that
    ADDING a route does not fail this particular test — the coverage test below is
    the one that should fail, with a message that says what to do.
    """
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
    """The other direction, and it matters as much.

    A stale row is worse than a missing one: it makes the table look complete
    while describing a surface that has moved. A renamed route would otherwise
    leave its old entry sitting there, still declaring "audited", with nothing
    behind it.
    """
    served = _served_mutating_routes()
    stale = (set(AUDITED) | set(EXEMPT)) - served
    assert not stale, (
        f"these declared routes are no longer served: {sorted(stale)} — remove or "
        "rename them in backend/tests/support/audit_coverage.py"
    )


def test_no_route_is_both_audited_and_exempt() -> None:
    """Two dispositions for one route is not a merge conflict a reader would spot;
    whichever dict is read last would silently win."""
    both = set(AUDITED) & set(EXEMPT)
    assert not both, f"declared both AUDITED and EXEMPT: {sorted(both)}"


def test_every_exemption_states_a_reason() -> None:
    """An unexplained exemption is indistinguishable from an oversight, which is
    exactly what this guard exists to make impossible. A bare truthy check would
    accept `"x"`, so the bar is a sentence."""
    thin = {route: reason for route, reason in EXEMPT.items() if len(reason.strip()) < 25}
    assert not thin, f"exemptions with no substantive reason: {sorted(thin)}"


def test_every_audited_route_declares_an_entity_dot_verb_action() -> None:
    """The action vocabulary is `entity.verb`, and this table doubles as the
    vocabulary — so a verb invented at a call site and not declared here shows up
    as a mismatch rather than as a new, undocumented action string in production
    data."""
    malformed = {
        route: action
        for route, action in AUDITED.items()
        if action.count(".") != 1 or not all(part.isidentifier() for part in action.split("."))
    }
    assert not malformed, f"actions that are not `entity.verb`: {sorted(malformed.items())}"
