"""A batched native engine gets each check's last evaluated result (#2227: DQX stream resume)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.datasources.base import CheckOutcome
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.services import run_service


def _graph(db: Any) -> tuple[Suite, Check, Check]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex.io")
    db.add(user)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="unity_catalog",
        env="dev",
        config={"workspace_url": "https://w.example", "warehouse_id": "w"},
        secret_ref="kv",
        created_by=user.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=user.id,
        target={"table": "t", "schema": "s"},
    )
    db.add(suite)
    db.flush()
    checks = [
        Check(
            suite_id=suite.id,
            name=f"chk-{i}",
            kind="expectation",
            expectation_type="dqx:is_not_null",
            engine="dqx",
            config={"column": "a", "mode": "stream"},
        )
        for i in range(2)
    ]
    db.add_all(checks)
    db.flush()
    return suite, checks[0], checks[1]


def _result(db: Any, suite: Suite, check: Check, status: str, observed: Any, age: int) -> None:
    run = Run(suite_id=suite.id, status="succeeded")
    db.add(run)
    db.flush()
    db.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status=status,
            observed_value=observed,
            created_at=datetime.now(UTC) - timedelta(minutes=age),
        )
    )
    db.flush()


def test_the_latest_evaluated_result_wins_and_errors_never_move_the_resume_point(
    db_session: Any,
) -> None:
    suite, first, second = _graph(db_session)
    _result(db_session, suite, first, "pass", {"stream": {"next_version": 3}}, age=30)
    _result(db_session, suite, first, "fail", {"stream": {"next_version": 7}}, age=20)
    # A later errored or skipped run carries no evaluation: it must not reset or move the point.
    _result(db_session, suite, first, "error", None, age=10)
    _result(db_session, suite, first, "skip", {"reason": "x"}, age=5)

    previous = run_service.latest_evaluated_observed(db_session, [second, first])

    assert previous == [None, {"stream": {"next_version": 7}}]


def test_a_later_snapshot_result_is_the_latest_so_a_mode_flip_restarts_the_stream(
    db_session: Any,
) -> None:
    suite, first, _second = _graph(db_session)
    _result(db_session, suite, first, "pass", {"stream": {"next_version": 3}}, age=30)
    _result(db_session, suite, first, "pass", {"failing_rows": 0, "rows": 9}, age=10)

    assert run_service.latest_evaluated_observed(db_session, [first]) == [
        {"failing_rows": 0, "rows": 9}
    ]


def test_the_batch_hook_receives_each_checks_previous_result_in_order() -> None:
    received: dict[str, Any] = {}

    class _Runner:
        supported_native_engines = frozenset({"dqx"})

        def run_native_checks(
            self, engine: str, specs: list[Any], *, table: str, schema: str | None, **kw: Any
        ) -> list[CheckOutcome]:
            received.update(kw)
            return [CheckOutcome(expectation_type=t, success=True) for _k, t, _c in specs]

    checks = [
        Check(id=uuid.uuid4(), kind="expectation", expectation_type=t, engine="dqx", config={})
        for t in ("dqx:is_not_null", "dqx:is_not_empty")
    ]
    asked: list[list[Check]] = []

    def _previous(batch: list[Check]) -> list[dict[str, Any] | None]:
        asked.append(batch)
        return [{"n": i} for i, _ in enumerate(batch)]

    list(
        run_service._run_outcome_phases(
            _Runner(),  # type: ignore[arg-type]
            table="t",
            schema="s",
            checks=checks,
            native_previous=_previous,
        )
    )

    assert asked == [checks]
    assert received == {"previous": [{"n": 0}, {"n": 1}]}
