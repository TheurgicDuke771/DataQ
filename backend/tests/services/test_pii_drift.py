"""pii_drift monitor kind (ADR 0049 phase 2) — profile at run time, baseline-and-skip, a
baseline that never moves on its own, and every rung that takes a column out of
"unclassified"."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.datasources.monitors import STATEFUL_MONITOR_KINDS
from backend.app.db.models import (
    Asset,
    AssetColumnClassification,
    Check,
    Connection,
    Run,
    Suite,
    User,
)
from backend.app.services import column_detections, pii_drift, profile_service, stateful_monitors
from backend.app.services.monitor_baseline import get_baseline
from backend.app.services.pii_drift import build_pii_drift_executor
from backend.app.services.profile_service import ColumnProfile, ProfileResult
from backend.tests.support.fake_secret_store import FakeSecretStore

_EMAILS = [{"value": f"u{i}@example.com", "count": 1} for i in range(10)]
_PLAIN = [{"value": v, "count": 1} for v in ("new", "paid", "shipped", "void", "held")]


def _profile(**columns: list[dict[str, Any]]) -> list[ColumnProfile]:
    return [
        ColumnProfile(name, 0, 0.0, len(values), None, None, values)
        for name, values in columns.items()
    ]


@pytest.fixture
def graph(db_session: Session) -> dict[str, Any]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:8]}",
        type="postgres",
        env="dev",
        config={"host": "db", "database": "app", "schema": "public"},
        secret_ref="kv",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    asset = Asset(namespace="postgres://db", name="public.orders")
    db_session.add(asset)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=user.id,
        target={"table": "orders", "schema": "public"},
        asset_id=asset.id,
    )
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="pii drift",
        kind="pii_drift",
        expectation_type="monitor:pii_drift",
        config={},
    )
    db_session.add(check)
    db_session.commit()
    return {"session": db_session, "conn": conn, "suite": suite, "asset": asset, "check": check}


class _Fake:
    """A swappable profile: tests change `columns` between runs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, columns: list[ColumnProfile]) -> None:
        self.columns = columns
        self.seen: list[dict[str, Any]] = []
        monkeypatch.setattr(
            profile_service, "list_columns", lambda *a, **k: [c.column for c in self.columns]
        )

        def _inner(connection: Any, **kwargs: Any) -> ProfileResult:
            self.seen.append(kwargs)
            return ProfileResult(row_count=10, table=kwargs.get("table"), columns=self.columns)

        monkeypatch.setattr(profile_service, "_profile_connection", _inner)


def _executor(g: dict[str, Any], *, persist: bool = True, run: Run | None = None) -> Any:
    return build_pii_drift_executor(
        g["session"],
        connection=g["conn"],
        target_table="orders",
        target_schema="public",
        target_catalog=None,
        secret_store=FakeSecretStore(default="s", raise_on_write=True),
        persist=persist,
        run=run,
    )


def _rows(session: Session, asset: Asset) -> list[AssetColumnClassification]:
    return list(
        session.scalars(
            select(AssetColumnClassification).where(AssetColumnClassification.asset_id == asset.id)
        )
    )


# ── the lifecycle ────────────────────────────────────────────────────────────────────────────


