"""What a run is estimated to materialise, and what admission does about it (#1998)."""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from backend.app.core import memory_budget
from backend.app.core.config import get_settings
from backend.app.core.memory_budget import Admission, MemoryBudget
from backend.app.datasources.flatfile import FileStat
from backend.app.db.models import Check, Connection, Run, Suite
from backend.app.services import run_admission

MiB = 1024 * 1024


class FakeSession:
    """Just the three reads `estimate_run_memory` makes."""

    def __init__(self, suite: Suite | None, connection: Connection | None, checks: list[Check]):
        self._objs: dict[type, Any] = {Suite: suite, Connection: connection}
        self._checks = checks
        self.commits = 0

    def get(self, model: type, _pk: Any) -> Any:
        return self._objs.get(model)

    def scalars(self, _stmt: Any) -> Any:
        return iter(self._checks)

    def commit(self) -> None:
        self.commits += 1


def _sess(session: FakeSession) -> Session:
    return cast(Session, session)


def _graph(
    conn_type: str,
    *,
    target: dict[str, Any],
    expectation_types: tuple[str, ...] = ("expect_column_values_to_not_be_null",),
) -> tuple[Run, FakeSession]:
    suite_id, conn_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run = Run(id=uuid.uuid4(), suite_id=suite_id, status="queued")
    suite = Suite(id=suite_id, name="s", connection_id=conn_id, created_by=user_id, target=target)
    connection = Connection(
        id=conn_id,
        name="c",
        type=conn_type,
        env="dev",
        config={"account": "a", "container": "raw", "bucket": "raw"},
        secret_ref="ref",
        created_by=user_id,
    )
    checks = [
        Check(
            id=uuid.uuid4(),
            suite_id=suite_id,
            name=f"c{i}",
            kind="expectation",
            expectation_type=etype,
            config={},
        )
        for i, etype in enumerate(expectation_types)
    ]
    return run, FakeSession(suite, connection, checks)


class StubSecretStore:
    def get(self, _ref: str) -> str:
        return "sec"


def _stub_secret_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_admission, "get_secret_store", StubSecretStore)


@pytest.fixture
def stub_flat_file(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stand in for the store's metadata call and the credential read."""

    def _stub(size: int | None) -> None:
        def _stat(**_kw: Any) -> FileStat:
            return FileStat(size=size)

        monkeypatch.setattr("backend.app.datasources.flatfile.file_stat", _stat)
        _stub_secret_store(monkeypatch)

    return _stub


# ───────────────────────── estimates ──────────────────────────────


def test_a_flat_file_run_is_estimated_from_the_object_size(stub_flat_file: Any) -> None:
    stub_flat_file(48 * MiB)
    run, session = _graph("s3", target={"path": "raw/orders.csv"})

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert estimate.basis == "flat_file_size"
    # The measured CSV expansion (8x), i.e. the 1M-row rung's ~750 MiB (perf-baseline.md).
    assert estimate.bytes == 48 * MiB * 8
    assert estimate.exclusive is False


@pytest.mark.parametrize(
    ("path", "factor"),
    [
        ("raw/orders.csv", 2.0),
        ("raw/orders.parquet", 3.0),
        ("raw/orders.pq", 3.0),
        ("raw/orders.jsonl", 5.0),
        ("raw/orders.ndjson", 5.0),
        ("raw/orders.json", 5.0),
        ("raw/orders.bin", 7.0),
    ],
)
def test_each_format_is_priced_at_its_own_expansion(
    monkeypatch: pytest.MonkeyPatch, stub_flat_file: Any, path: str, factor: float
) -> None:
    """Priced by the READER's format decision (#1677): a `.pq` file is read as Parquet and a
    `.jsonl` one as JSON, so pricing them as the default under-reserves the JSON path.
    """
    for name, value in (("CSV", 2), ("PARQUET", 3), ("JSON", 5), ("DEFAULT", 7)):
        monkeypatch.setenv(f"RUN_ADMISSION_EXPANSION_{name}", str(value))
    get_settings.cache_clear()
    stub_flat_file(1 * MiB)
    run, session = _graph("s3", target={"path": path})

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None and estimate.basis == "flat_file_size"
    assert estimate.bytes == int(1 * MiB * factor)


def test_a_sampled_run_is_estimated_from_the_sample_not_the_object(stub_flat_file: Any) -> None:
    """A `head` sample is flat at ~400-500 MiB regardless of object size — throttling it as a
    full read would serialise the very mode that exists to avoid the problem.
    """
    stub_flat_file(5000 * MiB)
    run, session = _graph(
        "s3",
        target={"path": "raw/orders.csv", "sampling": {"strategy": "head", "rows": 100_000}},
    )

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert estimate.basis == "flat_file_sample"
    assert estimate.bytes == 100_000 * get_settings().run_admission_row_bytes


def test_an_unknown_object_size_reads_as_unknown_not_as_zero(stub_flat_file: Any) -> None:
    stub_flat_file(None)
    run, session = _graph("s3", target={"path": "raw/orders.csv"})

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert estimate.exclusive is True


def test_a_failed_probe_never_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kw: Any) -> FileStat:
        raise RuntimeError("store unreachable")

    monkeypatch.setattr("backend.app.datasources.flatfile.file_stat", _boom)
    _stub_secret_store(monkeypatch)
    run, session = _graph("s3", target={"path": "raw/orders.csv"})

    assert run_admission.estimate_run_memory(_sess(session), run) is None


