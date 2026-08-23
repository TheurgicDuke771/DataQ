"""Guardrails for custom-SQL checks (ADR 0019)."""

from __future__ import annotations

import re
import string
from typing import Any

from backend.app.core.errors import DataQError

# Trailing characters a single statement may end with (whitespace + a closing semicolon).
_TRAILING_CHARS = string.whitespace + ";"

# The GX expectation a custom-SQL check maps to (ADR 0019).
CUSTOM_SQL_EXPECTATION_TYPE = "unexpected_rows_expectation"
# The GX kwarg holding the user's query.
QUERY_KEY = "unexpected_rows_query"

# Datasources GX can run SQL against (ADR 0019). Flat files (adls_gen2 / s3) are
# DataFrame assets; orchestration types (adf / airflow) aren't datasources at all.
SQL_QUERYABLE_TYPES = frozenset({"snowflake", "unity_catalog"})

# Statement keywords that mutate data, schema, permissions, or transaction state.
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
        # Defence-in-depth: other state-changing / proc-invoking statements.
        "call",
        "exec",
        "execute",
        "do",
        "copy",
        "lock",
        "set",
        "reset",
        "discard",
        "prepare",
        "deallocate",
        "vacuum",
        "analyze",
        "use",
        "attach",
        "detach",
        "unload",
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


def _strip_noncode(sql: str) -> tuple[str, bool]:
    """Replace comments and string literals with spaces in a single left-to-right
    pass, leaving only executable code. Returns ``(code, well_formed)``.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    well_formed = True
    while i < n:
        pair = sql[i : i + 2]
        if pair == "--":
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl
            out.append(" ")
        elif pair == "/*":
            end = sql.find("*/", i + 2)
            if end == -1:
                well_formed = False  # unterminated block comment
                i = n
            else:
                i = end + 2
            out.append(" ")
        elif sql[i] in "'\"":
            quote = sql[i]
            i += 1
            closed = False
            while i < n:
                if sql[i] == quote:
                    if sql[i + 1 : i + 2] == quote:
                        i += 2  # doubled quote = escaped, stay in the string
                        continue
                    i += 1
                    closed = True
                    break
                i += 1
            if not closed:
                well_formed = False  # unterminated string literal
            out.append(" ")
        else:
            out.append(sql[i])
            i += 1
    return "".join(out), well_formed


def validate_query(raw_query: Any) -> None:
    """Reject a non-read-only or multi-statement custom-SQL query (422)."""
    if not isinstance(raw_query, str) or not raw_query.strip():
        raise CustomSqlInvalidError(
            f"custom-SQL check requires a non-empty {QUERY_KEY!r}",
            detail={"query_key": QUERY_KEY},
        )

    code, well_formed = _strip_noncode(raw_query)
    if not well_formed:
        # An unterminated string/comment means the rest of the query was swallowed
        # as literal text — we can't reason about it, so fail closed.
        raise CustomSqlInvalidError(
            "custom-SQL has an unterminated string literal or comment",
            detail={"query_key": QUERY_KEY},
        )

    analysis = code.strip().rstrip(_TRAILING_CHARS)
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
    """Guardrail for a custom-SQL check; a no-op for any other expectation."""
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
