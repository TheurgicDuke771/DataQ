"""Real-broker E2E: dispatch -> Redis -> in-process worker -> run_service -> Postgres.

Only the Snowflake adapter is mocked; the broker hop and DB round-trip are real,
so this covers what the unit tests can't (the request_id header + task message
actually serialising over Redis, the worker running in its own context).

Opt-in: skips unless DATABASE_URL + REDIS_URL are set (CI provides both via
service containers). Uses real commits + a TRUNCATE teardown rather than the
rolled-back db_session fixture, because the worker runs on a separate session
and would not see uncommitted savepoint data.
"""

import os
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from backend.app.datasources.base import CheckOutcome, SuiteOutcome

requires_real_infra = pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL") and os.environ.get("REDIS_URL")),
    reason="E2E needs DATABASE_URL + REDIS_URL pointing at real Postgres + Redis",
)


def _fake_runner(**_kwargs: Any) -> Any:
    class _Runner:
        def run_checks(self, *, table: str, schema: str | None, checks: list[Any]) -> SuiteOutcome:
            return SuiteOutcome(
                success=True,
                checks=[
                    CheckOutcome(
                        "expect_table_row_count_to_be_between",
                        success=True,
                        observed_value={"observed_value": 42},
                        expected_value={"min_value": 1},
                    )
                ],
            )

    return _Runner()


@requires_real_infra
def test_probe_round_trip_over_real_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.contrib.testing.worker import start_worker

    from backend.app.core.config import get_settings
    from backend.app.core.logging import request_id_var
    from backend.app.db.base import Base
    from backend.app.db.models import Result, Run, User
    from backend.app.db.session import engine, get_session
    from backend.app.services.probe import ensure_probe_fixtures
    from backend.app.worker import tasks
    from backend.app.worker.celery_app import celery_app

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover
        pytest.skip("DATABASE_URL not reachable")

    Base.metadata.create_all(engine)
    monkeypatch.setattr(tasks, "build_snowflake_runner", _fake_runner)

    session = get_session()
    try:
        user = User(
            aad_object_id=f"e2e-{uuid.uuid4()}", email="e2e@example.com", display_name="E2E"
        )
        session.add(user)
        session.commit()
        _, suite, _ = ensure_probe_fixtures(session, user=user, settings=get_settings())
        run = Run(suite_id=suite.id, status="queued", triggered_by="e2e")
        session.add(run)
        session.commit()
        run_id = run.id

        with start_worker(celery_app, perform_ping_check=False, loglevel="info"):
            request_id_var.set("e2e-REQ")
            tasks.run_suite.delay(str(run_id), "ORDERS")  # real publish over Redis

            deadline = time.time() + 30
            final: str | None = None
            while time.time() < deadline:
                session.expire_all()
                current = session.get(Run, run_id)
                if current is not None and current.status in ("succeeded", "failed"):
                    final = current.status
                    break
                time.sleep(0.5)

        assert final == "succeeded", f"run did not succeed over the broker (got {final})"
        results = session.scalars(select(Result).where(Result.run_id == run_id)).all()
        assert len(results) == 1
        assert results[0].status == "passed"
        assert results[0].observed_value == {"observed_value": 42}
    finally:
        session.execute(text("TRUNCATE results, runs, checks, suites, connections, users CASCADE"))
        session.commit()
        session.close()
