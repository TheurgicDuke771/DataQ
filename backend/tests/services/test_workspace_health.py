"""Workspace-wide poll-staleness check (#1052) — the signal that cannot lie.

The defining test here is the first one: **the worker writes nothing at all and the
alert still fires.** Every #905-class incident (#852, #854, the wedged broker
reconnect) had a worker that looked alive and wrote nothing, so these tests never
simulate a worker — they only arrange DB rows, which is exactly the information the
check is allowed to use.

Publisher delivery is faked at the seam (`get_health_publisher`), never mocked past
it: the delivered-first tests depend on the real control flow between publish and
the `workspace_health` flag write.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import select

from backend.app.alerting.base import AlertUndeliverableError, PollStalenessReport
from backend.app.core.config import get_settings
from backend.app.db.models import (
    Connection,
    Share,
    Suite,
    TriggerBinding,
    User,
    WorkspaceHealth,
)
from backend.app.orchestration.base import RunUpdate
from backend.app.services import orchestration_service
from backend.app.services import workspace_health_service as svc


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    return user


def _orch_connection(
    db_session: Any,
    *,
    conn_type: str = "airflow",
    last_polled_at: datetime | None = None,
    created_at: datetime | None = None,
    env: str = "dev",
) -> Connection:
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env=env,
        config={"base_url": "http://x", "project_name": "p", "factory_name": "f"},
        secret_ref="ref",
        created_by=_user(db_session).id,
        last_polled_at=last_polled_at,
    )
    db_session.add(conn)
    db_session.flush()
    if created_at is not None:
        conn.created_at = created_at
        db_session.flush()
    return conn


class _CapturingPublisher:
    def __init__(self, fail: bool = False, undeliverable: bool = False) -> None:
        self.reports: list[PollStalenessReport] = []
        self.fail = fail
        self.undeliverable = undeliverable

    def publish_poll_staleness(self, session: Any, report: PollStalenessReport) -> bool:
        if self.fail:
            raise RuntimeError("every channel failed")
        if self.undeliverable:
            raise AlertUndeliverableError("no alert channel is configured")
        self.reports.append(report)
        return True


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch) -> _CapturingPublisher:
    pub = _CapturingPublisher()
    monkeypatch.setattr(svc, "get_health_publisher", lambda: pub)
    return pub


def _flag(db_session: Any) -> WorkspaceHealth | None:
    row = db_session.execute(
        select(WorkspaceHealth).where(WorkspaceHealth.key == svc.POLL_STALENESS_KEY)
    ).scalar_one_or_none()
    return cast("WorkspaceHealth | None", row)


NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


class TestTheAlertFiresWithoutTheWorker:
    def test_worker_writes_nothing_at_all_and_the_alert_still_fires(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """The #1052 acceptance test verbatim: nothing here simulates a worker —
        two connections whose poll writes simply stopped are all it takes."""
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        _orch_connection(db_session, conn_type="adf", last_polled_at=NOW - timedelta(hours=3))

        outcome = svc.run_poll_staleness_check(db_session, now=NOW)

        assert outcome == "alerted"
        assert len(publisher.reports) == 1
        report = publisher.reports[0]
        assert report.is_failing
        assert report.connection_count == 2
        assert report.most_recent_polled_at == NOW - timedelta(hours=2)
        flag = _flag(db_session)
        assert flag is not None and flag.alerted_at is not None

    def test_never_polled_connections_go_stale_from_their_creation_moment(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """A poller that never ran at ALL (wrong image, task never registered) writes
        no `last_polled_at` anywhere — 'we have not looked yet' must not read as
        'nothing to report' (#828), so `created_at` is the fallback reference."""
        _orch_connection(db_session, last_polled_at=None, created_at=NOW - timedelta(hours=1))

        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"
        assert publisher.reports[0].most_recent_polled_at is None

    def test_fresh_polls_mean_nothing_to_say(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        _orch_connection(db_session, last_polled_at=NOW - timedelta(minutes=5))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "ok"
        assert publisher.reports == []
        flag = _flag(db_session)
        assert flag is None or flag.alerted_at is None

    def test_no_orchestration_connections_is_not_a_dead_loop(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """Nothing to poll ≠ polling is dead. A datasource connection (snowflake)
        must not be counted into the orchestration population either."""
        conn = Connection(
            name=f"snowflake-{uuid.uuid4().hex[:8]}",
            type="snowflake",
            env="dev",
            config={"account": "ACCT"},
            secret_ref="ref",
            created_by=_user(db_session).id,
        )
        db_session.add(conn)
        db_session.flush()
        conn.created_at = NOW - timedelta(days=30)
        db_session.flush()

        assert svc.run_poll_staleness_check(db_session, now=NOW) == "ok"
        assert publisher.reports == []

    def test_disabled_by_config(
        self,
        db_session: Any,
        publisher: _CapturingPublisher,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("POLL_STALENESS_ALERT_AFTER_S", "0")
        get_settings.cache_clear()
        _orch_connection(db_session, last_polled_at=NOW - timedelta(days=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "disabled"
        assert publisher.reports == []


class TestDeliveredFirst:
    """#843: the flag records what was DELIVERED, never what we intended to send."""

    def test_a_failed_publish_leaves_the_flag_unset_so_the_next_tick_retries(
        self, db_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing = _CapturingPublisher(fail=True)
        monkeypatch.setattr(svc, "get_health_publisher", lambda: failing)
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))

        with pytest.raises(RuntimeError):
            svc.run_poll_staleness_check(db_session, now=NOW)
        db_session.rollback()

        flag = _flag(db_session)
        assert flag is None or flag.alerted_at is None

    def test_no_configured_channel_leaves_the_flag_unset_and_retries(
        self, db_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fresh-install trap (review finding): every channel unconfigured must
        NOT stamp the flag — the incident stays outstanding, and the first tick
        after an operator wires a channel delivers the original edge."""
        undeliverable = _CapturingPublisher(undeliverable=True)
        monkeypatch.setattr(svc, "get_health_publisher", lambda: undeliverable)
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))

        assert svc.run_poll_staleness_check(db_session, now=NOW) == "undeliverable"
        flag = _flag(db_session)
        assert flag is None or flag.alerted_at is None

        # The undeliverable path's rollback discarded this test's UNCOMMITTED
        # fixture row (prod connections are long-committed); re-seed the same
        # still-dead state before the second tick.
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))

        # The operator wires up a channel; the loop is STILL dead — the edge
        # must now actually go out.
        configured = _CapturingPublisher()
        monkeypatch.setattr(svc, "get_health_publisher", lambda: configured)
        assert svc.run_poll_staleness_check(db_session, now=NOW + timedelta(minutes=10)) == (
            "alerted"
        )
        assert [r.state for r in configured.reports] == ["failing"]

    def test_undeliverable_recovery_keeps_the_flag_set(
        self, db_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Channels unconfigured AFTER a delivered failing edge: the operator was
        told about a failure and must not have the recovery silently dropped —
        the flag stays set until a recovery actually goes out."""
        delivered = _CapturingPublisher()
        monkeypatch.setattr(svc, "get_health_publisher", lambda: delivered)
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"

        conn = db_session.execute(select(Connection)).scalars().first()
        conn.last_polled_at = NOW + timedelta(minutes=1)
        db_session.flush()

        undeliverable = _CapturingPublisher(undeliverable=True)
        monkeypatch.setattr(svc, "get_health_publisher", lambda: undeliverable)
        outcome = svc.run_poll_staleness_check(db_session, now=NOW + timedelta(minutes=2))
        assert outcome == "undeliverable"
        flag = _flag(db_session)
        assert flag is not None and flag.alerted_at is not None

    def test_recovery_fires_only_after_a_delivered_failing_edge(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"

        # Poll writes resume (the connection got polled again).
        conn = db_session.execute(select(Connection)).scalars().first()
        conn.last_polled_at = NOW + timedelta(minutes=1)
        db_session.flush()

        outcome = svc.run_poll_staleness_check(db_session, now=NOW + timedelta(minutes=2))
        assert outcome == "recovered"
        assert [r.state for r in publisher.reports] == ["failing", "recovered"]
        flag = _flag(db_session)
        assert flag is not None and flag.alerted_at is None

    def test_no_recovery_edge_without_an_outstanding_alert(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """Fresh polls with no delivered FAILING edge → silence, not an all-clear.
        An unprompted 'recovered' message is the #837 rule violated from the other
        side: the operator's last delivered state must stay truthful."""
        _orch_connection(db_session, last_polled_at=NOW - timedelta(minutes=1))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "ok"
        assert publisher.reports == []

    def test_still_stale_with_outstanding_alert_does_not_resend(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """The edge fires once per transition, never once per tick (#837's edge-not-
        state rule) — a dead loop checked every 10 minutes must not page every 10
        minutes."""
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"
        assert svc.run_poll_staleness_check(db_session, now=NOW + timedelta(hours=1)) == "ok"
        assert len(publisher.reports) == 1


# ── #1186: trigger-binding env-mismatch near-miss marker ─────────────────────
#
# Unit-level coverage of `record_trigger_binding_env_near_miss` itself (the
# integration path — reached from `orchestration_service._trigger_suites` on a
# genuine env mismatch — is covered in test_orchestration_service.py). This
# module owns the `workspace_health` write; these tests pin its upsert/dedupe
# contract directly against the real table.


class TestTriggerBindingEnvNearMiss:
    def test_first_call_creates_one_row(self, db_session: Any) -> None:
        first_occurrence = svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a_snowflake_load",
            run_env="qa",
            binding_env="dev",
        )
        assert first_occurrence is True
        rows = list(db_session.scalars(select(WorkspaceHealth)))
        assert len(rows) == 1
        assert rows[0].key.startswith("trigger_env_near_miss:")

    def test_repeated_calls_for_the_same_tuple_upsert_one_row(self, db_session: Any) -> None:
        occurrences = [
            svc.record_trigger_binding_env_near_miss(
                db_session,
                provider="airflow",
                pipeline_or_dag_id="flow_a_snowflake_load",
                run_env="qa",
                binding_env="dev",
            )
            for _ in range(3)
        ]
        rows = list(db_session.scalars(select(WorkspaceHealth)))
        assert len(rows) == 1  # deduped, not one row per call
        # Only the FIRST call is a genuine insert — the caller uses this to
        # throttle its own log line (the #852 log-amplification lesson): the
        # row still bumps `updated_at` on every call, but repeats don't warn.
        assert occurrences == [True, False, False]

    def test_a_different_tuple_gets_its_own_row(self, db_session: Any) -> None:
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a_snowflake_load",
            run_env="qa",
            binding_env="dev",
        )
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_b_medallion",  # different dag → different tuple
            run_env="qa",
            binding_env="dev",
        )
        rows = list(db_session.scalars(select(WorkspaceHealth)))
        assert len(rows) == 2

    def test_key_is_deterministic_and_within_the_column_length(self, db_session: Any) -> None:
        # A long Airflow DAG id (up to 256 chars) must still fit the 64-char
        # `workspace_health.key` column — this is why the key is hashed, not the
        # raw tuple concatenated.
        long_dag_id = "x" * 256
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id=long_dag_id,
            run_env="qa",
            binding_env="dev",
        )
        key_first = db_session.scalar(select(WorkspaceHealth.key))
        assert key_first is not None
        assert len(key_first) <= 64

        # Recomputed independently for the same tuple → same key (idempotent).
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id=long_dag_id,
            run_env="qa",
            binding_env="dev",
        )
        key_second = db_session.scalar(select(WorkspaceHealth.key))
        assert key_second == key_first


# ── #1199: read side — decoding current near-misses back to their tuple ─────


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
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.commit()
    return suite


def _binding(
    db_session: Any, suite: Suite, *, provider: str, pipeline: str, env: str, enabled: bool = True
) -> TriggerBinding:
    binding = TriggerBinding(
        provider=provider,
        pipeline_or_dag_id=pipeline,
        env=env,
        suite_id=suite.id,
        enabled=enabled,
    )
    db_session.add(binding)
    db_session.commit()
    return binding


class TestListCurrentEnvNearMisses:
    def test_no_enabled_bindings_returns_empty_without_querying_workspace_health(
        self, db_session: Any
    ) -> None:
        # A near-miss row could theoretically still exist from a since-deleted
        # binding — with zero candidates to hash there is nothing to match it
        # against, so it correctly can't be decoded back.
        owner = _user(db_session)
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a",
            run_env="qa",
            binding_env="dev",
        )
        assert svc.list_current_env_near_misses(db_session, user_id=owner.id) == []

    def test_decodes_a_recorded_row_for_an_enabled_binding(self, db_session: Any) -> None:
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a",
            run_env="qa",
            binding_env="dev",
        )

        records = svc.list_current_env_near_misses(db_session, user_id=owner.id)

        assert len(records) == 1
        assert records[0].provider == "airflow"
        assert records[0].pipeline_or_dag_id == "flow_a"
        assert records[0].run_env == "qa"
        assert records[0].binding_env == "dev"

    def test_a_disabled_binding_yields_no_candidates(self, db_session: Any) -> None:
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev", enabled=False)
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a",
            run_env="qa",
            binding_env="dev",
        )
        assert svc.list_current_env_near_misses(db_session, user_id=owner.id) == []

    def test_a_binding_with_no_recorded_mismatch_yields_nothing(self, db_session: Any) -> None:
        # An enabled binding generates candidate hashes, but none of them exist as
        # a real workspace_health row unless the ingest path actually observed
        # that exact mismatch.
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        assert svc.list_current_env_near_misses(db_session, user_id=owner.id) == []

    def test_a_stale_row_past_the_recency_window_is_excluded(self, db_session: Any) -> None:
        """Mirrors the #1199 acceptance criterion: a near-miss that stopped
        recurring (fixed, or the pipeline stopped running) must not read as
        still-current forever just because the row was never deleted."""
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a",
            run_env="qa",
            binding_env="dev",
        )
        key = svc._near_miss_key(
            provider="airflow", pipeline_or_dag_id="flow_a", run_env="qa", binding_env="dev"
        )
        row = db_session.get(WorkspaceHealth, key)
        assert row is not None
        row.updated_at = datetime.now(UTC) - timedelta(days=30)
        db_session.commit()

        assert svc.list_current_env_near_misses(db_session, user_id=owner.id, since_hours=48) == []
        # A wider window still finds it — proves the exclusion is the recency
        # filter, not a bug in the candidate derivation.
        assert (
            len(svc.list_current_env_near_misses(db_session, user_id=owner.id, since_hours=24 * 31))
            == 1
        )

    def test_multiple_current_rows_sort_newest_first(self, db_session: Any) -> None:
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        _binding(db_session, suite, provider="airflow", pipeline="flow_b", env="dev")
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a",
            run_env="qa",
            binding_env="dev",
        )
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_b",
            run_env="qa",
            binding_env="dev",
        )
        # Push flow_a's row further into the past so flow_b is unambiguously newer.
        key_a = svc._near_miss_key(
            provider="airflow", pipeline_or_dag_id="flow_a", run_env="qa", binding_env="dev"
        )
        row_a = db_session.get(WorkspaceHealth, key_a)
        assert row_a is not None
        row_a.updated_at = datetime.now(UTC) - timedelta(hours=1)
        db_session.commit()

        records = svc.list_current_env_near_misses(db_session, user_id=owner.id)

        assert [r.pipeline_or_dag_id for r in records] == ["flow_b", "flow_a"]

    def test_two_simultaneous_mismatches_on_one_binding_are_both_returned(
        self, db_session: Any
    ) -> None:
        """The #1186 root case this whole feature exists to catch: the same DAG id
        observed by TWO orchestrator connections in two different wrong envs. Both
        are live mismatches on the one binding — returning only the newer would
        hide a real one behind another."""
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        for run_env in ("qa", "uat"):
            svc.record_trigger_binding_env_near_miss(
                db_session,
                provider="airflow",
                pipeline_or_dag_id="flow_a",
                run_env=run_env,
                binding_env="dev",
            )

        records = svc.list_current_env_near_misses(db_session, user_id=owner.id)

        assert sorted(r.run_env for r in records) == ["qa", "uat"]

    def test_ties_on_updated_at_are_ordered_deterministically(self, db_session: Any) -> None:
        """Two mismatches recorded in the same ingest batch share a `func.now()`
        transaction timestamp to the microsecond. Without a total sort the order
        would be whatever Postgres happened to return, so the list — and the
        per-binding badges built from it — could reshuffle between refreshes."""
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        _binding(db_session, suite, provider="airflow", pipeline="flow_b", env="dev")
        for pipeline in ("flow_a", "flow_b"):
            svc.record_trigger_binding_env_near_miss(
                db_session,
                provider="airflow",
                pipeline_or_dag_id=pipeline,
                run_env="qa",
                binding_env="dev",
            )
        # Force an exact tie — the same instant on both rows.
        tied_at = datetime.now(UTC) - timedelta(minutes=5)
        for pipeline in ("flow_a", "flow_b"):
            key = svc._near_miss_key(
                provider="airflow", pipeline_or_dag_id=pipeline, run_env="qa", binding_env="dev"
            )
            row = db_session.get(WorkspaceHealth, key)
            assert row is not None
            row.updated_at = tied_at
        db_session.commit()

        first = svc.list_current_env_near_misses(db_session, user_id=owner.id)
        second = svc.list_current_env_near_misses(db_session, user_id=owner.id)

        assert [r.pipeline_or_dag_id for r in first] == ["flow_a", "flow_b"]
        assert first == second

    def test_a_binding_on_an_inaccessible_suite_is_not_enumerable(self, db_session: Any) -> None:
        """Trigger bindings are suite-owned config — `GET /trigger-bindings` scopes
        them to owned-or-shared suites, so this read must too. A stranger must not
        be able to enumerate the (provider, pipeline, env) of someone else's
        binding by reading near-misses."""
        owner = _user(db_session)
        stranger = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a",
            run_env="qa",
            binding_env="dev",
        )

        assert len(svc.list_current_env_near_misses(db_session, user_id=owner.id)) == 1
        assert svc.list_current_env_near_misses(db_session, user_id=stranger.id) == []
        # …but a workspace admin (ADR 0027) sees the whole workspace.
        assert (
            len(svc.list_current_env_near_misses(db_session, user_id=stranger.id, include_all=True))
            == 1
        )

    def test_a_shared_suite_is_visible_to_the_sharee(self, db_session: Any) -> None:
        owner = _user(db_session)
        sharee = _user(db_session)
        suite = _suite(db_session, owner)
        db_session.add(Share(suite_id=suite.id, user_id=sharee.id, permission="view"))
        db_session.commit()
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        svc.record_trigger_binding_env_near_miss(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="flow_a",
            run_env="qa",
            binding_env="dev",
        )

        assert len(svc.list_current_env_near_misses(db_session, user_id=sharee.id)) == 1

    def test_suite_id_narrows_to_one_suites_bindings(self, db_session: Any) -> None:
        owner = _user(db_session)
        suite_a = _suite(db_session, owner)
        suite_b = _suite(db_session, owner)
        _binding(db_session, suite_a, provider="airflow", pipeline="flow_a", env="dev")
        _binding(db_session, suite_b, provider="airflow", pipeline="flow_b", env="dev")
        for pipeline in ("flow_a", "flow_b"):
            svc.record_trigger_binding_env_near_miss(
                db_session,
                provider="airflow",
                pipeline_or_dag_id=pipeline,
                run_env="qa",
                binding_env="dev",
            )

        both = svc.list_current_env_near_misses(db_session, user_id=owner.id)
        just_a = svc.list_current_env_near_misses(db_session, user_id=owner.id, suite_id=suite_a.id)

        assert {r.pipeline_or_dag_id for r in both} == {"flow_a", "flow_b"}
        assert [r.pipeline_or_dag_id for r in just_a] == ["flow_a"]


