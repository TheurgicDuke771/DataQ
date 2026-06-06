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
