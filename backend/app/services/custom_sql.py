"""Guardrails for custom-SQL checks (ADR 0019).

A custom-SQL check is a GX ``UnexpectedRowsExpectation``: a normal
``kind='expectation'`` check with ``expectation_type='unexpected_rows_expectation'``
and ``config={'unexpected_rows_query': '<SQL with {batch}>'}``. It rides the
existing model / runner / result path unchanged — the only thing v1 adds is this
single guardrail, reused by every authoring path (check CRUD + suite import).

Two checks:

1. **Datasource gating** — custom-SQL is offered only for SQL-queryable
   datasources (Snowflake, Unity Catalog). Flat-file stores (ADLS / S3) are GX
   DataFrame assets, not SQL, so the query could never run there.
2. **Read-only, single statement** — the query must be a single ``SELECT`` /
   ``WITH`` statement with no DML/DDL/DCL. This is **best-effort, defence-in-depth,
   not a SQL firewall** (ADR 0019): the real boundary is the connection's
   least-privilege role. We strip string literals / quoted identifiers / comments
   before scanning so a literal ``'delete'`` or a column named ``"update"`` is not
   mistaken for a keyword, then require a read-only shape.

FastAPI-free like the sibling services: raises ``DataQError`` subclasses.
"""

from __future__ import annotations

import re
from typing import Any

from backend.app.core.errors import DataQError

# The GX expectation a custom-SQL check maps to (ADR 0019).
CUSTOM_SQL_EXPECTATION_TYPE = "unexpected_rows_expectation"
# The GX kwarg holding the user's query.
QUERY_KEY = "unexpected_rows_query"

# Datasources GX can run SQL against (ADR 0019). Flat files (adls_gen2 / s3) are
# DataFrame assets; orchestration types (adf / airflow) aren't datasources at all.
SQL_QUERYABLE_TYPES = frozenset({"snowflake", "unity_catalog"})

# Statement keywords that mutate data, schema, permissions, or transaction state.
# A read-only check query must contain none of them (as a bareword, after string
# literals / quoted identifiers / comments are stripped). `replace` is deliberately
# absent — it collides with the very common `replace()` string function; a
# `CREATE OR REPLACE` is already caught by `create`.
_FORBIDDEN_KEYWORDS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "merge",
        "upsert",
        "truncate",
        "drop",
        "alter",
        "create",
        "grant",
        "revoke",
        "commit",
        "rollback",
        "into",  # SELECT ... INTO <table> creates a table in some dialects
    }
)

_LEADING_KEYWORD = re.compile(r"[\s(]*([a-zA-Z]+)")
_WORD = re.compile(r"[a-zA-Z_]+")


class CustomSqlInvalidError(DataQError):
    status_code = 422
    code = "custom_sql_invalid"


def is_custom_sql(expectation_type: str) -> bool:
    """True if `expectation_type` is the custom-SQL expectation (ADR 0019)."""
    return expectation_type == CUSTOM_SQL_EXPECTATION_TYPE


def _strip_noncode(sql: str) -> str:
    """Replace comments, string literals, and quoted identifiers with spaces in a
    single left-to-right pass, leaving only executable code.

    A single pass (not sequential regexes) is required so neither construct can
    mask the other: a ``--`` *inside* a string must not be read as a comment (which
    could hide a trailing ``; DROP ...`` from the keyword scan), and a quote inside
    a comment must not start a bogus string. Handles ``--`` line comments, ``/* */``
    block comments, and ``'`` / ``"`` / `` ` `` quotes (``''``/``""`` doubled-escape).
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        pair = sql[i : i + 2]
        if pair == "--":
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl
            out.append(" ")
        elif pair == "/*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        elif sql[i] in "'\"`":
            quote = sql[i]
            i += 1
            while i < n:
                if sql[i] == quote:
                    if quote in "'\"" and sql[i + 1 : i + 2] == quote:
                        i += 2  # doubled quote = escaped, stay in the string
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def validate_query(raw_query: Any) -> None:
    """Reject a non-read-only or multi-statement custom-SQL query (422).

    Best-effort lexical guard (ADR 0019), not a SQL firewall — paired with the
    connection's least-privilege role as the real boundary.
    """
    if not isinstance(raw_query, str) or not raw_query.strip():
        raise CustomSqlInvalidError(
            f"custom-SQL check requires a non-empty {QUERY_KEY!r}",
            detail={"query_key": QUERY_KEY},
        )

    analysis = re.sub(r"[;\s]+$", "", _strip_noncode(raw_query).strip())
    if not analysis:
        raise CustomSqlInvalidError(
            "custom-SQL query is empty after removing comments",
            detail={"query_key": QUERY_KEY},
        )

    if ";" in analysis:
        raise CustomSqlInvalidError(
            "custom-SQL must be a single statement (no ';'-chained statements)",
            detail={"query_key": QUERY_KEY},
        )

    leading = _LEADING_KEYWORD.match(analysis)
    first_kw = leading.group(1).lower() if leading else ""
    if first_kw not in {"select", "with"}:
        raise CustomSqlInvalidError(
            "custom-SQL must be a read-only SELECT / WITH query",
            detail={"query_key": QUERY_KEY, "first_keyword": first_kw or None},
        )

    forbidden = sorted({w.lower() for w in _WORD.findall(analysis)} & _FORBIDDEN_KEYWORDS)
    if forbidden:
        raise CustomSqlInvalidError(
            "custom-SQL must be read-only; remove the disallowed keyword(s)",
            detail={"query_key": QUERY_KEY, "forbidden": forbidden},
        )


def validate_custom_sql_check(
    *, expectation_type: str, config: dict[str, Any], connection_type: str
) -> None:
    """Guardrail for a custom-SQL check; a no-op for any other expectation.

    Rejects (422) a custom-SQL check on a non-SQL datasource, or one whose query
    isn't a single read-only statement.
    """
    if not is_custom_sql(expectation_type):
        return
    if connection_type not in SQL_QUERYABLE_TYPES:
        raise CustomSqlInvalidError(
            f"custom-SQL checks require a SQL datasource, not {connection_type!r}",
            detail={
                "connection_type": connection_type,
                "supported": sorted(SQL_QUERYABLE_TYPES),
            },
        )
    validate_query(config.get(QUERY_KEY))
