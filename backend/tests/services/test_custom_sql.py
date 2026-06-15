"""Custom-SQL guardrail battery (ADR 0019).

Pure-unit (no DB / no GX): the read-only/single-statement validator + datasource
gating. Failure-mode-first per CONTRIBUTING rule 4a — the hostile cases (DML,
CTE-wrapped DML, multi-statement, comment/quote smuggling) carry the security
weight, so they outnumber the happy path.

Mutation-spiked (CONTRIBUTING rule 4a): a mutmut pass on `custom_sql.py` drove
these tests to isolate every real behavioural gap — each `_FORBIDDEN_KEYWORDS`
member individually, the escaped-quote/comment-boundary scanner edges, and the
error `code`/`status_code`/`detail` semantics. The residual survivors are all
equivalent or brittle (human message text; the `quote in "'\\""` membership,
which only governs backtick-doubling — not real SQL; the constant `query_key`
detail), so they're deliberately not chased.
"""

from __future__ import annotations

import pytest

from backend.app.services.custom_sql import (
    _FORBIDDEN_KEYWORDS,
    CUSTOM_SQL_EXPECTATION_TYPE,
    CustomSqlInvalidError,
    is_custom_sql,
    validate_custom_sql_check,
    validate_query,
)

# Queries a read-only check legitimately needs — must NOT raise.
VALID_QUERIES = [
    "SELECT * FROM {batch} WHERE amount IS NULL",
    "select count(*) from {batch}",
    "WITH t AS (SELECT * FROM {batch}) SELECT * FROM t WHERE n > 0",
    "SELECT 1 FROM {batch};",  # single trailing semicolon is fine
    "SELECT 1 FROM {batch}  ;  ",  # trailing semicolon + whitespace
    "(SELECT * FROM {batch}) UNION (SELECT * FROM {batch})",  # leading paren
    # `replace()` is a string function, not the DDL keyword; a literal 'delete'
    # and a quoted identifier "update" must not trip the keyword scan.
    "SELECT replace(name, 'a', 'b') AS r FROM {batch} WHERE action <> 'delete'",
    'SELECT "update" FROM {batch}',
    "SELECT * FROM {batch} WHERE note = 'a;b'",  # ';' inside a string literal
    "SELECT * FROM {batch} -- drop table evil\nWHERE 1 = 1",  # keyword in a comment
    "SELECT 1 /* ; drop */ FROM {batch}",  # block comment hides ';' + keyword
]

# Queries that must be rejected (CustomSqlInvalidError).
INVALID_QUERIES = [
    "",  # empty
    "   ",  # whitespace only
    "-- just a comment",  # empty after stripping the comment
    "DELETE FROM {batch}",
    "UPDATE {batch} SET x = 1",
    "DROP TABLE secrets",
    "INSERT INTO t VALUES (1)",
    "TRUNCATE TABLE t",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
    "GRANT SELECT ON t TO bob",
    "SELECT * INTO new_table FROM {batch}",  # SELECT ... INTO creates a table
    "SELECT 1 FROM a; SELECT 2 FROM b",  # two statements (both reads)
    "SELECT 1 FROM {batch}; DROP TABLE x",  # trailing DML statement
    "WITH t AS (INSERT INTO x VALUES (1) RETURNING *) SELECT * FROM t",  # CTE DML
    # The bug the single-pass scanner fixes: a '--' inside a string literal must
    # not mask the trailing '; DROP ...' from the multi-statement / keyword scan.
    "SELECT 1 FROM {batch} WHERE x = 'a--'; DROP TABLE y",
]


@pytest.mark.parametrize("query", VALID_QUERIES)
def test_valid_queries_pass(query: str) -> None:
    validate_query(query)  # must not raise


@pytest.mark.parametrize("query", INVALID_QUERIES)
def test_invalid_queries_rejected(query: str) -> None:
    with pytest.raises(CustomSqlInvalidError):
        validate_query(query)


@pytest.mark.parametrize("bad", [None, 123, [], {}, b"SELECT 1"])
def test_non_string_query_rejected(bad: object) -> None:
    with pytest.raises(CustomSqlInvalidError):
        validate_query(bad)


def test_is_custom_sql() -> None:
    assert is_custom_sql(CUSTOM_SQL_EXPECTATION_TYPE)
    assert is_custom_sql("unexpected_rows_expectation")
    assert not is_custom_sql("expect_column_values_to_not_be_null")
    assert not is_custom_sql("")


_GATING_QUERY = {"unexpected_rows_query": "SELECT * FROM {batch} WHERE x IS NULL"}


