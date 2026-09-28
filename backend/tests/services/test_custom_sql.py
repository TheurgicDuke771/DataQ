"""Custom-SQL guardrail battery (ADR 0019)."""

from __future__ import annotations

import pytest

from backend.app.services.custom_sql import (
    _FORBIDDEN_KEYWORDS,
    CUSTOM_SQL_EXPECTATION_TYPE,
    QUERY_KEY,
    SQL_QUERYABLE_TYPES,
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
        assert exc.value.detail["supported"] == sorted(SQL_QUERYABLE_TYPES)
        assert {"postgres", "mysql", "mssql", "trino", "snowflake", "unity_catalog"} <= set(
            SQL_QUERYABLE_TYPES
        )


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
    # The '-- ok' comment ends at the newline; the '; DROP' on the next line is real code → must be
    # rejected (a scanner that ran the comment to EOF would swallow it and wrongly pass).
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
    # Guards against reintroducing a polynomial-ReDoS in the trailing-token strip (CodeQL
    # py/polynomial-redos): the query is user-provided.
    validate_query("SELECT 1 FROM {batch} WHERE x = 1" + "\t" * 50_000)


def test_backtick_is_not_a_string_quote() -> None:
    # Snowflake / Unity Catalog don't quote strings with backticks, so a backtick span must stay as
    # code — otherwise a '.
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 1 FROM {batch} WHERE x = 1 `; DROP TABLE y; SELECT *`")


# ── mutation-spike gaps (#278) ──────────────────────────────────────────────── The lexer tests
# above pin the cases we thought of; these pin the ones a mutmut spike found nothing asserting.


def test_a_line_comment_blanks_only_its_own_line_not_the_rest_of_the_query() -> None:
    """Pins that the comment ends at the NEXT newline, not the last one."""
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 -- note\nFROM {batch} WHERE drop = 1\nAND y = 2")
    assert exc.value.detail["forbidden"] == ["drop"]


def test_a_comment_on_a_later_line_scans_forward_from_itself() -> None:
    """The newline search must start at the comment, not at the start of the query."""
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1\nFROM {batch} -- note\nWHERE drop = 1")
    assert exc.value.detail["forbidden"] == ["drop"]


def test_a_block_comment_ends_at_its_own_terminator_not_the_last_one() -> None:
    """Two block comments with real code between them."""
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 /* a */ FROM {batch} WHERE drop = 1 /* b */")
    assert exc.value.detail["forbidden"] == ["drop"]


def test_an_empty_block_comment_is_not_read_as_unterminated() -> None:
    """`/**/` — the terminator begins immediately after the opener."""
    validate_query("SELECT 1 FROM {batch} /**/")


def test_a_short_string_literal_closes_normally() -> None:
    """A one-character string, whose closing quote sits at an odd offset."""
    validate_query("SELECT 'a' FROM {batch}")


def test_a_comment_touching_a_keyword_does_not_corrupt_it() -> None:
    """A comment collapses to whitespace, not to text."""
    validate_query("SELECT/**/ 1 FROM {batch}")


@pytest.mark.parametrize(
    "query",
    [
        "SELECT {q}{q} FROM {{batch}}".format(q="'"),  # an empty string literal
        "SELECT {q}{q}{q}{q} FROM {{batch}}".format(q="'"),  # only an escaped quote
        "SELECT {q}a{q} FROM {{batch}}".format(q="'"),  # closer at an odd offset
    ],
)
def test_short_and_empty_string_literals_close_normally(query: str) -> None:
    """The quote scanner must step one character at a time and pair `''` exactly."""
    validate_query(query)


def test_a_line_comment_touching_a_keyword_does_not_corrupt_it() -> None:
    """The line-comment branch collapses to whitespace too."""
    validate_query("SELECT--c\n 1 FROM {batch}")


def test_a_query_may_open_with_a_block_comment() -> None:
    """A comment at offset 0 — where the terminator search starts from nothing."""
    validate_query("/* leading note */ SELECT 1 FROM {batch}")


def test_every_rejection_carries_the_query_key_in_its_detail() -> None:
    """The four rejection paths whose detail carries nothing but `query_key`."""
    for query in (
        "",  # empty
        "   ",  # whitespace only
        "SELECT 1 /* unclosed",  # unterminated comment
        "-- just a comment",  # empty after stripping
        "SELECT 1; SELECT 2",  # multi-statement
    ):
        with pytest.raises(CustomSqlInvalidError) as exc:
            validate_query(query)
        assert exc.value.detail["query_key"] == QUERY_KEY, query


def test_the_error_detail_is_a_stable_contract_not_just_a_message() -> None:
    """The `detail` payload is what a client renders and acts on."""
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 FROM {batch} WHERE drop = 1 AND truncate = 2")
    assert exc.value.detail == {
        "query_key": QUERY_KEY,
        "forbidden": ["drop", "truncate"],  # sorted, de-duplicated
    }

    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SHOW TABLES")
    assert exc.value.detail == {"query_key": QUERY_KEY, "first_keyword": "show"}


def test_a_statement_chained_straight_after_a_closing_quote_is_caught() -> None:
    """`SELECT 'a'; SELECT 1` — the `;` sits immediately after the closing quote."""
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 'a'; SELECT 1")
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT 'a';SELECT 1")  # no space either


def test_a_string_literal_touching_a_keyword_does_not_corrupt_it() -> None:
    """`SELECT'a' FROM t` — a literal may abut the keyword before it."""
    validate_query("SELECT'a' FROM {batch}")


# ── the survivors left standing, and why (#278 triage) ─────────────────────── The spike went 63
# survivors → 29; every remaining one was examined and falls into the groups below.


# ── dialect-aware lexing (#1679) ──────────────────────────────────────────────


def test_tsql_bracket_identifiers_are_identifier_text() -> None:
    """Inside `[…]` a quote, `;` or comment marker is part of a name, not code."""
    query = "SELECT [Order Id], [it's], [a;b], [x--y], [p]]q] FROM {batch} WHERE [n] > 0"
    validate_query(query, connection_type="mssql")
    validate_custom_sql_check(
        expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
        config={QUERY_KEY: query},
        connection_type="mssql",
    )


def test_a_bracket_cannot_open_a_fake_string_that_hides_a_statement() -> None:
    """Read as plain SQL, `'] … --'` is ONE string literal; SQL Server reads `[a']` as a column
    and then runs the DELETE. The T-SQL lexing must see the second statement.
    """
    attack = "SELECT [a'], 1 FROM {batch}; DELETE FROM t --'"
    with pytest.raises(CustomSqlInvalidError):
        validate_query(attack, connection_type="mssql")
    with pytest.raises(CustomSqlInvalidError):
        validate_custom_sql_check(
            expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
            config={QUERY_KEY: attack},
            connection_type="mssql",
        )


def test_a_backtick_cannot_open_a_fake_string_on_databricks() -> None:
    """The same shape through Databricks' backtick identifiers."""
    attack = "SELECT `a'`, 1 FROM {batch}; DELETE FROM t --'"
    with pytest.raises(CustomSqlInvalidError):
        validate_query(attack, connection_type="unity_catalog")


def test_an_unterminated_bracket_identifier_is_rejected() -> None:
    with pytest.raises(CustomSqlInvalidError, match="unterminated"):
        validate_query("SELECT [a FROM {batch}", connection_type="mssql")


def test_an_unknown_dialect_must_pass_every_lexing() -> None:
    """Valid T-SQL that is an unterminated string in every other dialect is refused when the
    dialect is unknown — the strictest reading wins.
    """
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT [it's] FROM {batch}")
    with pytest.raises(CustomSqlInvalidError):
        validate_query("SELECT [a'], 1 FROM {batch}; DELETE FROM t --'")


def test_a_nested_block_comment_is_rejected_everywhere() -> None:
    """PostgreSQL and SQL Server nest block comments, MySQL and Snowflake do not: under nesting
    the `'` below is comment text and the DELETE runs; read flat, it opens a string hiding it.
    """
    attack = "SELECT 1 FROM {batch} /* /* */ ' */ ; DELETE FROM t; SELECT '"
    for connection_type in (None, "postgres", "mssql", "snowflake", "unity_catalog"):
        with pytest.raises(CustomSqlInvalidError, match="unterminated"):
            validate_query(attack, connection_type=connection_type)
    validate_query("SELECT 1 /* a */ FROM {batch} /* b */", connection_type="postgres")


@pytest.mark.parametrize(
    "keyword", ["openquery", "openrowset", "opendatasource", "dbcc", "waitfor", "bulk"]
)
def test_tsql_only_keywords_are_forbidden_on_mssql(keyword: str) -> None:
    query = f"SELECT * FROM {{batch}} WHERE {keyword} = 1"
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query(query, connection_type="mssql")
    assert exc.value.detail["forbidden"] == [keyword]
    # Not a keyword elsewhere — a column of that name stays usable.
    validate_query(query, connection_type="postgres")
    # Unknown dialect: every dialect's list applies.
    with pytest.raises(CustomSqlInvalidError):
        validate_query(query)


def test_a_lone_carriage_return_ends_a_line_comment() -> None:
    """PostgreSQL and SQL Server end `--` at a CR; reading on to the LF would hide the DELETE."""
    for connection_type in (None, "postgres", "mssql"):
        with pytest.raises(CustomSqlInvalidError):
            validate_query(
                "SELECT 1 FROM {batch} --x\rDELETE FROM t", connection_type=connection_type
            )
    validate_query("SELECT 1 FROM {batch} -- note\r\nWHERE 1 = 1", connection_type="mssql")


@pytest.mark.parametrize(
    "statement",
    [
        "WRITETEXT t.c @p 'x'",
        "UPDATETEXT t.c @p 0 NULL 'x'",
        "DENY SELECT ON t TO public",
        "DISABLE TRIGGER trg ON t",
        "CHECKPOINT",
    ],
)
def test_tsql_runs_unseparated_statements_so_each_write_keyword_is_refused(statement: str) -> None:
    """T-SQL needs no `;` between statements: the second one below would run in the same batch."""
    with pytest.raises(CustomSqlInvalidError):
        validate_query(f"SELECT TOP 1 c FROM {{batch}} {statement}", connection_type="mssql")


# ───────────────────────── T-SQL derived tables (#2138) ─────────────────


@pytest.mark.parametrize(
    ("query", "offset_added"),
    [
        ("SELECT a FROM t ORDER BY a", True),
        ("SELECT a FROM t UNION SELECT a FROM u ORDER BY a", True),
        ("SELECT a FROM t WHERE b IN (SELECT TOP 1 b FROM u ORDER BY b) ORDER BY a", True),
        ("SELECT TOP 5 a FROM t ORDER BY a", False),  # TOP already makes it legal
        ("SELECT a FROM t ORDER BY a OFFSET 2 ROWS", False),
        ("SELECT a FROM (SELECT a FROM t) AS x", False),
        ("SELECT a FROM (SELECT a FROM t ORDER BY a OFFSET 0 ROWS) AS x", False),
        ("SELECT 'order by' AS x FROM t", False),  # inside a literal
        ("SELECT [order by] FROM t", False),  # a delimited identifier
    ],
)
def test_a_top_level_order_by_gains_offset_only_where_t_sql_needs_it(
    query: str, offset_added: bool
) -> None:
    from backend.app.services.custom_sql import tsql_derived_table_query

    rewritten = tsql_derived_table_query(query)
    assert rewritten == (f"{query}\nOFFSET 0 ROWS" if offset_added else query)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("WITH c AS (SELECT 1) SELECT * FROM c", True),
        ("  -- a note\n with c AS (SELECT 1) SELECT * FROM c", True),
        ("SELECT 'with' FROM t", False),
        ("SELECT a FROM t", False),
    ],
)
def test_starts_with_cte(query: str, expected: bool) -> None:
    from backend.app.services.custom_sql import starts_with_cte

    assert starts_with_cte(query, "mssql") is expected


def test_a_sql_server_comparison_side_starting_with_a_cte_is_refused_at_author_time() -> None:
    """Saved, it would fail on every run: T-SQL cannot read a WITH query as a derived table."""
    from backend.app.services.check_service import CheckConfigInvalidError, _validate_side_query

    query = "WITH c AS (SELECT OrderId FROM dbo.Orders) SELECT OrderId FROM c"
    with pytest.raises(CheckConfigInvalidError, match="as a subquery") as exc:
        _validate_side_query(query, connection_type="mssql", field="config.target_query")
    assert exc.value.detail == {"field": "config.target_query"}
    _validate_side_query(query, connection_type="postgres", field="config.target_query")