class TestNearMissWriteReadRoundTrip:
    """Read/write coupling guard (#1199 review).

    Every test above drives the write LEAF (`record_trigger_binding_env_near_miss`)
    directly, so a change to `orchestration_service._record_env_near_misses` —
    which is where the tuple that actually gets hashed is *chosen* — could silently
    desync the two derivations and still ship green. This exercises the real
    production write path end-to-end instead.
    """

    def test_the_production_write_path_produces_rows_the_read_side_decodes(
        self, db_session: Any
    ) -> None:
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        # Binding is scoped to dev; the run lands via a QA connection.
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
        qa_connection = _orch_connection(db_session, conn_type="airflow", env="qa")
        db_session.commit()

        orchestration_service._record_env_near_misses(
            db_session,
            provider="airflow",
            connection=qa_connection,
            update=RunUpdate(
                provider_run_id="r1",
                pipeline_or_dag_id="flow_a",
                resource_name="airflow-host",
                status="succeeded",
            ),
        )

        records = svc.list_current_env_near_misses(db_session, user_id=owner.id)

        assert len(records) == 1
        assert records[0].provider == "airflow"
        assert records[0].pipeline_or_dag_id == "flow_a"
        assert records[0].run_env == "qa"
        assert records[0].binding_env == "dev"

    def test_a_disabled_binding_is_skipped_by_the_production_write_path(
        self, db_session: Any
    ) -> None:
        """The write side only considers ENABLED bindings; if that ever diverged
        from the read side's identical filter the two would silently disagree."""
        owner = _user(db_session)
        suite = _suite(db_session, owner)
        _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev", enabled=False)
        qa_connection = _orch_connection(db_session, conn_type="airflow", env="qa")
        db_session.commit()

        orchestration_service._record_env_near_misses(
            db_session,
            provider="airflow",
            connection=qa_connection,
            update=RunUpdate(
                provider_run_id="r1",
                pipeline_or_dag_id="flow_a",
                resource_name="airflow-host",
                status="succeeded",
            ),
        )

        assert db_session.scalars(select(WorkspaceHealth.key)).all() == []
        assert svc.list_current_env_near_misses(db_session, user_id=owner.id) == []
