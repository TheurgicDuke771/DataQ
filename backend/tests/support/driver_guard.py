"""The one assertion that the database driver binds parameters on the client (#1979).

Every partial and expression index the backend relies on is usable only because the pinned
driver, psycopg2, interpolates bind parameters into the statement text before it is sent: the
planner then sees constants and can prove a partial predicate or match an expression. A
server-binding driver (psycopg3) sends `$1`, a generic plan cannot prove anything, and the
affected queries fall back to other plans with every test still green, since a wrong plan is
still a correct answer.

Tests that hold such an index to account call :func:`assert_client_side_binding` first, so a
driver swap fails them with one message that points at the audit, rather than each repeating
its own check. The audit is ``docs/site/architecture/partial-indexes-and-the-driver.md``.
"""

from __future__ import annotations

from typing import Any

AUDIT = "docs/site/architecture/partial-indexes-and-the-driver.md"
CLIENT_SIDE_DRIVERS = frozenset({"psycopg2"})


def assert_client_side_binding(connection: Any) -> None:
    driver = connection.dialect.driver
    assert driver in CLIENT_SIDE_DRIVERS, (
        f"the database driver is {driver!r}, which binds parameters on the server. Partial and "
        "expression indexes are provable only against client-side-bound constants, so some "
        f"queries would silently stop using their index. Read {AUDIT} before swapping the "
        "driver: it lists each index and what to change."
    )
