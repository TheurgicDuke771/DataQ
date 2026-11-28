"""Ask the planner whether it CAN use an index, not whether it prefers it on a toss-up (#2545).

On a near-empty table two indexes cost about the same, so which one `EXPLAIN` names depends on
the statistics earlier tests left behind. A test that asserts one particular index was used then
passes alone and fails after the full suite. Dropping the table's other non-primary indexes (the
unique ones only when asked) inside the test's own transaction, which is rolled back with it, leaves
the planner only that index to choose.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

# Each rival, with the constraint behind it if any: a constraint's index is dropped with it.
_RIVALS = text(
    "SELECT other.indexrelid::regclass::text, other.indrelid::regclass::text, "
    "quote_ident(con.conname) "
    "FROM pg_index AS target "
    "JOIN pg_index AS other ON other.indrelid = target.indrelid "
    "LEFT JOIN pg_constraint AS con ON con.conindid = other.indexrelid "
    "AND con.conrelid = other.indrelid AND con.contype IN ('u', 'x') "
    "WHERE target.indexrelid = CAST(:index AS regclass) "
    "AND other.indexrelid <> target.indexrelid AND NOT other.indisprimary "
    "AND (CAST(:unique AS boolean) OR NOT other.indisunique)"
)


def drop_rival_indexes(connection: Any, index: str, *, unique: bool = False) -> list[str]:
    """Drop every other non-unique index on ``index``'s table, in the open transaction; with
    ``unique``, the unique ones too (and the constraint behind each, with what depends on it).
    The primary key always stays.

    Returns the names dropped. The caller's transaction must be rolled back afterwards, which the
    ``db_session`` fixture does.
    """
    rivals = connection.execute(_RIVALS, {"index": index, "unique": unique}).all()
    for rival, table, constraint in rivals:
        if constraint is None:
            connection.execute(text(f"DROP INDEX {rival} CASCADE"))
        else:
            connection.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {constraint} CASCADE"))
    return [rival for rival, _, _ in rivals]