def test_first_run_baselines_what_already_looks_like_pii_and_skips(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _Fake(monkeypatch, _profile(contact=_EMAILS, status=_PLAIN))
    out = _executor(graph)(graph["check"])

    assert out.skipped is True and out.errored is False
    assert out.observed_value == {
        "columns_profiled": 2,
        "columns_detected": 1,
        "columns_unclassified": 1,
        "baseline_captured": True,
        "detected_columns": ["contact"],
    }
    row = get_baseline(graph["session"], graph["check"].id)
    assert row is not None and row.baseline == {"columns": ["contact"]}
    # The run fed the classification source as a FULL profile: a row, and the stamp moved.
    assert [r.column_name for r in _rows(graph["session"], graph["asset"])] == ["contact"]
    graph["session"].refresh(graph["asset"])
    assert graph["asset"].detected_classifications_refreshed_at is not None


def test_a_column_that_starts_looking_like_pii_fails_until_someone_classifies_it(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The baseline is fixed (schema_drift's shape): rolling it forward would pass the very next
    run and auto-resolve the incident with the column still unclassified."""
    fake = _Fake(monkeypatch, _profile(contact=_EMAILS, status=_PLAIN))

    def run(check: Check) -> Any:  # one executor per simulated run, as the worker builds it
        return _executor(graph)(check)

    run(graph["check"])  # baseline: {contact}

    steady = run(graph["check"])
    assert steady.skipped is False and steady.success is True and steady.metric_value == 0.0

    fake.columns = _profile(contact=_EMAILS, status=_PLAIN, notes=_EMAILS)
    drifted = run(graph["check"])
    assert drifted.success is False and drifted.metric_value == 1.0
    assert drifted.observed_value is not None
    assert drifted.observed_value["new_columns"] == ["notes"]
    assert drifted.observed_value["known_columns"] == ["contact"]
    assert drifted.observed_value["columns_detected"] == 2
    assert drifted.observed_value["columns_unclassified"] == 2
    assert "baseline_captured_at" in drifted.observed_value

    # Nothing changed, nobody acted: still failing, baseline untouched.
    again = run(graph["check"])
    assert again.success is False and again.metric_value == 1.0
    assert again.observed_value is not None
    assert again.observed_value["new_columns"] == ["notes"]
    row = get_baseline(graph["session"], graph["check"].id)
    assert row is not None and row.baseline == {"columns": ["contact"]}

    # Classifying it in the suite policy clears the condition: the next run passes.
    graph["suite"].column_policy = {"pii_columns": ["notes"]}
    graph["session"].commit()
    cleared = run(graph["check"])
    assert cleared.success is True and cleared.metric_value == 0.0


def test_dismissing_then_restoring_a_baselined_column_is_not_drift(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed baseline means UI clicks cannot manufacture a drift event: a column the first run
    grandfathered stays grandfathered through a dismissal and its undo."""
    _Fake(monkeypatch, _profile(contact=_EMAILS))

    def run(check: Check) -> Any:  # one executor per simulated run, as the worker builds it
        return _executor(graph)(check)

    run(graph["check"])  # baseline {contact}
    column_detections.set_dismissed(
        graph["session"], graph["asset"], "contact", dismissed=True, actor_id=None
    )
    assert run(graph["check"]).metric_value == 0.0
    column_detections.set_dismissed(
        graph["session"], graph["asset"], "contact", dismissed=False, actor_id=None
    )
    restored = run(graph["check"])
    assert restored.success is True and restored.metric_value == 0.0
    assert restored.observed_value is not None
    assert restored.observed_value["new_columns"] == []


# ── every rung that takes a column out of "unclassified" ────────────────────────────────────


def test_a_column_in_pii_columns_is_classified_but_the_identifier_column_is_not(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`identifier_column` is a display decision the redaction ladder honours OVER a detection —
    the identifier is shown unmasked — so a detection there is the one the monitor must report."""
    graph["suite"].column_policy = {"pii_columns": ["Notes"], "identifier_column": "contact"}
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS, notes=_EMAILS, other=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == ["contact", "other"]


def test_a_column_with_its_own_warehouse_tag_is_not_drift(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Either verdict: the steward looked at it, so it is classified, not drifting."""
    graph["asset"].column_tags = {"contact": "public", "notes": "sensitive"}
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS, notes=_EMAILS, other=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == ["other"]


def test_a_dismissed_detection_is_not_drift(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Fake(monkeypatch, _profile(contact=_EMAILS))

    def run(check: Check) -> Any:  # one executor per simulated run, as the worker builds it
        return _executor(graph)(check)

    run(graph["check"])  # baseline {contact}, detection persisted
    column_detections.set_dismissed(
        graph["session"], graph["asset"], "contact", dismissed=True, actor_id=None
    )
    fake.columns = _profile(contact=_EMAILS, notes=_EMAILS)
    out = run(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["new_columns"] == ["notes"]
    # The detectors still flag both; only the unclassified remainder dropped the dismissed one.
    assert out.observed_value["columns_detected"] == 2
    assert out.observed_value["columns_unclassified"] == 1, "the dismissed column left the set"


def test_ignore_columns_are_left_out(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph["check"].config = {"ignore_columns": ["CONTACT"]}
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS, notes=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == ["notes"]


# ── dry run, failures, dispatch ──────────────────────────────────────────────────────────────


def test_a_column_since_dropped_from_the_table_is_not_drift(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classification source deliberately keeps a detection the next profile does not repeat
    (ADR 0049 §4); the monitor must still judge only columns the table holds NOW, or it fails
    forever on a name nobody can tag because it is no longer there."""
    fake = _Fake(monkeypatch, _profile(status=_PLAIN))

    def run(check: Check) -> Any:  # one executor per simulated run, as the worker builds it
        return _executor(graph)(check)

    run(graph["check"])  # baseline {}
    fake.columns = _profile(status=_PLAIN, contact2=_EMAILS)
    assert run(graph["check"]).metric_value == 1.0  # reported while it is there
    fake.columns = _profile(status=_PLAIN)  # dropped / renamed
    gone = run(graph["check"])
    assert gone.success is True and gone.metric_value == 0.0
    assert gone.observed_value is not None and gone.observed_value["new_columns"] == []
    # …while the classification row itself is kept, as the source documents.
    assert [r.column_name for r in _rows(graph["session"], graph["asset"])] == ["contact2"]


def test_the_target_is_profiled_once_per_run_however_many_checks(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every profile is a full top-values scan (billed per query on Athena): two pii_drift
    checks in one suite must share one."""
    fake = _Fake(monkeypatch, _profile(contact=_EMAILS))
    second = Check(
        suite_id=graph["suite"].id,
        name="pii drift, ignoring contact",
        kind="pii_drift",
        expectation_type="monitor:pii_drift",
        config={"ignore_columns": ["contact"]},
    )
    graph["session"].add(second)
    graph["session"].commit()
    run = _executor(graph)  # ONE executor: the worker builds one per run
    first, other = run(graph["check"]), run(second)
    assert len(fake.seen) == 1
    assert first.observed_value is not None and other.observed_value is not None
    assert first.observed_value["detected_columns"] == ["contact"]
    assert other.observed_value["detected_columns"] == []  # its own config still applies


def test_the_runs_own_asset_wins_over_the_suites(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve_asset` precedence: a suite retargeted between dispatch and pickup must not
    split the detections (and the baseline diff) from the incident keyed on the run's asset."""
    other = Asset(namespace="postgres://db", name="public.orders_v2")
    graph["session"].add(other)
    graph["session"].flush()
    run_row = Run(suite_id=graph["suite"].id, status="running", asset_id=other.id)
    graph["session"].add(run_row)
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS))
    _executor(graph, run=run_row)(graph["check"])
    assert [r.column_name for r in _rows(graph["session"], other)] == ["contact"]
    assert _rows(graph["session"], graph["asset"]) == []


def test_the_profilers_own_refusals_are_not_flattened_into_an_outage(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 422-class DataQError (no schema, no credential, a bad identifier) carries a message
    written for the author; `profile_target` must let it through intact."""

    class _RefusedError(DataQError):
        pass

    def _refuse(*a: Any, **k: Any) -> Any:
        raise _RefusedError("no schema given and the connection has none")

    monkeypatch.setattr(profile_service, "list_columns", _refuse)
    with pytest.raises(_RefusedError):
        pii_drift.profile_target(
            graph["conn"],
            session=graph["session"],
            table="orders",
            schema=None,
            catalog=None,
            secret_store=FakeSecretStore(default="s", raise_on_write=True),
            asset=None,
        )


def test_the_persisted_run_feeds_the_classification_source_as_a_full_profile_without_committing(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's asset gets the detections as a FULL profile (the stamp moves), written
    flush-only: the stateful phase rides the run's terminal commit (#318)."""
    captured: dict[str, Any] = {}
    real = column_detections.record_profile_detections_safely

    def spy(session: Any, asset: Any, columns: Any, **kwargs: Any) -> None:
        captured.update(kwargs, asset_id=asset.id)
        real(session, asset, columns, **kwargs)

    _Fake(monkeypatch, _profile(contact=_EMAILS))
    monkeypatch.setattr(column_detections, "record_profile_detections_safely", spy)
    _executor(graph)(graph["check"])
    assert captured == {"full_profile": True, "commit": False, "asset_id": graph["asset"].id}


def test_the_executor_never_commits_the_worker_session(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """execute_run's #318 invariant: a stateful phase is unpublishable and rides the terminal
    commit. The profile path's credential bookkeeping and the detection write both commit on
    their own elsewhere; inside a run neither may, or a cancelled run keeps committed rows."""
    _Fake(monkeypatch, _profile(contact=_EMAILS))
    commits: list[int] = []
    real_commit = graph["session"].commit

    def counting_commit() -> None:
        commits.append(1)
        real_commit()

    monkeypatch.setattr(graph["session"], "commit", counting_commit)
    out = _executor(graph)(graph["check"])
    assert out.errored is False
    assert commits == []
    # …and the detections are still visible to this transaction, pending the terminal commit.
    assert [r.column_name for r in _rows(graph["session"], graph["asset"])] == ["contact"]


def test_a_failed_profile_is_attempted_once_per_run_however_many_checks(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    def _down(*a: Any, **k: Any) -> list[str]:
        attempts.append(1)
        raise profile_service.ProfileFailedError("column profile could not execute")

    monkeypatch.setattr(profile_service, "list_columns", _down)
    second = Check(
        suite_id=graph["suite"].id,
        name="pii drift 2",
        kind="pii_drift",
        expectation_type="monitor:pii_drift",
        config={},
    )
    graph["session"].add(second)
    graph["session"].commit()
    run = _executor(graph)
    outcomes = [run(graph["check"]), run(second)]
    assert all(
        o.errored and o.error_message == "column profile could not execute" for o in outcomes
    )
    assert len(attempts) == 1


def test_a_flat_file_target_keeps_its_explicit_file_format(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extension-less key profiles only with the suite's `file_format`; dropping it would make
    every run error with 'pass file_format' about a field the author already set."""
    graph["conn"].type = "s3"
    graph["suite"].target = {"path": "landing/orders/latest", "file_format": "parquet"}
    graph["session"].commit()
    fake = _Fake(monkeypatch, _profile(contact=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.errored is False
    assert fake.seen[0]["path"] == "orders" and fake.seen[0]["file_format"] == "parquet"


def test_an_unclassified_driver_exception_is_a_per_check_error_with_a_classified_message(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*a: Any, **k: Any) -> list[str]:
        raise RuntimeError("password authentication failed for user 'x' at 10.0.0.1")

    monkeypatch.setattr(profile_service, "list_columns", _boom)
    out = _executor(graph)(graph["check"])
    assert out.errored is True and out.error_message is not None
    assert out.error_message.startswith("could not profile the target: ")
    assert "10.0.0.1" not in out.error_message
    assert get_baseline(graph["session"], graph["check"].id) is None


@pytest.mark.parametrize(
    "exc",
    [
        profile_service.ProfileFailedError("column profile could not execute"),
        profile_service.ProfileIdentifierInvalidError(
            "no schema given and the connection has none"
        ),
    ],
    ids=["outage", "refusal"],
)
def test_the_profilers_own_errors_are_per_check_errors_not_run_failures(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """What the real profiler raises is a DataQError (an outage is `ProfileFailedError`), which
    `profile_target` passes through for the dry-run's sake; the executor must still turn it into
    THIS check's error, or it escapes to `execute_run` and discards the sibling checks' results."""

    def _raise(*a: Any, **k: Any) -> list[str]:
        raise exc

    monkeypatch.setattr(profile_service, "list_columns", _raise)
    out = _executor(graph)(graph["check"])
    assert out.errored is True and out.error_message == str(exc)
    assert get_baseline(graph["session"], graph["check"].id) is None


def test_the_verdict_does_not_rest_on_the_classification_store_alone(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's write and read are fail-soft by design; a swallowed error there must not turn
    this run's detection into an empty baseline (and the next run into a false FAIL)."""
    _Fake(monkeypatch, _profile(contact=_EMAILS))
    monkeypatch.setattr(column_detections, "record_profile_detections_safely", lambda *a, **k: None)
    out = _executor(graph)(graph["check"])
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == ["contact"]
    row = get_baseline(graph["session"], graph["check"].id)
    assert row is not None and row.baseline == {"columns": ["contact"]}


def test_a_suite_with_no_asset_is_an_error_not_a_pass(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph["suite"].asset_id = None
    graph["session"].commit()
    _Fake(monkeypatch, _profile(contact=_EMAILS))
    out = _executor(graph)(graph["check"])
    assert out.errored is True and "asset" in (out.error_message or "")


def test_file_and_iceberg_targets_are_addressed_the_way_the_profiler_expects() -> None:
    s3 = Connection(name="s3", type="s3", env="dev", config={}, secret_ref="kv")
    ice = Connection(name="ice", type="iceberg", env="dev", config={}, secret_ref="kv")
    pg = Connection(name="pg", type="postgres", env="dev", config={}, secret_ref="kv")
    assert pii_drift._target_kwargs(s3, table="landing/orders.csv", schema=None, catalog=None) == {
        "path": "landing/orders.csv",
        "file_format": None,
    }
    assert pii_drift._target_kwargs(
        s3, table="landing/orders/latest", schema=None, catalog=None, file_format="parquet"
    ) == {"path": "landing/orders/latest", "file_format": "parquet"}
    assert pii_drift._target_kwargs(ice, table="sales.orders", schema=None, catalog=None) == {
        "table": "sales.orders"
    }
    assert pii_drift._target_kwargs(pg, table="orders", schema="public", catalog=None) == {
        "table": "orders",
        "schema": "public",
        "catalog": None,
    }


def test_every_stateful_kind_has_an_executor_and_nothing_else_does() -> None:
    """The registry says which kinds are stateful; the dispatcher must wire exactly those."""
    assert set(stateful_monitors._BUILDERS) == set(STATEFUL_MONITOR_KINDS)


# ── author time and dry run ──────────────────────────────────────────────────────────────────


def test_authoring_is_allowed_on_every_profilable_type_and_refused_elsewhere() -> None:
    from decimal import Decimal

    from backend.app.services.check_service import CheckConfigInvalidError, validate_monitor_check

    for conn_type in ("postgres", "snowflake", "unity_catalog", "s3", "adls_gen2", "iceberg"):
        validate_monitor_check(
            "pii_drift",
            {},
            expectation_type="monitor:pii_drift",
            connection_type=conn_type,
            warn_threshold=Decimal(1),
            fail_threshold=Decimal(1),
            critical_threshold=None,
        )
    with pytest.raises(CheckConfigInvalidError):
        validate_monitor_check(
            "pii_drift",
            {},
            expectation_type="monitor:pii_drift",
            connection_type="airflow",
            warn_threshold=None,
            fail_threshold=None,
            critical_threshold=None,
        )
    with pytest.raises(CheckConfigInvalidError):
        validate_monitor_check(
            "pii_drift",
            {"ignore_columns": "contact"},
            expectation_type="monitor:pii_drift",
            connection_type="postgres",
            warn_threshold=None,
            fail_threshold=None,
            critical_threshold=None,
        )


def test_dry_run_reports_the_raw_detector_view_and_persists_nothing(
    graph: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.services import dryrun_service

    fake = _Fake(monkeypatch, _profile(contact=_EMAILS, status=_PLAIN, notes=_EMAILS))
    out = dryrun_service.dry_run_check(
        graph["conn"],
        session=graph["session"],
        kind="pii_drift",
        expectation_type="monitor:pii_drift",
        config={"ignore_columns": ["notes"]},
        warn_threshold=None,
        fail_threshold=None,
        critical_threshold=None,
        target={"table": "orders", "schema": "public"},
        secret_store=FakeSecretStore(default="s", raise_on_write=True),
    )
    assert out.status == "skip" and out.metric_value is None
    assert out.observed_value is not None
    assert out.observed_value["detected_columns"] == [{"column": "contact", "detector": "email"}]
    assert out.observed_value["columns_profiled"] == 3
    assert "persisted run" in out.observed_value["preview"]
    assert _rows(graph["session"], graph["asset"]) == []
    assert all(k.get("asset") is None for k in fake.seen)
