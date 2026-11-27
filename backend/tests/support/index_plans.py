"""Ask the planner whether it CAN use an index, not whether it prefers it on a toss-up (#2545).

On a near-empty table two indexes cost about the same, so which one `EXPLAIN` names depends on
the statistics earlier tests left behind. A test that asserts one particular index was used then
passes alone and fails after the full suite. Dropping the table's other non-unique indexes inside
the test's own transaction (rolled back with it) leaves the planner only that index to choose.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

_RIVALS = text(
    "SELECT other.indexrelid::regclass::text FROM pg_index AS target "
    "JOIN pg_index AS other ON other.indrelid = target.indrelid "
    "WHERE target.indexrelid = CAST(:index AS regclass) "
    "AND other.indexrelid <> target.indexrelid "
    "AND NOT other.indisunique AND NOT other.indisprimary"
)


def drop_rival_indexes(connection: Any, index: str) -> list[str]:
    """Drop every other non-unique index on ``index``'s table, in the open transaction.

    Returns the names dropped. The caller's transaction must be rolled back afterwards, which the
    ``db_session`` fixture does.
    """
    rivals = list(connection.execute(_RIVALS, {"index": index}).scalars())
    for rival in rivals:
        connection.execute(text(f"DROP INDEX {rival}"))
    return rivals
