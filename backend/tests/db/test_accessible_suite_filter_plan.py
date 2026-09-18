"""`accessible_suite_filter` — plan shape + population equivalence (#1986).

The `X-Total-Count` COUNT on `/runs` and `/incidents` has no ORDER BY, so #1245's ordering
indexes cannot serve it; what it *was* paying was a row-wise join of the accessible-suite
set against every candidate row. These tests pin the InitPlan shape that removes it, and
prove the rewritten predicate selects exactly the population the subquery form did.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, text

from backend.app.db.models import Connection, Incident, Run, Share, Suite, User
from backend.app.services import incident_service, suite_service
from backend.app.services import run_service as run_svc


def _user(db_session: Any, *, role: str = "member") -> User:
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.test", display_name="u", role=role)
    db_session.add(user)
    db_session.commit()
    return user


def _suite(db_session: Any, owner: User) -> Suite:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.commit()
    return suite


def _plan(connection: Any, statement: Any) -> str:
    compiled = statement.compile(bind=connection.engine, compile_kwargs={"literal_binds": True})
    return "\n".join(row[0] for row in connection.execute(text(f"EXPLAIN {compiled}")))


def _count(db_session: Any, model: Any, predicate: Any) -> int:
    return db_session.scalar(select(func.count()).select_from(model).where(predicate)) or 0


def _runs_count_statement(user_id: uuid.UUID, *, include_all: bool = False) -> Any:
    return (
        select(func.count())
        .select_from(Run)
        .where(
            *run_svc._run_filters(
                user_id=user_id, suite_id=None, status=None, include_all=include_all
            )
        )
    )


def _incidents_count_statement(user_id: uuid.UUID, *, include_all: bool = False) -> Any:
    return (
        select(func.count())
        .select_from(Incident)
        .where(
            *incident_service._incident_filters(
                user_id=user_id,
                include_all=include_all,
                asset_id=None,
                suite_id=None,
                state=None,
            )
        )
    )


def test_visibility_predicate_renders_as_an_array_not_a_subquery_membership() -> None:
    """`= ANY (array(...))`, not `IN (SELECT ...)` — the difference is one InitPlan
    versus a join evaluated per candidate row.
    """
    sql = str(suite_service.accessible_suite_filter(Run.suite_id, uuid.uuid4()))
    assert sql.startswith("runs.suite_id = ANY (array("), sql


def test_runs_count_plan_has_no_row_wise_suite_join(db_session: Any) -> None:
    """The accessible-suite set is resolved once as an InitPlan; nothing joins `suites`
    against `runs`. `enable_seqscan = off` gives the assertion teeth on an empty table —
    without it the planner has no reason to pick any join shape at all.
    """
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    plan = _plan(connection, _runs_count_statement(uuid.uuid4()))
    assert "InitPlan" in plan, plan
    assert "Join" not in plan, plan


def test_incidents_count_plan_has_no_row_wise_suite_join(db_session: Any) -> None:
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    plan = _plan(connection, _incidents_count_statement(uuid.uuid4()))
    assert "InitPlan" in plan, plan
    assert "Join" not in plan, plan


def test_runs_list_plan_has_no_row_wise_suite_join(db_session: Any) -> None:
    """The page shares the predicate with its total, so the list must not regress either."""
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    connection.execute(text("SET LOCAL enable_sort = off"))
    statement = (
        select(Run)
        .where(
            *run_svc._run_filters(
                user_id=uuid.uuid4(), suite_id=None, status=None, include_all=False
            )
        )
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(50)
    )
    plan = _plan(connection, statement)
    assert "ix_runs_created_id" in plan, plan
    assert "Join" not in plan, plan


def test_population_matches_the_subquery_form_for_owner_sharee_and_outsider(
    db_session: Any,
) -> None:
    """Owned, shared and invisible rows all land on the same side under both forms — a
    faster predicate that widens (or narrows) visibility would be a security defect.
    """
    owner = _user(db_session)
    sharee = _user(db_session)
    outsider = _user(db_session)
    own_suite = _suite(db_session, owner)
    other_suite = _suite(db_session, _user(db_session))
    db_session.add(Share(suite_id=own_suite.id, user_id=sharee.id, permission="view"))
    db_session.add_all(
        [
            Run(suite_id=own_suite.id, status="queued", triggered_by="a"),
            Run(suite_id=own_suite.id, status="queued", triggered_by="b"),
            Run(suite_id=other_suite.id, status="queued", triggered_by="c"),
        ]
    )
    db_session.commit()

    for user, expected in ((owner, 2), (sharee, 2), (outsider, 0)):
        legacy = _count(
            db_session, Run, Run.suite_id.in_(suite_service.accessible_suite_ids(user.id))
        )
        rewritten = _count(
            db_session, Run, suite_service.accessible_suite_filter(Run.suite_id, user.id)
        )
        assert legacy == rewritten == expected, (user.email, legacy, rewritten)

    admin_legacy = _count(
        db_session,
        Run,
        Run.suite_id.in_(suite_service.accessible_suite_ids(outsider.id, include_all=True)),
    )
    admin_rewritten = _count(
        db_session,
        Run,
        suite_service.accessible_suite_filter(Run.suite_id, outsider.id, include_all=True),
    )
    assert admin_legacy == admin_rewritten == 3


def test_empty_accessible_set_selects_nothing(db_session: Any) -> None:
    """An empty `ARRAY(...)` must behave like `IN (<empty>)` — false, never true. The
    two constructs differ on NULL handling, so the degenerate case is pinned.
    """
    owner = _user(db_session)
    suite = _suite(db_session, owner)
    db_session.add(Run(suite_id=suite.id, status="queued", triggered_by="a"))
    db_session.commit()
    stranger = uuid.uuid4()
    assert (
        _count(db_session, Run, suite_service.accessible_suite_filter(Run.suite_id, stranger)) == 0
    )
