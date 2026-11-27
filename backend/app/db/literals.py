"""Constants written into the statement text instead of sent as bind parameters.

A partial index is used only when the planner can prove the query implies its predicate, and an
expression index only when the query's expression matches it. Both need the constants they
compare with to be in the SQL. psycopg2 interpolates every parameter on the client, so today
they are. A server-binding driver sends ``$1``, and a generic plan proves nothing about it.
``sql_constant`` renders its value into the text under any driver, so those queries keep their
index whichever driver runs them. The index audit is
``docs/site/architecture/partial-indexes-and-the-driver.md``.

Only for the closed vocabulary an index is written against (statuses, kinds, JSON keys,
separators). A caller-supplied value must stay a bind parameter.
"""

from __future__ import annotations

from sqlalchemy import BindParameter, String, literal


def sql_constant(value: str) -> BindParameter[str]:
    return literal(value, String, literal_execute=True)
