"""Orchestration-poll health becomes a fact about the connection (#828).

The bug this pins is not the expired credential — it's that a failing poll was
**invisible**. It logged `orchestration_poll_failed` every 10 minutes and moved on: the
connection still read as configured, the lineage UI showed its ordinary empty state, and
the beat task returned success with an `errors` count nobody was watching. Prod lineage
was dark for six days and the product said nothing was wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.db.models import Connection, User
from backend.app.services import orchestration_service


def _dbt_connection(db_session: Any) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"dbt-{uuid.uuid4().hex[:8]}",
        type="dbt",
        env="dev",
        config={"project_name": "p", "artifacts_uri": "adls://a/raw", "jobs": ["dbt"]},
        secret_ref="conn-x",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


class TestFailureBecomesVisible:
    def test_a_failed_poll_is_recorded_on_the_connection(self, db_session: Any) -> None:
        conn = _dbt_connection(db_session)
        assert conn.consecutive_poll_failures == 0
        assert conn.last_poll_error is None

        orchestration_service.record_poll_failure(
            db_session, connection_id=conn.id, exc=PermissionError("AuthenticationFailed")
        )

        db_session.refresh(conn)
        assert conn.consecutive_poll_failures == 1
        assert conn.last_poll_error  # a classified reason is present
        assert conn.last_polled_at is not None

    def test_consecutive_failures_accumulate(self, db_session: Any) -> None:
        # The counter, not a boolean — it is what lets the UI say "failing for ~6 days"
        # rather than just "failing", and what an alert threshold would ride on.
        conn = _dbt_connection(db_session)
        for _ in range(3):
            orchestration_service.record_poll_failure(
                db_session, connection_id=conn.id, exc=RuntimeError("boom")
            )
        db_session.refresh(conn)
        assert conn.consecutive_poll_failures == 3

    def test_a_success_clears_the_streak(self, db_session: Any) -> None:
        conn = _dbt_connection(db_session)
        orchestration_service.record_poll_failure(
            db_session, connection_id=conn.id, exc=RuntimeError("boom")
        )
        orchestration_service.record_poll_success(db_session, connection=conn)

        db_session.refresh(conn)
        assert conn.consecutive_poll_failures == 0
        assert conn.last_poll_error is None
        assert conn.last_polled_at is not None

    def test_a_connection_deleted_mid_sweep_does_not_explode(self, db_session: Any) -> None:
        # The poll iterates a snapshot of connections; one can be deleted underneath it.
        orchestration_service.record_poll_failure(
            db_session, connection_id=uuid.uuid4(), exc=RuntimeError("boom")
        )  # must be a no-op, not an AttributeError


class TestTheStoredReasonCannotLeakACredential:
    """The column is served by the API and rendered in the UI — raw exception text is
    not safe to put there. The real failure carried the SAS query string."""

    @pytest.mark.parametrize(
        "secret",
        [
            "sig=abc123DEADBEEF%2Fxyz",  # an ADLS SAS signature
            "postgresql://user:hunter2@host/db",  # a DSN with a password
            "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",  # a token
        ],
    )
    def test_the_secret_never_reaches_the_column(self, db_session: Any, secret: str) -> None:
        conn = _dbt_connection(db_session)
        # Exactly the shape of the real prod failure: the credential is IN the message.
        exc = PermissionError(
            f"AuthenticationFailed: Server failed to authenticate the request. {secret}"
        )

        orchestration_service.record_poll_failure(db_session, connection_id=conn.id, exc=exc)

        db_session.refresh(conn)
        stored = conn.last_poll_error or ""
        assert secret not in stored
        assert "hunter2" not in stored
        assert "sig=" not in stored
        assert len(stored) <= 512  # and it always fits the column

    def test_the_reason_is_still_useful(self, db_session: Any) -> None:
        # Redaction must not reduce it to nothing — an operator has to be able to act.
        conn = _dbt_connection(db_session)
        orchestration_service.record_poll_failure(
            db_session, connection_id=conn.id, exc=PermissionError("AuthenticationFailed")
        )
        db_session.refresh(conn)
        assert conn.last_poll_error
        assert len(conn.last_poll_error) > 3


class TestTheLineageEmptyStateStopsLying:
    def test_a_failing_dbt_poll_surfaces_as_a_failing_lineage_source(self, db_session: Any) -> None:
        from backend.app.services.asset_view_service import failing_lineage_sources

        conn = _dbt_connection(db_session)
        assert failing_lineage_sources(db_session) == []  # healthy → nothing to say

        orchestration_service.record_poll_failure(
            db_session, connection_id=conn.id, exc=PermissionError("AuthenticationFailed")
        )

        failing = failing_lineage_sources(db_session)
        assert len(failing) == 1
        assert failing[0].connection_id == conn.id
        assert failing[0].consecutive_failures == 1
        # This is what stops the UI rendering "No lineage recorded" over a broken pipe.

    def test_a_recovered_source_stops_being_reported(self, db_session: Any) -> None:
        from backend.app.services.asset_view_service import failing_lineage_sources

        conn = _dbt_connection(db_session)
        orchestration_service.record_poll_failure(
            db_session, connection_id=conn.id, exc=RuntimeError("boom")
        )
        assert failing_lineage_sources(db_session)

        orchestration_service.record_poll_success(db_session, connection=conn)
        assert failing_lineage_sources(db_session) == []


class TestThePollWritesHealth:
    """The wiring: the beat task must actually call the bookkeeping, and a bookkeeping
    error must never take down the sweep it is reporting on."""

    def test_a_raising_provider_marks_the_connection_unhealthy(
        self, db_session: Any, monkeypatch: Any
    ) -> None:
        from backend.app.worker import tasks

        conn = _dbt_connection(db_session)

        class _Store:
            def get(self, name: str) -> str:
                return "secret"

            def set(self, name: str, value: str) -> None: ...

            def delete(self, name: str) -> None: ...

        class _Provider:
            provider = "dbt"
            resource_config_key = "project_name"

            def list_recent_runs(self, config: Any, secret: str, since: Any) -> Any:
                raise PermissionError("AuthenticationFailed: SAS expired sig=LEAKME")

        monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: _Provider())

        summary = tasks._poll_orchestration_runs(
            db_session,
            secret_store=_Store(),
            lookback=timedelta(minutes=15),
            now=datetime.now(UTC),
        )

        assert summary["errors"] >= 1
        db_session.refresh(conn)
        # The regression this pins (#828 AC-4): the poll no longer just logs and shrugs.
        assert conn.consecutive_poll_failures == 1
        assert conn.last_poll_error
        assert "LEAKME" not in conn.last_poll_error


def _warehouse_connection(db_session: Any, conn_type: str = "snowflake") -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config={"account": "ACCT"} if conn_type == "snowflake" else {"workspace_url": "https://x"},
        secret_ref="ref",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


class TestWarehouseLineageStatusStopsLying:
    """A degraded (view-level-only) or failing warehouse-lineage source must be
    surfaced so the graph isn't shown as complete + current (#858 slice 4)."""

    def test_never_refreshed_source_is_not_listed(self, db_session: Any) -> None:
        from backend.app.services.asset_view_service import warehouse_lineage_status

        _warehouse_connection(db_session)  # no lineage state yet
        assert warehouse_lineage_status(db_session) == []

    def test_healthy_full_tier_source_is_not_listed(self, db_session: Any) -> None:
        from datetime import UTC, datetime

        from backend.app.services.asset_view_service import warehouse_lineage_status

        conn = _warehouse_connection(db_session)
        conn.lineage_last_refresh_at = datetime.now(UTC)
        conn.lineage_last_tier = "snowflake_get_lineage"
        conn.lineage_degraded_reason = None
        conn.lineage_last_error = None
        db_session.flush()
        # refreshed, full tier, no error → nothing to warn about
        assert warehouse_lineage_status(db_session) == []

    def test_degraded_tier_is_surfaced(self, db_session: Any) -> None:
        from datetime import UTC, datetime

        from backend.app.services.asset_view_service import warehouse_lineage_status

        conn = _warehouse_connection(db_session)
        conn.lineage_last_refresh_at = datetime.now(UTC)
        conn.lineage_last_tier = "snowflake_object_dependencies"
        conn.lineage_degraded_reason = "view-level lineage only — richer tiers need Enterprise"
        db_session.flush()

        status = warehouse_lineage_status(db_session)
        assert len(status) == 1
        assert status[0].connection_id == conn.id
        assert status[0].degraded_reason is not None
        assert "view-level" in status[0].degraded_reason
        assert status[0].last_error is None

    def test_failing_refresh_is_surfaced_with_classified_error(self, db_session: Any) -> None:
        from datetime import UTC, datetime

        from backend.app.services.asset_view_service import warehouse_lineage_status

        conn = _warehouse_connection(db_session, "unity_catalog")
        conn.lineage_last_refresh_at = datetime.now(UTC)
        conn.lineage_last_error = "the datasource could not be reached"
        db_session.flush()

        status = warehouse_lineage_status(db_session)
        assert len(status) == 1
        assert status[0].last_error == "the datasource could not be reached"
