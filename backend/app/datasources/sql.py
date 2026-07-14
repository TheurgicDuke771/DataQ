"""The shared SQL-identifier allowlist (#428).

SQL identifiers have no bind-parameter slot, so every path that interpolates a
user-supplied table/schema/column into a query string (monitor config, the
profiler's query builders) validates against this one decision first — widening
it (e.g. quoted/unicode identifiers) then happens everywhere at once. Callers
keep their own error types; the shared piece is the decision, not the failure
shape.
"""

from __future__ import annotations

import re
from typing import Final

# The Snowflake/Databricks unquoted-identifier set: letter/underscore lead, then
# word chars or `$`. Private — go through `is_sql_identifier`, whose isinstance
# guard keeps a non-string config value (JSON ints/lists/None) a clean rejection
# instead of a re TypeError at the match call.
_SQL_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def is_sql_identifier(name: object) -> bool:
    """True when ``name`` is a plain SQL identifier safe to interpolate.

    Anything else (spaces, quotes, dots, a trailing newline, non-strings) is
    refused — it can't be made injection-safe by quoting alone at the call sites
    that use this. ``fullmatch``, not ``match`` + ``$``: the ``$`` anchor accepts
    one trailing ``\\n``, which has no business in an identifier.
    """
    return isinstance(name, str) and bool(_SQL_IDENTIFIER_RE.fullmatch(name))
