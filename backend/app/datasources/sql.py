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


def folding_identifier(name: str) -> Any:
    """Wrap ``name`` with an explicit quote decision: bare iff it is all lower-case.

    The #476 rule, made ours instead of SQLAlchemy's. Left to its defaults the
    compiler also quotes any name in the **dialect's reserved-word set**, which is
    not the same set the warehouse reserves — Snowflake does not reserve ``copy``,
    but SQLAlchemy's Snowflake dialect does, so a column stored ``COPY`` (created
    unquoted as ``copy``) would be emitted as ``"copy"`` and stop resolving. That
    is the very failure #476 exists to remove, reintroduced for one word.

    Pinning the decision explicitly makes the emitted form depend only on case:

    * **all lower-case → never quoted**, so the warehouse folds it exactly as it
      did before #476 (``order_ts`` → ``ORDER_TS``). This is what makes the change
      byte-for-byte behaviour-preserving rather than approximately so.
    * **anything else → always quoted**, which is the whole point: a mixed-case
      column is only reachable quoted.

    Genuinely reserved words (``order``, ``select``) are unreachable either way —
    bare is a parse error, and the folded object is ``ORDER``, which ``"order"``
    does not match. They were broken before this change and are broken after it,
    identically; alias them in a view.
    """
    from sqlalchemy.sql import quoted_name

    return quoted_name(name, quote=name != name.lower())


def core_table(*, table: str, schema: str | None, catalog: str | None) -> TableClause:
    """A Core table clause for ``[catalog.][schema.]table`` — the dialect quotes it.

    The one construction shared by the profiler's query builders and the monitor
    engine (#476). Going through Core rather than f-string interpolation is what
    makes a **mixed-case identifier** work at all, per `folding_identifier`'s rule.

    It must be Core and not hand-rolled quoting because the quote character is
    **dialect-specific** — Snowflake uses ``"``, Databricks/Unity Catalog uses
    backticks and reads ``"..."`` as a string literal — and this module is shared
    by both.

    **Known limit — the 3-part (catalog) form does not get the #476 treatment.**
    Core's ``schema=`` slot is a single string, so a ``catalog.schema`` namespace
    has to be passed as one *unquoted* ``quoted_name`` for the dialect to emit
    dotted parts rather than quoting the whole thing as one identifier. That
    suppresses quoting for the catalog and schema too, so a mixed-case *catalog or
    schema* still folds. Only the table and column are quote-correct here.

    Building the namespace from pre-quoted parts would mean choosing the quote
    character ourselves, which is exactly the dialect-specific mistake this
    function exists to avoid — so the limit is recorded rather than papered over.
    It is currently unreachable: Unity Catalog is the only caller that passes a
    catalog and resolves identifiers case-insensitively, and Snowflake passes
    ``catalog=None``. Tracked as #936; pinned by a test so it reads as a recorded
    limitation rather than a guarantee.

    That unquoted namespace is also the one raw interpolation left in the module,
    hence the allowlist check on every part here rather than trusting callers
    (they validate first for a good error message; this is the injection
    guarantee, and it survives a caller forgetting to validate).

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

    namespace: Any = None if schema is None else folding_identifier(schema)
    if catalog is not None:
        namespace = quoted_name(f"{catalog}.{schema}", quote=False)
    return table_clause(folding_identifier(table), schema=namespace)


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
