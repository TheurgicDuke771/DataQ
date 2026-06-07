"""Flat-file IO + GX runner tests.

Unlike the warehouse runners (which need a live datasource), the flat-file runner
runs GX in-process on a pandas DataFrame — so the full run path is tested with a
canned frame; only the network `download_bytes` is the deferred-smoke seam.
"""

from typing import Any

import pandas as pd
import pytest

from backend.app.datasources import flatfile
from backend.app.datasources.base import CheckSpec


class _FakeStore:
    def get(self, ref: str) -> str:
        return "tok"


# ── format_from_path ──


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("data/orders.csv", "csv"),
        ("DATA/ORDERS.CSV", "csv"),
        ("x.parquet", "parquet"),
        ("x.pq", "parquet"),
        ("noext", None),
        ("data/orders.txt", None),
    ],
)
def test_format_from_path(path: str, expected: str | None) -> None:
    assert flatfile.format_from_path(path) == expected


# ── read_dataframe (real parse, mocked download) ──


def test_read_dataframe_reads_full_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: b"a,b\n1,2\n3,4\n")
    df = flatfile.read_dataframe(conn_type="s3", config={}, path="x.csv", secret="s")
    assert list(df.columns) == ["a", "b"] and len(df) == 2


def test_read_dataframe_reads_full_parquet(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    buf = io.BytesIO()
    pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_parquet(buf)
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: buf.getvalue())
    df = flatfile.read_dataframe(conn_type="s3", config={}, path="x.parquet", secret="s")
    assert set(df.columns) == {"a", "b"} and len(df) == 2


def test_read_dataframe_unknown_format_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: b"")
    with pytest.raises(ValueError, match="unsupported flat-file format"):
        flatfile.read_dataframe(conn_type="s3", config={}, path="x.txt", secret="s")


# ── build_flatfile_runner ──


def test_build_flatfile_runner_resolves_secret() -> None:
    runner = flatfile.build_flatfile_runner(
        conn_type="s3", config={"bucket": "b"}, secret_ref="ref", secret_store=_FakeStore()
    )
    assert isinstance(runner, flatfile.FlatFileCheckRunner)


def test_build_flatfile_runner_rejects_non_flatfile_type() -> None:
    with pytest.raises(ValueError, match="not a flat-file datasource"):
        flatfile.build_flatfile_runner(
            conn_type="snowflake", config={}, secret_ref="ref", secret_store=_FakeStore()
        )


def test_build_flatfile_runner_requires_secret_ref() -> None:
    with pytest.raises(ValueError, match="requires secret_ref"):
        flatfile.build_flatfile_runner(
            conn_type="s3", config={}, secret_ref=None, secret_store=_FakeStore()
        )


# ── FlatFileCheckRunner.run_checks (real GX on an in-memory DataFrame) ──


def _runner_over(df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: df)
    return flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")


def test_run_checks_runs_gx_expectations(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1, 2, None], "amt": [10, 20, 30]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="data/orders.csv",
        schema=None,
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 1, "max_value": 10}),
        ],
    )
    # suite fails because id has a null; per-check successes map through
    assert outcome.success is False
    by_type = {c.expectation_type: c for c in outcome.checks}
    assert by_type["expect_column_values_to_not_be_null"].success is False
    assert by_type["expect_table_row_count_to_be_between"].success is True
    # observed_value flows through the shared mapping
    assert by_type["expect_table_row_count_to_be_between"].observed_value == {"observed_value": 3}


def test_run_checks_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.DataFrame({"id": [1, 2, 3]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="data/orders.parquet",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.success is True
    assert outcome.checks[0].success is True


# ── batch resolution (pure resolve_batch + mocked list orchestrator) ──

from datetime import UTC, datetime  # noqa: E402


def _dt(day: int) -> datetime:
    return datetime(2026, 6, day, tzinfo=UTC)


_BATCH_FILES = [
    flatfile.FileRef("data/orders_2026-06-01.csv", _dt(1)),
    flatfile.FileRef("data/orders_2026-06-03.csv", _dt(3)),
    flatfile.FileRef("data/orders_2026-06-02.csv", _dt(2)),
    flatfile.FileRef("data/other.csv", _dt(9)),  # doesn't match the pattern
]

_PATTERN = r"orders_(\d{4}-\d{2}-\d{2})\.csv"


def test_resolve_batch_latest_by_capture_group() -> None:
    # greatest batch key wins (ISO dates sort lexicographically = chronologically)
    assert flatfile.resolve_batch(_BATCH_FILES, pattern=_PATTERN) == "data/orders_2026-06-03.csv"


def test_resolve_batch_specific_by_key() -> None:
    got = flatfile.resolve_batch(
        _BATCH_FILES, pattern=_PATTERN, strategy="specific", batch="2026-06-02"
    )
    assert got == "data/orders_2026-06-02.csv"


def test_resolve_batch_latest_falls_back_to_mtime_without_group() -> None:
    # no capture group → pick most recently modified among matches
    files = [
        flatfile.FileRef("a/load.csv", _dt(1)),
        flatfile.FileRef("b/load.csv", _dt(5)),
    ]
    assert flatfile.resolve_batch(files, pattern=r"load\.csv") == "b/load.csv"


def test_resolve_batch_no_match_raises() -> None:
    with pytest.raises(flatfile.BatchNotFoundError):
        flatfile.resolve_batch(_BATCH_FILES, pattern=r"invoices_(\d+)\.csv")


def test_resolve_batch_specific_unknown_key_raises() -> None:
    with pytest.raises(flatfile.BatchNotFoundError):
        flatfile.resolve_batch(
            _BATCH_FILES, pattern=_PATTERN, strategy="specific", batch="2099-01-01"
        )


def test_resolve_batch_specific_requires_batch() -> None:
    with pytest.raises(ValueError, match="requires a batch key"):
        flatfile.resolve_batch(_BATCH_FILES, pattern=_PATTERN, strategy="specific")


def test_resolve_batch_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError, match="unknown batch strategy"):
        flatfile.resolve_batch(_BATCH_FILES, pattern=_PATTERN, strategy="earliest")


def test_resolve_batch_file_lists_then_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatfile, "list_files", lambda **kwargs: _BATCH_FILES)
    got = flatfile.resolve_batch_file(
        conn_type="s3", config={}, secret="s", prefix="data/", pattern=_PATTERN
    )
    assert got == "data/orders_2026-06-03.csv"


def test_resolve_batch_optional_group_no_crash() -> None:
    # an optional first group that doesn't participate (key=None) must not crash
    # the latest selection; keyed files win, unkeyed fall back to mtime.
    files = [
        flatfile.FileRef("orders_.csv", _dt(9)),  # group didn't match → key None
        flatfile.FileRef("orders_2026-06-01.csv", _dt(1)),
    ]
    assert flatfile.resolve_batch(files, pattern=r"orders_(\d{4}-\d{2}-\d{2})?\.csv") == (
        "orders_2026-06-01.csv"
    )


def test_resolve_batch_optional_group_all_none_falls_back_to_mtime() -> None:
    files = [flatfile.FileRef("orders_.csv", _dt(1)), flatfile.FileRef("orders_x.csv", _dt(5))]
    # neither has a numeric key → fall back to most recent; no None-vs-str compare
    assert flatfile.resolve_batch(files, pattern=r"orders_(\d+)?[\w]*\.csv") == "orders_x.csv"


def test_resolve_batch_invalid_pattern_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="invalid batch pattern"):
        flatfile.resolve_batch(_BATCH_FILES, pattern=r"orders_([0-9]+")  # unbalanced (
