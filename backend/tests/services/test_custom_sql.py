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
    QUERY_KEY,
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


# ── mutation-spike gaps (#278) ────────────────────────────────────────────────
#
# The lexer tests above pin the cases we thought of; these pin the ones a mutmut
# spike found nothing asserting. Each corresponds to a specific surviving mutant,
# and each is a FAIL-OPEN risk: the mutated scanner blanks out more of the query
# than it should, hiding a forbidden keyword from the guard rather than tripping
# on it. That direction is what matters — a scanner that blanks too little is
# noisy, one that blanks too much is a bypass.


def test_a_line_comment_blanks_only_its_own_line_not_the_rest_of_the_query() -> None:
    """Pins that the comment ends at the NEXT newline, not the last one.

    The existing single-newline test can't tell `find` from `rfind` — with one
    newline they agree. With two, a scanner that jumped to the last newline would
    swallow the whole middle line, hiding the `drop` it carries.
    """
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 -- note\nFROM {batch} WHERE drop = 1\nAND y = 2")
    assert exc.value.detail["forbidden"] == ["drop"]


def test_a_comment_on_a_later_line_scans_forward_from_itself() -> None:
    """The newline search must start at the comment, not at the start of the query.

    Searching from position 0 finds a newline *behind* the cursor, which either
    rewinds the scan or terminates it early — either way the tail stops being
    examined.
    """
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1\nFROM {batch} -- note\nWHERE drop = 1")
    assert exc.value.detail["forbidden"] == ["drop"]


def test_a_block_comment_ends_at_its_own_terminator_not_the_last_one() -> None:
    """Two block comments with real code between them.

    A scanner that ran to the LAST `*/` would blank the code in the middle —
    including the `drop` — and pass the query. With one comment the bug is
    invisible, which is why nothing caught it.
    """
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 /* a */ FROM {batch} WHERE drop = 1 /* b */")
    assert exc.value.detail["forbidden"] == ["drop"]


def test_an_empty_block_comment_is_not_read_as_unterminated() -> None:
    """`/**/` — the terminator begins immediately after the opener.

    Pins where the search for `*/` starts: one character later and this reads as
    an unterminated comment, so a perfectly ordinary query is rejected. The
    fail-closed direction, but a false rejection is still a bug.
    """
    validate_query("SELECT 1 FROM {batch} /**/")


def test_a_short_string_literal_closes_normally() -> None:
    """A one-character string, whose closing quote sits at an odd offset.

    Pins that the in-string scan advances one character at a time: stepping two
    at a time skips the closing quote on odd-length content, and the query is
    then rejected as having an unterminated literal.
    """
    validate_query("SELECT 'a' FROM {batch}")


def test_a_comment_touching_a_keyword_does_not_corrupt_it() -> None:
    """A comment collapses to whitespace, not to text.

    `SELECT/**/1` must still read as a SELECT. If the blanked span contributed
    any *letters*, the leading keyword would parse as `selectsomething` and a
    valid read-only query would be rejected as not-a-SELECT.
    """
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
    """The quote scanner must step one character at a time and pair `\'\'` exactly.

    Each of these is a legitimate query that an off-by-one in the in-string scan
    reads as an UNTERMINATED literal — so the guard would reject it and a user
    could not write `WHERE note = \'\'`.

    Found by differential-testing the surviving mutants at the VERDICT level.
    Comparing the scanner's raw output was misleading: several mutants change only
    how many blank placeholders it appends, which no caller can observe. Four of
    the quote-scanner mutants cannot change a verdict at all and are recorded as
    unkillable below rather than chased.
    """
    validate_query(query)


def test_a_line_comment_touching_a_keyword_does_not_corrupt_it() -> None:
    """The line-comment branch collapses to whitespace too.

    Same property as the block-comment case above, on the other branch — which
    the block-comment test cannot reach, and nothing else asserted.
    """
    validate_query("SELECT--c\n 1 FROM {batch}")


def test_a_query_may_open_with_a_block_comment() -> None:
    """A comment at offset 0 — where the terminator search starts from nothing.

    An off-by-two in that start position is invisible anywhere else in the query
    (the search still lands on the right `*/`), but at the very beginning it
    walks off the front and the comment reads as unterminated.
    """
    validate_query("/* leading note */ SELECT 1 FROM {batch}")


def test_every_rejection_carries_the_query_key_in_its_detail() -> None:
    """All six rejection paths, not just the two informative ones.

    A caller keys its field-level error off `query_key`; a path that omitted it
    would surface as an error attached to nothing. Cheap to assert, and it is the
    only thing four of these six paths return besides prose.
    """
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
    """The `detail` payload is what a client renders and acts on.

    Asserted as a whole dict rather than one key: the error prose is deliberately
    NOT pinned (see the note below), so this payload is the only part of the
    422 a caller can rely on — which makes it the part worth pinning.
    """
    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SELECT 1 FROM {batch} WHERE drop = 1 AND truncate = 2")
    assert exc.value.detail == {
        "query_key": QUERY_KEY,
        "forbidden": ["drop", "truncate"],  # sorted, de-duplicated
    }

    with pytest.raises(CustomSqlInvalidError) as exc:
        validate_query("SHOW TABLES")
    assert exc.value.detail == {"query_key": QUERY_KEY, "first_keyword": "show"}


# ── the survivors left standing, and why (#278 triage) ───────────────────────
#
# The spike went 63 survivors → 31; every remaining one was examined and falls
# into three groups, none of them a coverage gap worth closing:
#
# 1. ERROR PROSE (~22). Mutants that upper-case, lower-case, XX-wrap or None out
#    the human-readable message. Pinning prose turns every copy edit into a test
#    failure while proving nothing about behaviour. What a caller actually
#    depends on — the 422, the `custom_sql_invalid` code, and the `detail`
#    payload — IS asserted above, on all six rejection paths.
#
# 2. FALSY SUBSTITUTIONS (3). `well_formed = None` / `closed = None` in place of
#    `False`. Both are only ever read through `not …`, so the mutant is
#    behaviourally identical — an equivalent mutant, unkillable by construction.
#
# 3. FOUR QUOTE-SCANNER MUTANTS that shift the doubled-quote probe window
#    (`sql[i+1:i+2]` → `[i-1:i+2]`, `[i+2:i+2]`, `[i+1:i-2]`, `[i+1:i+3]`).
#    These looked like real gaps when the scanner's raw output was compared —
#    and that comparison was misleading. For balanced quotes they change only how
#    many blank placeholders get appended, never which spans are blanked, so
#    `validate_query`'s verdict is identical for every input. Verified by
#    exhaustively differential-testing real-vs-mutant VERDICTS over a quote-heavy
#    alphabet rather than by reasoning about it: three sibling mutants in the same
#    scanner DID differ, and are pinned by
#    `test_short_and_empty_string_literals_close_normally` above.
#
# Score at the time of this triage: 139 killed / 31 survived / 15 timeout of 185
# (up from 113 / 63 / 9). Re-run with the `[tool.mutmut]` block pointed at this
# module — it is a manual spike, never CI (CONTRIBUTING rule 4a).