def test_a_batch_target_is_bounded_by_the_scan_cap_rather_than_listed_twice() -> None:
    """Resolving a batch means listing the store — which the run path does moments later."""
    run, session = _graph(
        "s3", target={"prefix": "raw/orders/", "pattern": r".*\.csv", "strategy": "latest"}
    )

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert estimate.basis == "flat_file_batch_cap"
    settings = get_settings()
    assert estimate.bytes == int(
        settings.run_max_scan_bytes * settings.run_admission_expansion_default
    )


def test_a_snowflake_run_bypasses_admission_entirely() -> None:
    """Pushdown holds no dataset in the worker, so it must not consume budget."""
    run, session = _graph("snowflake", target={"table": "ORDERS"})

    assert run_admission.estimate_run_memory(_sess(session), run) is None


def test_a_unity_catalog_pushdown_suite_bypasses_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The suite pins UC_SQL_PUSHDOWN=false globally; this case is about the ON state.
    monkeypatch.setenv("UC_SQL_PUSHDOWN", "true")
    get_settings.cache_clear()
    run, session = _graph(
        "unity_catalog",
        target={"catalog": "main", "schema": "gold", "table": "orders"},
        expectation_types=("expect_column_values_to_not_be_null",),
    )

    assert run_admission.estimate_run_memory(_sess(session), run) is None


_UC_TARGET = {"catalog": "main", "schema": "gold", "table": "orders"}
_FRAME_TYPES = ("expect_column_values_to_be_of_type",)


