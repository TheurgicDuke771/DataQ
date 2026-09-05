"""Every door membership is enforced at, declared as DATA — ADR 0043.

A bare list of names catches a door being *added* without a gate; it does not
catch one added with the WRONG gate, because a name in the wrong comment group
looks exactly like a name in the right one. Each row here says which credential
the door accepts and how to exercise it with a real one, and the sweeps in
`tests/core/test_membership_gates.py` are driven off the rows.

Adding a row is adding a test. A fifth credential kind that does not appear here
is a door nothing sweeps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: Credential kinds, so a sweep can assert every one is covered rather than
#: trusting that the rows happen to span them.
CREDENTIALS = ("identity", "session", "pat")

#: Surfaces a door is reachable from.
SURFACES = ("rest", "mcp")


@dataclass(frozen=True)
class Door:
    """One place a credential turns into a principal."""

    name: str
    #: What the caller presents.
    credential: str
    #: Where it is presented.
    surface: str
    #: Admit `email`, mint the credential, and return something `exercise` uses.
    #: `None` when the door needs no set-up beyond the membership row.
    setup: Callable[..., Any] | None
    #: Present the credential; must raise once the address is no longer a member.
    exercise: Callable[..., Any]


def credentials_covered(doors: list[Door]) -> set[str]:
    return {door.credential for door in doors}


def surfaces_covered(doors: list[Door]) -> set[str]:
    return {door.surface for door in doors}
