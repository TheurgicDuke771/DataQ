"""Comparison run-path tests (ADR 0015, #794) — executor + dispatch + redaction.

The #792 reader is monkeypatched (frames in-memory); the #793 engine runs for
real, so the outcome mapping under test carries genuine bucket semantics. No
database: a minimal fake session resolves the source connection.
"""

import uuid
from typing import Any

import pandas as pd
import pytest

from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome
from backend.app.db.models import Check, Connection
from backend.app.services import comparison_run, run_service
from backend.app.services.dataset_reader import DatasetTooLargeError


class FakeSession:
    def __init__(self, connections: dict[uuid.UUID, Connection]) -> None:
        self._connections = connections

    def get(self, model: Any, pk: uuid.UUID) -> Connection | None:
        assert model is Connection
        return self._connections.get(pk)


class FakeSecretStore:
    def get(self, ref: str) -> str:
        return "s3cret"


def _conn(conn_type: str = "snowflake") -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name=f"{conn_type}-{uuid.uuid4().hex[:6]}",
        type=conn_type,
        env="dev",
        config={"schema": "PUBLIC"},
        secret_ref="conn-x",
        created_by=uuid.uuid4(),
    )


def _comparison_check(source_id: uuid.UUID, **config_overrides: Any) -> Check:
    config: dict[str, Any] = {
        "source": {"table": "ORDERS", "schema": "RETAIL"},
        "keys": ["id"],
    }
    config.update(config_overrides)
    return Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="recon",
        kind="comparison",
        expectation_type="comparison:records",
        source_connection_id=source_id,
        config=config,
    )


def _executor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_conn: Connection,
    frames: dict[uuid.UUID, pd.DataFrame],
    suite_conn: Connection | None = None,
) -> comparison_run.ComparisonExecutor:
    """An executor whose reader returns `frames[connection.id]`."""
    suite_conn = suite_conn or _conn()

    def _fake_read(connection: Connection, spec: Any, *, max_rows: int, secret_store: Any) -> Any:
        return frames[connection.id]

    monkeypatch.setattr(comparison_run, "read_dataset", _fake_read)
    return comparison_run.build_comparison_executor(
        FakeSession({source_conn.id: source_conn}),  # type: ignore[arg-type]
        suite_connection=suite_conn,
        target_table="ORDERS_COPY",
        target_schema="RETAIL",
        target_catalog=None,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )


def test_executor_maps_buckets_onto_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    source_conn, suite_conn = _conn(), _conn()
    frames = {
        source_conn.id: pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"]}),
        suite_conn.id: pd.DataFrame({"id": [2, 3, 4], "v": ["b", "X", "d"]}),
    }
    execute = _executor(monkeypatch, source_conn=source_conn, frames=frames, suite_conn=suite_conn)
    outcome = execute(_comparison_check(source_conn.id))

    assert not outcome.errored and not outcome.success
    # union=4, non-matching=3 (one mismatch + one per side) → 75%
    assert outcome.metric_value == 75.0
    assert outcome.observed_value is not None
    assert outcome.observed_value["matched"] == 1
    assert outcome.observed_value["column_mismatch_counts"] == {"v": 1}
    assert outcome.sample_failures is not None
    # Samples show the engine's canonical comparison form — integer-pair keys
    # render via Int64 ("3", #799; was Float64 "3.0").
    assert {r["id"] for r in outcome.sample_failures["mismatched"]} == {"3"}
    assert outcome.expected_value is not None
    assert outcome.expected_value["keys"] == ["id"]


def test_executor_success_has_no_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    source_conn, suite_conn = _conn(), _conn()
    df = pd.DataFrame({"id": [1], "v": ["a"]})
    frames = {source_conn.id: df, suite_conn.id: df.copy()}
    execute = _executor(monkeypatch, source_conn=source_conn, frames=frames, suite_conn=suite_conn)
    outcome = execute(_comparison_check(source_conn.id))
    assert outcome.success and outcome.metric_value == 0.0
    assert outcome.sample_failures is None