class FakeUcRunner:
    """The two probes admission asks the runner for, and whether it let go of the engine."""

    def __init__(self, *, rows: int, row_bytes: int, sampling: Any, fail: bool) -> None:
        self.rows, self.row_bytes, self.sampling, self.fail = rows, row_bytes, sampling, fail
        self.probed: list[tuple[str, str | None]] = []
        self.closed = False

    def probe_frame(self, *, table: str, schema: str | None) -> tuple[int, int]:
        self.probed.append((table, schema))
        if self.fail:
            raise RuntimeError("warehouse unavailable")
        rows = self.sampling.rows if self.sampling is not None else self.rows
        return rows, self.row_bytes

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_uc(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stand in for the UC runner's live probes; returns the runners it built."""
    built: list[FakeUcRunner] = []

    def _stub(
        *, rows: int = 10_000, row_bytes: int = 1_400, fail: bool = False
    ) -> list[FakeUcRunner]:
        def _build(*, catalog: str, sampling: Any = None, **_kw: Any) -> FakeUcRunner:
            assert catalog == "main"
            runner = FakeUcRunner(rows=rows, row_bytes=row_bytes, sampling=sampling, fail=fail)
            built.append(runner)
            return runner

        monkeypatch.setattr(
            "backend.app.datasources.unity_catalog.build_unity_catalog_runner", _build
        )
        _stub_secret_store(monkeypatch)
        return built

    return _stub


def test_a_small_unity_catalog_frame_reserves_what_it_needs_not_the_frame_cap(
    stub_uc: Any,
) -> None:
    # #2145: reserving the whole 1.25 GiB cap ran every UC frame suite alone, 10k rows included.
    built = stub_uc(rows=10_000, row_bytes=1_400)
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate == run_admission.MemoryEstimate(bytes=14_000_000, basis="uc_frame_width")
    assert built[0].probed == [("orders", "gold")]
    assert built[0].closed


def test_a_wide_unity_catalog_frame_is_priced_at_its_width(stub_uc: Any) -> None:
    # The flat 1,024 B/row under-reserved a 2,340 B/row table by more than half.
    stub_uc(rows=100_000, row_bytes=2_340)
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert estimate.bytes == 234_000_000


def test_a_unity_catalog_frame_over_the_frame_cap_holds_nothing(stub_uc: Any) -> None:
    # The runner refuses it on the same two numbers before reading a row.
    cap = get_settings().run_max_frame_bytes
    stub_uc(rows=cap // 1_000 + 1, row_bytes=1_000)
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)

    assert run_admission.estimate_run_memory(_sess(session), run) == run_admission.MemoryEstimate(
        bytes=0, basis="uc_over_cap"
    )


def test_a_unity_catalog_frame_over_the_row_cap_holds_nothing(
    monkeypatch: pytest.MonkeyPatch, stub_uc: Any
) -> None:
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS", "1000")
    get_settings.cache_clear()
    stub_uc(rows=1_001, row_bytes=100)
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert (estimate.bytes, estimate.basis) == (0, "uc_over_cap")


def test_a_unity_catalog_frame_with_no_caps_is_still_sized_not_run_alone(
    monkeypatch: pytest.MonkeyPatch, stub_uc: Any
) -> None:
    monkeypatch.setenv("RUN_MAX_FRAME_BYTES", "0")
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS", "0")
    get_settings.cache_clear()
    stub_uc(rows=50_000_000, row_bytes=1_400)
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate == run_admission.MemoryEstimate(bytes=70_000_000_000, basis="uc_frame_width")


def test_a_sampled_unity_catalog_run_is_priced_at_the_samples_width(stub_uc: Any) -> None:
    built = stub_uc(rows=9_000_000, row_bytes=2_000)
    run, session = _graph(
        "unity_catalog",
        target={**_UC_TARGET, "sampling": {"strategy": "head", "rows": 20_000}},
    )

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate == run_admission.MemoryEstimate(bytes=40_000_000, basis="uc_sample_width")
    assert built[0].sampling.rows == 20_000


def test_a_unity_catalog_sample_over_the_row_cap_holds_nothing_and_probes_nothing(
    monkeypatch: pytest.MonkeyPatch, stub_uc: Any
) -> None:
    monkeypatch.setenv("RUN_MAX_SCAN_ROWS", "1000")
    get_settings.cache_clear()
    built = stub_uc()
    run, session = _graph(
        "unity_catalog",
        target={**_UC_TARGET, "sampling": {"strategy": "head", "rows": 5_000}},
    )

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert (estimate.bytes, estimate.basis) == (0, "uc_over_cap")
    assert built == []


def test_a_unity_catalog_connection_without_a_credential_is_not_probed(stub_uc: Any) -> None:
    built = stub_uc()
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)
    cast(Connection, session.get(Connection, None)).secret_ref = None

    assert run_admission.estimate_run_memory(_sess(session), run) is None
    assert built == []


def test_a_failed_unity_catalog_probe_reserves_the_frame_cap_and_releases_the_engine(
    stub_uc: Any,
) -> None:
    # A waking warehouse can answer the runner's probes moments later; the frame must not
    # then load unmetered.
    built = stub_uc(fail=True)
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)

    assert run_admission.estimate_run_memory(_sess(session), run) == run_admission.MemoryEstimate(
        bytes=get_settings().run_max_frame_bytes, basis="uc_probe_failed"
    )
    assert built[0].closed


def test_a_failed_unity_catalog_probe_without_a_frame_cap_runs_alone(
    monkeypatch: pytest.MonkeyPatch, stub_uc: Any
) -> None:
    monkeypatch.setenv("RUN_MAX_FRAME_BYTES", "0")
    get_settings.cache_clear()
    stub_uc(fail=True)
    run, session = _graph("unity_catalog", target=_UC_TARGET, expectation_types=_FRAME_TYPES)

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None and estimate.exclusive


def test_an_over_cap_object_is_clamped_to_the_cap_it_will_be_refused_at(
    stub_flat_file: Any,
) -> None:
    """Otherwise a run that fails in a second first queues behind every other one."""
    settings = get_settings()
    stub_flat_file(settings.run_max_scan_bytes * 40)
    run, session = _graph("s3", target={"path": "raw/orders.csv"})

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert estimate.bytes == int(settings.run_max_scan_bytes * settings.run_admission_expansion_csv)


def test_comparison_checks_are_metered_even_on_a_pushdown_connection() -> None:
    """Both sides of a comparison land in the worker whatever the datasource is (ADR 0015),
    so the Snowflake bypass must not carry them through unmetered.
    """
    run, session = _graph("snowflake", target={"table": "ORDERS"})
    session._checks[0].kind = "comparison"

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    assert estimate is not None
    assert estimate.basis == "comparison_sides"
    settings = get_settings()
    assert estimate.bytes == 2 * settings.comparison_max_rows * settings.run_admission_row_bytes


