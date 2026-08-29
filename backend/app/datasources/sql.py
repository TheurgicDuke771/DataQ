"""Shared SQL-datasource primitives: the identifier allowlist (#428), the
lazy engine lifecycle (#427), and the statement-echo strip (#1203).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

from backend.app.datasources.base import CheckSpec

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect
    from sqlalchemy.sql import TableClause

# The Snowflake/Databricks unquoted-identifier set: letter/underscore lead, then word chars or `$`.
_SQL_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def is_sql_identifier(name: object) -> bool:
    """True when ``name`` is a plain SQL identifier safe to interpolate."""
    return isinstance(name, str) and bool(_SQL_IDENTIFIER_RE.fullmatch(name))


# Where `sqlalchemy.exc.StatementError._sql_message` stops reporting the error and starts echoing
# `.statement` / `.params`.
_STATEMENT_ECHO_MARKER: Final = "\n[SQL: "


def strip_statement_echo(message: str | None) -> str | None:
    """Drop the ``[SQL: …] [parameters: …]`` tail SQLAlchemy appends to a failed
    statement, keeping the driver's own message (#1203).
    """
    if not message:
        return message
    head, marker, _ = message.partition(_STATEMENT_ECHO_MARKER)
    return head.rstrip() if marker else message


def folding_identifier(name: str) -> Any:
    """Wrap ``name`` with an explicit quote decision: bare iff it is all lower-case."""
    from sqlalchemy.sql import quoted_name

    return quoted_name(name, quote=name != name.lower())


def _quote_namespace_part(name: str, dialect: Dialect) -> str:
    """Apply `folding_identifier`'s case-based decision to one namespace part
    that is about to be hand-assembled into a pre-quoted, dotted string (the
    3-part ``catalog.schema`` case — see `core_table`).
    """
    if name == name.lower():
        return name
    return str(dialect.identifier_preparer.quote_identifier(name))


def core_table(
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    dialect: Dialect | None = None,
) -> TableClause:
    """A Core table clause for ``[catalog.][schema.]table`` — the dialect quotes it."""
    from sqlalchemy import table as table_clause
    from sqlalchemy.sql import quoted_name

    if catalog is not None and schema is None:
        raise ValueError(f"table {table!r} has a catalog but no schema")
    for part, label in ((table, "table"), (schema, "schema"), (catalog, "catalog")):
        if part is not None and not is_sql_identifier(part):
            raise ValueError(f"invalid {label} identifier: {part!r}")

    if catalog is not None:
        if dialect is None:
            raise ValueError(f"table {table!r} has a catalog but no dialect to quote it")
        assert schema is not None  # guaranteed by the catalog/schema check above
        full = quoted_name(
            ".".join(_quote_namespace_part(part, dialect) for part in (catalog, schema, table)),
            quote=False,
        )
        return table_clause(full, schema=None)

    namespace: Any = None if schema is None else folding_identifier(schema)
    return table_clause(folding_identifier(table), schema=namespace)


def qualified_sql_name(
    *, table: str, schema: str | None, catalog: str | None, dialect: Dialect
) -> str:
    """``[catalog.][schema.]table`` as a pre-quoted string for a `text()` statement."""
    if catalog is not None and schema is None:
        raise ValueError(f"table {table!r} has a catalog but no schema")
    parts = [(catalog, "catalog"), (schema, "schema"), (table, "table")]
    for part, label in parts:
        if part is not None and not is_sql_identifier(part):
            raise ValueError(f"invalid {label} identifier: {part!r}")
    return ".".join(_quote_namespace_part(part, dialect) for part, _ in parts if part is not None)


def fold_reflection_keyed_columns(
    checks: list[CheckSpec],
    *,
    reflection_keyed_types: frozenset[str],
    normalize_name: Callable[[str], str],
) -> list[CheckSpec]:
    """Fold `column_list` on checks whose GX metric indexes reflected columns, using the
    dialect's own reflection-name rewrite so an authored spelling matches the reflected key.
    """
    folded = []
    for spec in checks:
        column_list = spec.kwargs.get("column_list")
        if spec.expectation_type in reflection_keyed_types and isinstance(column_list, list):
            spec = CheckSpec(
                expectation_type=spec.expectation_type,
                kwargs={
                    **spec.kwargs,
                    "column_list": [
                        normalize_name(c) if isinstance(c, str) else c for c in column_list
                    ],
                },
            )
        folded.append(spec)
    return folded


class LazyEngine:
    """One lazily-built SQLAlchemy engine with an idempotent dispose (#427)."""

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