def test_executor_reader_refusal_is_error_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    source_conn = _conn()

    def _too_large(*a: Any, **kw: Any) -> Any:
        raise DatasetTooLargeError("dataset has 2000000 rows, over the comparison cap")

    monkeypatch.setattr(comparison_run, "read_dataset", _too_large)
    execute = comparison_run.build_comparison_executor(
        FakeSession({source_conn.id: source_conn}),  # type: ignore[arg-type]
        suite_connection=_conn(),
        target_table="T",
        target_schema=None,
        target_catalog=None,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    outcome = execute(_comparison_check(source_conn.id))
    assert outcome.errored
    assert outcome.error_message is not None and "comparison cap" in outcome.error_message


def test_executor_engine_refusal_is_error_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    source_conn, suite_conn = _conn(), _conn()
    frames = {
        source_conn.id: pd.DataFrame({"id": [1, 1], "v": ["a", "b"]}),  # duplicate keys
        suite_conn.id: pd.DataFrame({"id": [1], "v": ["a"]}),
    }
    execute = _executor(monkeypatch, source_conn=source_conn, frames=frames, suite_conn=suite_conn)
    outcome = execute(_comparison_check(source_conn.id))
    assert outcome.errored
    assert outcome.error_message is not None and "not unique" in outcome.error_message


def test_executor_unexpected_exception_is_classified_not_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_conn = _conn()

    def _boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("dsn=postgres://user:hunter2@host")  # must never surface raw

    monkeypatch.setattr(comparison_run, "read_dataset", _boom)
    execute = comparison_run.build_comparison_executor(
        FakeSession({source_conn.id: source_conn}),  # type: ignore[arg-type]
        suite_connection=_conn(),
        target_table="T",
        target_schema=None,
        target_catalog=None,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    outcome = execute(_comparison_check(source_conn.id))
    assert outcome.errored
    assert outcome.error_message is not None and "hunter2" not in outcome.error_message


def test_executor_missing_source_connection() -> None:
    execute = comparison_run.build_comparison_executor(
        FakeSession({}),  # type: ignore[arg-type]
        suite_connection=_conn(),
        target_table="T",
        target_schema=None,
        target_catalog=None,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    outcome = execute(_comparison_check(uuid.uuid4()))
    assert outcome.errored
    assert outcome.error_message is not None and "not found" in outcome.error_message


def test_executor_uses_target_query_and_source_query_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_conn, suite_conn = _conn(), _conn()
    seen_specs: list[Any] = []
    df = pd.DataFrame({"id": [1], "v": ["a"]})

    def _capture(connection: Connection, spec: Any, *, max_rows: int, secret_store: Any) -> Any:
        seen_specs.append((connection.id, spec))
        return df.copy()

    monkeypatch.setattr(comparison_run, "read_dataset", _capture)
    execute = comparison_run.build_comparison_executor(
        FakeSession({source_conn.id: source_conn}),  # type: ignore[arg-type]
        suite_connection=suite_conn,
        target_table="ORDERS_COPY",
        target_schema="RETAIL",
        target_catalog=None,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    check = _comparison_check(
        source_conn.id,
        source={"query": "SELECT id, v FROM RETAIL.ORDERS"},
        target_query="SELECT id, v FROM RETAIL.ORDERS_COPY",
    )
    outcome = execute(check)
    assert outcome.success
    by_conn = dict(seen_specs)
    assert by_conn[source_conn.id].query == "SELECT id, v FROM RETAIL.ORDERS"
    assert by_conn[suite_conn.id].query == "SELECT id, v FROM RETAIL.ORDERS_COPY"


# ───────────────────────── dispatch (run_service) ───────────────────


class _NoopRunner:
    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        return SuiteOutcome(success=True, checks=[CheckOutcome("e", success=True) for _ in checks])


def test_run_outcomes_routes_comparison_to_executor() -> None:
    comparison = _comparison_check(uuid.uuid4())
    expectation = Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="e",
        kind="expectation",
        expectation_type="e",
        config={},
    )
    calls: list[Check] = []

    def executor(check: Check) -> CheckOutcome:
        calls.append(check)
        return CheckOutcome("comparison:records", success=True, metric_value=0.0)

    outcomes = run_service._run_outcomes(
        _NoopRunner(),
        table="T",
        schema=None,
        checks=[expectation, comparison],
        comparison_executor=executor,
    )
    assert [o.expectation_type for o in outcomes] == ["e", "comparison:records"]
    assert calls == [comparison]


def test_run_outcomes_comparison_without_executor_errors() -> None:
    outcomes = run_service._run_outcomes(
        _NoopRunner(),
        table="T",
        schema=None,
        checks=[_comparison_check(uuid.uuid4())],
    )
    assert outcomes[0].errored
    assert outcomes[0].error_message is not None and "executor" in outcomes[0].error_message


# ───────────────────────── redaction (comparison buckets) ───────────


def test_executor_source_batch_not_found_is_friendly_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A routine "baseline hasn't landed" must not surface as the generic
    # UNKNOWN failure (BatchNotFoundError is a ValueError, not a DataQError).
    from backend.app.datasources.flatfile import BatchNotFoundError
    from backend.app.services import run_target as run_target_module

    source_conn = _conn("s3")

    def _no_batch(*a: Any, **kw: Any) -> Any:
        raise BatchNotFoundError("no files matched batch pattern")

    monkeypatch.setattr(run_target_module, "materialize_path", _no_batch)
    execute = comparison_run.build_comparison_executor(
        FakeSession({source_conn.id: source_conn}),  # type: ignore[arg-type]
        suite_connection=_conn(),
        target_table="T",
        target_schema=None,
        target_catalog=None,
        secret_store=FakeSecretStore(),  # type: ignore[arg-type]
    )
    check = _comparison_check(
        source_conn.id, source={"pattern": r"orders_(\d+)\.csv", "strategy": "latest"}
    )
    outcome = execute(check)
    assert outcome.errored
    assert outcome.error_message is not None and "batch not found" in outcome.error_message


def test_comparison_redaction_hard_masks_raw_suffixed_policy_names() -> None:
    # A pii_columns entry written exactly as the DISPLAYED suffixed column
    # ("status_src") — or a real column genuinely ending in _src — must still
    # mask; the hard-mask levels match both raw and stripped names.
    sample = {
        "mismatched": [{"order_id": "7", "status_src": "gold", "status_tgt": "gold"}],
    }
    policy = {"identifier_column": "order_id", "pii_columns": ["status_src"]}
    redacted = run_service.redact_sample_failures(sample, policy=policy)
    assert redacted is not None
    row = redacted["mismatched"][0]
    assert row["status_src"] == "<redacted>"  # raw-name policy entry honored
    assert row["order_id"] == "7"


def test_comparison_samples_redact_with_suffix_stripped_policy() -> None:
    sample = {
        "mismatched": [
            {"order_id": "7", "email_src": "a@x.io", "email_tgt": "b@x.io", "junk_src": "z"}
        ],
        "additional_in_source": [{"order_id": "9", "email_src": "c@x.io", "junk_src": "q"}],
    }
    policy = {"identifier_column": "order_id", "pii_columns": ["email"]}
    redacted = run_service.redact_sample_failures(sample, policy=policy)

    assert redacted is not None
    row = redacted["mismatched"][0]
    assert row["order_id"] == "7"  # identifier shown
    assert row["email_src"] == "<redacted>" and row["email_tgt"] == "<redacted>"  # pii masked
    assert row["junk_src"] == "<redacted>"  # unclassified defaults to masked
    extra = redacted["additional_in_source"][0]
    assert extra["order_id"] == "9" and extra["email_src"] == "<redacted>"


def test_executor_dispatches_columns_grain(monkeypatch: pytest.MonkeyPatch) -> None:
    """`comparison:columns` routes to compare_columns and maps the value-grain
    observed shape (+ tolerance passthrough into the engine)."""
    source_conn, suite_conn = _conn(), _conn()
    frames = {
        source_conn.id: pd.DataFrame({"id": [1, 2], "v": [10.0, 20.0]}),
        suite_conn.id: pd.DataFrame({"id": [1, 2], "v": [10.4, 25.0]}),
    }
    execute = _executor(monkeypatch, source_conn=source_conn, frames=frames, suite_conn=suite_conn)
    check = _comparison_check(source_conn.id, tolerance={"absolute": 0.5})
    check.expectation_type = "comparison:columns"
    outcome = execute(check)

    assert not outcome.errored
    assert outcome.observed_value is not None
    # Value grain: 10.0≈10.4 within 0.5 → matched; 20 vs 25 → mismatched.
    assert outcome.observed_value["matched_values"] == 1
    assert outcome.observed_value["mismatched_values"] == 1
    assert outcome.observed_value["per_column"]["v"]["mismatched"] == 1
    assert outcome.expected_value is not None
    assert outcome.expected_value["tolerance"] == {"absolute": 0.5}
    assert outcome.metric_value == 50.0
