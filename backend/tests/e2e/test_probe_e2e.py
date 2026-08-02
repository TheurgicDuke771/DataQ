"""Real-broker E2E: dispatch -> Redis -> in-process worker -> run_service -> Postgres.

Only the Snowflake adapter is mocked; the broker hop and DB round-trip are real,
so this covers what the unit tests can't (the request_id header + task message
actually serialising over Redis, the worker running in its own context).

Opt-in: skips unless DATAQ_E2E=1 is set EXPLICITLY, alongside DATABASE_URL +
REDIS_URL pointing at real Postgres + Redis (CI's service-container job sets
all three; scripts/test-backend.sh sets all three for local parity).

DATAQ_E2E is required, not merely DATABASE_URL + REDIS_URL, because conftest.py
(#1130) now points DATABASE_URL at the resolved TEST_DATABASE_URL whenever
DATABASE_URL is unset — a deliberate fix for a *different* problem (SessionLocal
silently reaching the dev DB). A side effect: what used to be a two-factor gate,
where DATABASE_URL required a conscious action, collapsed to one (REDIS_URL
alone), and REDIS_URL ships pre-populated in .env.app.example — so a bare
`pytest` for anyone with a plausible local Redis env var would silently start
spinning up a real embedded Celery worker + doing real commits/TRUNCATEs.
DATAQ_E2E is a value conftest never sets on its own, so activating this test is
always someone's conscious choice again. Uses real commits + a TRUNCATE
teardown rather than the rolled-back db_session fixture, because the worker
runs on a separate session and would not see uncommitted savepoint data.
"""

import os
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from backend.app.datasources.base import CheckOutcome, SuiteOutcome

requires_real_infra = pytest.mark.skipif(
    not (
        os.environ.get("DATAQ_E2E")
        and os.environ.get("DATABASE_URL")
        and os.environ.get("REDIS_URL")
    ),
    reason=(
        "E2E needs DATAQ_E2E=1 (explicit opt-in) plus DATABASE_URL + REDIS_URL "
        "pointing at real Postgres + Redis"
    ),
)


def _fake_runner(**_kwargs: Any) -> Any:
    class _Runner:
        def run_checks(
            self,
            *,
            table: str,
            schema: str | None,
            checks: list[Any],
            index_columns: list[str] | None = None,
        ) -> SuiteOutcome:
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
    monkeypatch.setattr(tasks, "build_check_runner", _fake_runner)

    session = get_session()
    try:
        user = User(
            aad_object_id=f"e2e-{uuid.uuid4()}", email="e2e@example.com", display_name="E2E"
        )
        session.add(user)
        session.commit()
        _, suite, _ = ensure_probe_fixtures(session, user=user, settings=get_settings())
        # The worker resolves the table from the suite's target (#215); pin it so
        # the run is deterministic regardless of the probe-table env setting.
        suite.target = {"table": "ORDERS"}
        run = Run(suite_id=suite.id, status="queued", triggered_by="e2e")
        session.add(run)
        session.commit()
        run_id = run.id

        # Route this run to a unique queue that only our embedded worker consumes,
        # so a developer's docker-compose `worker` (on the default queue, same
        # broker) can't steal the task and fail it with the real Snowflake
        # adapter. CI has no competing worker; this just makes local runs robust.
        queue = f"e2e-{uuid.uuid4()}"
        with start_worker(celery_app, perform_ping_check=False, loglevel="info", queues=[queue]):
            request_id_var.set("e2e-REQ")
            tasks.run_suite.apply_async(args=[str(run_id)], queue=queue)  # real publish over Redis

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
        assert results[0].status == "pass"
        assert results[0].observed_value == {"observed_value": 42}
    finally:
        session.execute(text("TRUNCATE results, runs, checks, suites, connections, users CASCADE"))
        session.commit()
        session.close()
