"""A run's own pii_drift detection masks that same run's failing samples on every reader —
including one that reads while the run is still going — against real, COMMITTED rows."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome
from backend.app.db.models import (
    Asset,
    AssetColumnClassification,
    Check,
    Connection,
    MonitorBaseline,
    Result,
    Run,
    Suite,
    User,
)
from backend.app.services import profile_service, run_service
from backend.app.services.pii_drift import build_pii_drift_executor
from backend.app.services.profile_service import ColumnProfile, ProfileResult
from backend.tests.support.fake_secret_store import FakeSecretStore

# The failing values a check stores: nothing in them looks like personal data, so only the
# detection the run's own profile records can mask them.
_FIELD_FAILING = ["pending-review", "on-hold", "queued"]
_STATUS_FAILING = ["void", "held"]


class _Runner:
    """Both expectation checks fail with a scalar sample of their tested column."""

    supported_monitor_kinds: frozenset[str] = frozenset()

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        samples = {"field_7": _FIELD_FAILING, "status": _STATUS_FAILING}
        outcomes = [
            CheckOutcome(
                expectation_type=c.expectation_type,
                success=False,
                sample_failures={
                    "unexpected_count": 3,
                    "partial_unexpected_list": list(samples[c.kwargs["column"]]),
                },
            )
            for c in checks
        ]
        return SuiteOutcome(success=False, checks=outcomes)


class _Graph:
    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.session = Session(bind=engine)
        s = self.session
        self.user = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:10]}@ex.io")
        s.add(self.user)
        s.flush()
        self.conn = Connection(
            name=f"pg-{uuid.uuid4().hex[:8]}",
            type="postgres",
            env="dev",
            config={"host": "db", "database": "app", "schema": "public"},
            secret_ref="kv",
            created_by=self.user.id,
        )
        self.asset = Asset(namespace="postgres://db", name=f"public.t_{uuid.uuid4().hex[:8]}")
        s.add_all([self.conn, self.asset])
        s.flush()
        self.suite = Suite(
            name=f"s-{uuid.uuid4().hex[:8]}",
            connection_id=self.conn.id,
            created_by=self.user.id,
            target={"table": "orders", "schema": "public"},
            asset_id=self.asset.id,
        )
        s.add(self.suite)
        s.flush()
        self.checks = [
            Check(
                suite_id=self.suite.id,
                name=f"{column} in set",
                expectation_type="expect_column_values_to_be_in_set",
                config={"column": column, "value_set": ["ok"]},
            )
            for column in ("field_7", "status")
        ]
        self.checks.append(
            Check(
                suite_id=self.suite.id,
                name="pii drift",
                kind="pii_drift",
                expectation_type="monitor:pii_drift",
                config={},
            )
        )
        s.add_all(self.checks)
        s.flush()
        self.run = Run(suite_id=self.suite.id, status="queued", triggered_by="test")
        s.add(self.run)
        s.commit()

    def read_samples(self) -> dict[str, run_service.RedactedSample]:
        """Each check's sample as a reader in ANOTHER process sees it: its own connection, only
        committed rows, and the same ladder call the results API, MCP and alert cards make."""
        with Session(bind=self.engine) as other:
            suite = other.get(Suite, self.suite.id)
            assert suite is not None
            run = other.get(Run, self.run.id)
            tags = run_service.asset_column_tags(other, suite, run)
            out: dict[str, run_service.RedactedSample] = {}
            for result in other.scalars(select(Result).where(Result.run_id == self.run.id)):
                check = other.get(Check, result.check_id)
                assert check is not None
                if check.kind != "expectation":
                    continue
                column = str(check.config["column"])
                out[column] = run_service.redact_sample_failures_with_state(
                    result.sample_failures,
                    tested_column=column,
                    policy=suite.column_policy,
                    tags=tags,
                )
            return out

    def close(self) -> None:
        self.session.rollback()
        check_ids = [ch.id for ch in self.checks]
        run_id, suite_id, asset_id = self.run.id, self.suite.id, self.asset.id
        conn_id, user_id = self.conn.id, self.user.id
        self.session.close()
        with Session(bind=self.engine) as c:
            c.execute(delete(Result).where(Result.run_id == run_id))
            c.execute(delete(MonitorBaseline).where(MonitorBaseline.check_id.in_(check_ids)))
            c.execute(delete(Run).where(Run.id == run_id))
            c.execute(delete(Check).where(Check.suite_id == suite_id))
            c.execute(delete(Suite).where(Suite.id == suite_id))
            c.execute(
                delete(AssetColumnClassification).where(
                    AssetColumnClassification.asset_id == asset_id
                )
            )
            c.execute(delete(Asset).where(Asset.id == asset_id))
            c.execute(delete(Connection).where(Connection.id == conn_id))
            c.execute(delete(User).where(User.id == user_id))
            c.commit()


def _column(name: str, values: list[str]) -> ColumnProfile:
    return ColumnProfile(
        name, 0, 0.0, len(values), None, None, [{"value": v, "count": 1} for v in values]
    )


@pytest.fixture
def graph(_db_engine: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Graph]:
    # The run-time profile sees e-mail addresses in `field_7` — the column starts carrying
    # personal data with this run — and nothing of the kind in `status`.
    columns = [
        _column("field_7", [f"u{i}@example.com" for i in range(10)]),
        _column("status", ["ok", "void", "held"]),
    ]
    monkeypatch.setattr(
        profile_service, "list_columns", lambda *a, **k: [c.column for c in columns]
    )
    monkeypatch.setattr(
        profile_service,
        "_profile_connection",
        lambda connection, **kw: ProfileResult(
            row_count=10, table=kw.get("table"), columns=columns
        ),
    )
    g = _Graph(_db_engine)
    yield g
    g.close()


def test_a_detection_masks_its_own_runs_samples_for_a_reader_during_and_after_the_run(
    graph: _Graph,
) -> None:
    pii_drift = build_pii_drift_executor(
        graph.session,
        connection=graph.conn,
        target_table="orders",
        target_schema="public",
        target_catalog=None,
        secret_store=FakeSecretStore(default="s", raise_on_write=True),
        run=graph.run,
    )
    during: list[dict[str, run_service.RedactedSample]] = []

    def stateful(check: Check) -> CheckOutcome:
        # The stateful phase runs last: whatever another reader can see now was published
        # BEFORE this run's detection exists.
        during.append(graph.read_samples())
        return pii_drift(check)

    run_service.execute_run(
        graph.session,
        run=graph.run,
        checks=graph.checks,
        runner=_Runner(),
        table="orders",
        schema="public",
        stateful_monitor_executor=stateful,
    )
    assert graph.run.status == "succeeded"

    # Mid-run, no sample is readable at all: the detection is not committed yet, so a sample
    # published now would show `field_7` unmasked.
    assert during == [{}]

    after = graph.read_samples()
    assert set(after) == {"field_7", "status"}
    field = after["field_7"]
    assert field.sample is not None
    assert field.sample["partial_unexpected_list"] == ["<redacted>"] * len(_FIELD_FAILING)
    assert field.redaction_reasons == {"field_7": "detected"}
    # A column the profile found nothing in is untouched.
    status = after["status"]
    assert status.sample is not None
    assert status.sample["partial_unexpected_list"] == _STATUS_FAILING
    assert status.redacted_columns == []