class TestDatasourceGating:
    @pytest.mark.parametrize("conn_type", ["snowflake", "unity_catalog"])
    def test_sql_datasources_allowed(self, conn_type: str) -> None:
        validate_custom_sql_check(
            expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
            config=_GATING_QUERY,
            connection_type=conn_type,
        )  # must not raise

    @pytest.mark.parametrize("conn_type", ["s3", "adls_gen2", "adf", "airflow"])
    def test_non_sql_datasources_rejected(self, conn_type: str) -> None:
        with pytest.raises(CustomSqlInvalidError):
            validate_custom_sql_check(
                expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
                config=_GATING_QUERY,
                connection_type=conn_type,
            )

    def test_bad_query_on_sql_datasource_rejected(self) -> None:
        with pytest.raises(CustomSqlInvalidError):
            validate_custom_sql_check(
                expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
                config={"unexpected_rows_query": "DELETE FROM {batch}"},
                connection_type="snowflake",
            )

    def test_missing_query_key_rejected(self) -> None:
        with pytest.raises(CustomSqlInvalidError):
            validate_custom_sql_check(
                expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
                config={},
                connection_type="snowflake",
            )

    def test_non_custom_expectation_is_noop_even_on_flatfile(self) -> None:
        # A normal expectation on a flat-file datasource must pass untouched —
        # the guardrail only governs custom-SQL.
        validate_custom_sql_check(
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "id"},
            connection_type="s3",
        )

    def test_gating_error_detail_names_type_and_supported(self) -> None:
        with pytest.raises(CustomSqlInvalidError) as exc:
            validate_custom_sql_check(
                expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
                config=_GATING_QUERY,
                connection_type="s3",
            )
        assert exc.value.detail["connection_type"] == "s3"
        assert exc.value.detail["supported"] == ["snowflake", "unity_catalog"]


# ─────────── forbidden-keyword set: isolate every member ────────────
# A bareword DML/DDL keyword inside a SELECT must be rejected. This exercises
# each `_FORBIDDEN_KEYWORDS` member on its own — a top-level `DELETE` is caught by
# the SELECT/WITH check (never reaching the set), and DML in a real query usually
# co-occurs with `into`, so without this every individual keyword's removal goes
# unnoticed (the mutmut survivors that motivated this test).


@pytest.mark.parametrize("keyword", sorted(_FORBIDDEN_KEYWORDS))
def test_each_forbidden_keyword_is_rejected(keyword: str) -> None:
    with pytest.raises(CustomSqlInvalidError):
        validate_query(f"SELECT 1 FROM {{batch}} WHERE {keyword} = 1")


# ───────────────────────── error metadata ──────────────────────────


def test_error_carries_code_status_and_forbidden_detail() -> None:
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 FROM {batch} WHERE drop = 1")
    err = exc.value
    assert err.code == "custom_sql_invalid"
    assert err.status_code == 422
    assert err.detail["forbidden"] == ["drop"]


def test_non_select_start_reports_first_keyword() -> None:
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("EXPLAIN SELECT 1 FROM {batch}")
    assert exc.value.detail["first_keyword"] == "explain"


def test_non_keyword_start_reports_none_first_keyword() -> None:
    # A query that doesn't start with a word at all → first_keyword is None
    # (exercises the `first_kw or None` fallback).
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("42 IS THE ANSWER")
    assert exc.value.detail["first_keyword"] is None


# ─────────── scanner edges (_strip_noncode): the security core ──────
# These pin the single-pass scanner so neither comments nor strings can mask the
# other — the class of bug that lets a smuggled `; DROP` slip past the keyword /
# multi-statement scan.


def test_escaped_quote_does_not_break_out_of_string() -> None:
    # 'a''; DROP TABLE y' is ONE string literal ('' = an escaped quote); the
    # '; DROP' lives inside it, so the query is a single, valid SELECT.
    validate_query("SELECT * FROM {batch} WHERE x = 'a''; DROP TABLE y'")


def test_doubled_quote_identifier_handled() -> None:
    validate_query('SELECT "a""b" AS c FROM {batch}')  # "" escaped in an identifier


def test_line_comment_stops_at_newline_not_end_of_query() -> None:
    # The '-- ok' comment ends at the newline; the '; DROP' on the next line is
    # real code → must be rejected (a scanner that ran the comment to EOF would
    # swallow it and wrongly pass).
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 1 FROM {batch} -- ok\n; DROP TABLE x")


def test_statement_after_block_comment_is_caught() -> None:
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 1 FROM {batch} /* c */ ; DROP TABLE x")


def test_keyword_immediately_after_block_comment_is_caught() -> None:
    # `drop` abuts the `*/` with no space — pins the comment-end boundary
    # (`end + 2`): an off-by-one would clip the keyword and let it through.
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 FROM {batch} WHERE/*x*/drop = 1")
    assert exc.value.detail["forbidden"] == ["drop"]


def test_unterminated_block_comment_is_rejected() -> None:
    # An unterminated string/comment swallows the rest of the query as literal
    # text — we can't reason about it, so fail closed (ADR 0019 review).
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 1 FROM {batch} /* unclosed ; DROP TABLE x")


def test_unterminated_string_is_rejected() -> None:
    # Without this, the open quote hides the trailing '; DROP TABLE y' from the
    # multi-statement + keyword scan (a confirmed fail-open bypass).
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 1 FROM {batch} WHERE n = 'unterminated ; DROP TABLE y")


def test_large_trailing_whitespace_handled_linearly() -> None:
    # Guards against reintroducing a polynomial-ReDoS in the trailing-token strip
    # (CodeQL py/polynomial-redos): the query is user-provided, and a `[;\s]+$`
    # regex would backtrack quadratically here. str.rstrip is linear — this
    # returns instantly; a regex version would hang the test.
    validate_query("SELECT 1 FROM {batch} WHERE x = 1" + "\t" * 50_000)


def test_backtick_is_not_a_string_quote() -> None:
    # Snowflake / Unity Catalog don't quote strings with backticks, so a backtick
    # span must stay as code — otherwise a '; DROP' smuggled inside it is blanked
    # out before the scan (a confirmed bypass). The embedded ';' must be caught.
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 1 FROM {batch} WHERE x = 1 `; DROP TABLE y; SELECT *`")
