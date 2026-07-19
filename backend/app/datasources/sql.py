"""Shared SQL-datasource primitives: the identifier allowlist (#428) and the
lazy engine lifecycle (#427).

SQL identifiers have no bind-parameter slot, so every path that interpolates a
user-supplied table/schema/column into a query string (monitor config, the
profiler's query builders) validates against this one decision first — widening
it (e.g. quoted/unicode identifiers) then happens everywhere at once. Callers
keep their own error types; the shared piece is the decision, not the failure
shape.

`LazyEngine` is the one build-once/dispose-once engine holder the SQL runners
(Snowflake / Unity Catalog) share — only the URL/connect-args factory varies per
runner, so lifecycle fixes (pre-ping, dispose semantics, guards) land in one
place instead of drifting between copies. No import cycle: this module knows
nothing about monitors or runners.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from sqlalchemy.sql import TableClause

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


def core_table(*, table: str, schema: str | None, catalog: str | None) -> TableClause:
    """A Core table clause for ``[catalog.][schema.]table`` — the dialect quotes it.

    The one construction shared by the profiler's query builders and the monitor
    engine (#476). Going through Core rather than f-string interpolation is what
    makes a **mixed-case identifier** work at all: the compiler quotes anything
    that isn't already lower-case (``"Amount"``) and leaves a lower-case name bare
    so the warehouse folds it as it always has (``order_ts`` → ``ORDER_TS`` on
    Snowflake). That rule is also why this is behaviour-preserving for every
    identifier that works today.

    It must be Core and not hand-rolled quoting because the quote character is
    **dialect-specific** — Snowflake uses ``"``, Databricks/Unity Catalog uses
    backticks and reads ``"..."`` as a string literal — and this module is shared
    by both.

    With a ``catalog`` the namespace is ``catalog.schema`` passed as an *unquoted*
    ``quoted_name``, so the dialect emits three dotted parts rather than quoting
    the dotted string as one identifier. That one spot is raw interpolation, hence
    the allowlist check on every part here rather than trusting callers (they
    validate first for a good error message; this is the injection guarantee).

    A ``catalog`` with no ``schema`` is refused: dropping the ``None`` schema would
    emit a 2-part ``catalog.table``, which Unity Catalog resolves as
    ``schema.table`` — a wrong object rather than an error.
    """
    from sqlalchemy import table as table_clause
    from sqlalchemy.sql import quoted_name

    if catalog is not None and schema is None:
        raise ValueError(f"table {table!r} has a catalog but no schema")
    for part, label in ((table, "table"), (schema, "schema"), (catalog, "catalog")):
        if part is not None and not is_sql_identifier(part):
            raise ValueError(f"invalid {label} identifier: {part!r}")

    namespace: Any = schema
    if catalog is not None:
        namespace = quoted_name(f"{catalog}.{schema}", quote=False)
    return table_clause(table, schema=namespace)


class LazyEngine:
    """One lazily-built SQLAlchemy engine with an idempotent dispose (#427).

    ``factory`` builds the engine on first :meth:`get` (the runner's URL /
    connect-args closure — the only per-datasource variance); every later call
    returns the same engine, so a runner's SQL touchpoints share one pool and
    one auth handshake per run. :meth:`close` disposes the pool and resets, so
    a closed holder lazily rebuilds if reused — never a bricked runner.
    """

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._engine: Any | None = None

    def get(self) -> Any:
        if self._engine is None:
            self._engine = self._factory()
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
