"""Case-insensitive LIKE matching with the user's text kept literal.

The escaper and the `escape=` the predicate is built with live together here, so a `%`, `_` or
`\\` typed by a user can never turn back into a wildcard at one call site and not another.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute

_ESCAPE = "\\"

#: Anything LIKE-able that yields text: a mapped column or an expression such as `coalesce()`.
SqlText = ColumnElement[str] | InstrumentedAttribute[str]


def escape_like(term: str) -> str:
    r"""Escape LIKE wildcards so a literal `%` / `_` / `\` stays literal."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains_ci(column: SqlText, term: str) -> ColumnElement[bool]:
    """`column ILIKE '%term%'`, `term` taken literally."""
    return column.ilike(f"%{escape_like(term)}%", escape=_ESCAPE)


def starts_with_ci(column: SqlText, term: str) -> ColumnElement[bool]:
    """`column ILIKE 'term%'`, `term` taken literally."""
    return column.ilike(f"{escape_like(term)}%", escape=_ESCAPE)
