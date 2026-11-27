"""Partial and expression indexes against the database driver's parameter binding (#1979).

Two things are held here. Every partial or expression index the models declare must be in the
audit page, so a new one is a recorded decision and not an accident. And the page's verdicts
for the indexes where it matters are the planner's, asked with ``EXPLAIN (GENERIC_PLAN)``: the
plan a server-binding driver would get, where every parameter is unknown.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Column, text

from backend.app.db.models import Base
from backend.tests.support.driver_guard import AUDIT, assert_client_side_binding

_AUDIT = Path(__file__).resolve().parents[3] / AUDIT
#: A column ordering (`created_at DESC`) is not an expression.
_PLAIN_ORDERING = re.compile(r"^\w+ DESC$")


def _partial_or_expression_indexes() -> set[str]:
    found: set[str] = set()
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            partial = index.dialect_options["postgresql"].get("where") is not None
            expression = any(
                not isinstance(e, Column) and not _PLAIN_ORDERING.match(str(e))
                for e in index.expressions
            )
            if partial or expression:
                assert index.name is not None
                found.add(index.name)
    return found


def _audited() -> set[str]:
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", _AUDIT.read_text(encoding="utf-8"), re.M))


def test_every_partial_or_expression_index_has_a_recorded_verdict() -> None:
    declared = _partial_or_expression_indexes()
    assert declared, "the scan found no partial or expression index: it is broken"
    audited = _audited()
    assert declared - audited == set(), (
        f"add a row to {AUDIT} for each of these: how the app queries it, and whether a "
        "generic plan (server-side binding) still reaches it"
    )
    assert (
        audited - declared == set()
    ), f"the audit lists indexes that no longer exist: {audited - declared}"


def test_the_driver_in_use_binds_on_the_client(db_session: Any) -> None:
    assert_client_side_binding(db_session.connection())


# (index, the statement as a server-binding driver would send it, whether its generic plan
# reaches the index). A constant on one side and a parameter on the other is what defeats a
# partial predicate; a literal in the statement, an `IS NULL` or a matching expression does not.
_GENERIC_PLANS = [
    (
        "ix_llm_invocations_status_open",
        "SELECT id FROM llm_invocations WHERE status = $1 AND created_at < $2",
        False,
    ),
    (
        "ix_llm_invocations_status_open",
        "SELECT id FROM llm_invocations WHERE status IN ('pending', 'running') AND created_at < $1",
        True,
    ),
    (
        "ix_llm_invocations_rca_incident",
        "SELECT id FROM llm_invocations WHERE kind = $1 AND status = $2 AND (request ->> $3) = $4 "
        "ORDER BY created_at DESC, id DESC LIMIT $5",
        False,
    ),
    (
        "ix_runs_results_unrolled",
        "SELECT id FROM runs WHERE status = $1 AND results_rolled_up_at IS NULL",
        False,
    ),
    (
        "ix_runs_results_unrolled",
        "SELECT id FROM runs WHERE status = 'succeeded' AND results_rolled_up_at IS NULL",
        True,
    ),
    ("ix_runs_triggered_by", "SELECT id FROM runs WHERE triggered_by = ANY($1)", True),
    (
        "ix_notifications_user_unread",
        "SELECT id FROM notifications WHERE user_id = $1 AND read_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT $2",
        True,
    ),
    # The retention sweeps send the JSON key, the type name and the `?` operand as parameters.
    (
        "ix_results_unpurged_created",
        "SELECT id FROM results WHERE sample_failures_purged_at IS NULL "
        "AND sample_failures IS NOT NULL AND jsonb_typeof(sample_failures) <> $1 "
        "AND created_at < $2 LIMIT $3",
        False,
    ),
    (
        "ix_results_unpurged_created",
        "SELECT id FROM results WHERE sample_failures_purged_at IS NULL "
        "AND sample_failures IS NOT NULL AND jsonb_typeof(sample_failures) <> 'null' "
        "AND created_at < $1 LIMIT $2",
        True,
    ),
    (
        "ix_results_unpurged_observed",
        "SELECT id FROM results WHERE jsonb_typeof(observed_value -> $1) = $2 "
        "AND created_at < $3 LIMIT $4",
        False,
    ),
    (
        "ix_results_unpurged_observed",
        "SELECT id FROM results WHERE jsonb_typeof(observed_value -> 'observed_value') = 'array' "
        "AND created_at < $1 LIMIT $2",
        True,
    ),
    (
        "ix_results_unpurged_unparsed",
        "SELECT id FROM results WHERE observed_value ? $1 AND created_at < $2 LIMIT $3",
        False,
    ),
    (
        "ix_results_unpurged_unparsed",
        "SELECT id FROM results WHERE observed_value ? 'unparsed_value' "
        "AND created_at < $1 LIMIT $2",
        True,
    ),
    # The marker's `:` separators are parameters too, so the expression no longer matches.
    (
        "ix_pipeline_runs_marker",
        "SELECT id FROM pipeline_runs "
        "WHERE (provider || $1 || pipeline_or_dag_id || $2 || provider_run_id) = ANY($3)",
        False,
    ),
    (
        "ix_pipeline_runs_marker",
        "SELECT id FROM pipeline_runs "
        "WHERE (provider || ':' || pipeline_or_dag_id || ':' || provider_run_id) = ANY($1)",
        True,
    ),
    ("uq_users_email_lower", "SELECT id FROM users WHERE lower(email) = lower($1)", True),
]


@pytest.mark.parametrize(
    ("index", "statement", "reaches"),
    _GENERIC_PLANS,
    ids=[f"{i}-{'reached' if r else 'missed'}-{n}" for n, (i, _s, r) in enumerate(_GENERIC_PLANS)],
)
def test_the_audit_verdict_is_what_a_generic_plan_does(
    db_session: Any, index: str, statement: str, reaches: bool
) -> None:
    """``enable_seqscan = off`` removes the empty-table preference for a scan, so an index the
    planner can use is the one it picks; it cannot make an unprovable predicate usable."""
    connection = db_session.connection()
    # The other non-unique indexes of the table are dropped (inside the test's transaction, rolled
    # back with it): with them present the planner may prefer a plain index on an empty table, so
    # "reached" would say nothing about the partial one and "missed" would hide behind them.
    rivals = connection.execute(
        text(
            "SELECT other.indexrelid::regclass::text FROM pg_index AS target "
            "JOIN pg_index AS other ON other.indrelid = target.indrelid "
            "WHERE target.indexrelid = CAST(:index AS regclass) "
            "AND other.indexrelid <> target.indexrelid "
            "AND NOT other.indisunique AND NOT other.indisprimary"
        ),
        {"index": index},
    ).scalars()
    for rival in list(rivals):
        connection.execute(text(f"DROP INDEX {rival}"))
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        row[0] for row in connection.execute(text(f"EXPLAIN (GENERIC_PLAN) {statement}"))
    )
    assert (
        index in plan
    ) is reaches, (
        f"{index}: the audit says a generic plan {'reaches' if reaches else 'misses'} it:\n{plan}"
    )