def test_comparison_sides_add_to_the_suite_s_own_batch(stub_flat_file: Any) -> None:
    stub_flat_file(1 * MiB)
    run, session = _graph("s3", target={"path": "raw/orders.csv"})
    session._checks[0].kind = "comparison"

    estimate = run_admission.estimate_run_memory(_sess(session), run)

    settings = get_settings()
    assert estimate is not None
    assert estimate.basis == "flat_file_size"
    assert estimate.bytes == int(1 * MiB * settings.run_admission_expansion_csv) + (
        2 * settings.comparison_max_rows * settings.run_admission_row_bytes
    )


def test_a_flat_file_connection_without_a_credential_is_not_silently_unmetered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(run_admission.log, "info", lambda event, **_kw: events.append(event))
    run, session = _graph("s3", target={"path": "raw/orders.csv"})
    connection = session.get(Connection, None)
    connection.secret_ref = None

    assert run_admission.estimate_run_memory(_sess(session), run) is None

    assert "run_admission_no_credential" in events


# ───────────────────────── the admission decision ─────────────────


class StubBudget:
    def __init__(self, outcomes: list[Admission]) -> None:
        self.outcomes = outcomes
        self.reserved: list[tuple[str, int]] = []
        self.budget_bytes = 1000 * MiB

    def reserve(self, key: str, amount: int) -> Admission:
        self.reserved.append((key, amount))
        return self.outcomes.pop(0)


@pytest.fixture
def stub_budget(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(*outcomes: Admission) -> StubBudget:
        budget = StubBudget(list(outcomes))
        monkeypatch.setattr(run_admission, "get_memory_budget", lambda: cast(MemoryBudget, budget))
        return budget

    return _install


def _run() -> Run:
    return Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")


def test_no_estimate_means_no_reservation(stub_budget: Any) -> None:
    budget = stub_budget()
    session = FakeSession(None, None, [])

    decision = run_admission.admit(_sess(session), run=_run(), estimate=None, waited_out=False)

    assert decision.defer is False
    assert budget.reserved == []


def test_a_refused_reservation_defers_rather_than_failing_the_run(stub_budget: Any) -> None:
    stub_budget(Admission(admitted=False, used_bytes=900 * MiB))
    session = FakeSession(None, None, [])

    decision = run_admission.admit(
        _sess(session),
        run=_run(),
        estimate=run_admission.MemoryEstimate(bytes=400 * MiB, basis="flat_file_size"),
        waited_out=False,
    )

    assert decision.defer is True
    assert decision.timed_out is False


def test_past_the_wait_budget_the_run_proceeds_rather_than_starving(stub_budget: Any) -> None:
    stub_budget(Admission(admitted=False, used_bytes=900 * MiB))
    session = FakeSession(None, None, [])

    decision = run_admission.admit(
        _sess(session),
        run=_run(),
        estimate=run_admission.MemoryEstimate(bytes=400 * MiB, basis="flat_file_size"),
        waited_out=True,
    )

    assert decision.defer is False
    assert decision.timed_out is True


def test_an_unknown_size_reserves_the_whole_budget(stub_budget: Any) -> None:
    budget = stub_budget(Admission(admitted=True, used_bytes=1000 * MiB))
    session = FakeSession(None, None, [])
    run = _run()

    run_admission.admit(
        _sess(session),
        run=run,
        estimate=run_admission.MemoryEstimate(
            bytes=0, basis="flat_file_size_unknown", exclusive=True
        ),
        waited_out=False,
    )

    assert budget.reserved == [(str(run.id), 1000 * MiB)]


def test_admission_off_admits_without_touching_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUN_ADMISSION_BUDGET_BYTES", "0")
    get_settings.cache_clear()
    memory_budget.reset_memory_budget_state()
    session = FakeSession(None, None, [])
    try:
        decision = run_admission.admit(
            _sess(session),
            run=_run(),
            estimate=run_admission.MemoryEstimate(bytes=9_000 * MiB, basis="flat_file_size"),
            waited_out=False,
        )
    finally:
        get_settings.cache_clear()
        memory_budget.reset_memory_budget_state()

    assert decision.defer is False


def test_the_wait_budget_stays_inside_the_stuck_run_reaper_window() -> None:
    """A waiting run must proceed long before the reaper would call it stuck and fail it."""
    settings = get_settings()

    assert settings.run_admission_max_wait_seconds < settings.stuck_run_threshold_minutes * 60
