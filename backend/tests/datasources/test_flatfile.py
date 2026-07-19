"""Flat-file IO + GX runner tests.

Unlike the warehouse runners (which need a live datasource), the flat-file runner
runs GX in-process on a pandas DataFrame — so the full run path is tested with a
canned frame; only the network `download_bytes` is the deferred-smoke seam.
"""

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from backend.app.datasources import flatfile
from backend.app.datasources.base import CheckSpec


class _FakeStore:
    def get(self, name: str) -> str:
        return "tok"

    def set(self, name: str, value: str) -> None:  # read-only test double
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


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


# ── sniff_delimiter / read_csv_bytes (#476) ──


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (b"a,b,c\n1,2,3\n4,5,6\n", ","),
        (b"a;b;c\n1;2;3\n4;5;6\n", ";"),
        (b"a\tb\tc\n1\t2\t3\n4\t5\t6\n", "\t"),
        (b"a|b|c\n1|2|3\n4|5|6\n", "|"),
    ],
)
def test_sniff_delimiter_detects_each_supported_delimiter(sample: bytes, expected: str) -> None:
    assert flatfile.sniff_delimiter(sample) == expected


@pytest.mark.parametrize(
    "sample",
    [
        b"",  # empty file
        b"\n\n  \n",  # whitespace only
        b"only_one_column\n1\n2\n",  # nothing to infer a delimiter from
        b"\x00\x01\x02\xff\xfe",  # binary junk (undecodable)
        b"a,b\n",  # header with no data rows
    ],
)
def test_sniff_delimiter_falls_back_to_comma_when_undecidable(sample: bytes) -> None:
    """An unsniffable sample must degrade to the pre-#476 behaviour, never raise —
    a wrong-but-unchanged answer beats a 502 on a file that parses today."""
    assert flatfile.sniff_delimiter(sample) == ","


def test_sniff_delimiter_ignores_a_truncated_trailing_row() -> None:
    """The sniff sample is a byte prefix, so the last line is usually cut. A
    fragment with mismatched field counts is what Sniffer keys off, so it must be
    dropped — otherwise the delimiter flips depending on where the cut landed."""
    assert flatfile.sniff_delimiter(b"a;b;c\n1;2;3\n4;5") == ";"


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        # A foreign delimiter inside a quoted field, both directions. This is the
        # exact silent-wrong-answer class the change exists to prevent, so it is
        # pinned rather than left to Sniffer's current behaviour.
        (b'name,note\n"a;b;c",1\n"d;e;f",2\n', ","),
        (b'name;note\n"a,b,c";1\n"d,e,f";2\n', ";"),
        (b"\xef\xbb\xbfa;b\n1;2\n", ";"),  # UTF-8 BOM
        (b"a;b\r\n1;2\r\n3;4\r\n", ";"),  # CRLF
    ],
)
def test_sniff_delimiter_survives_quoting_bom_and_crlf(sample: bytes, expected: str) -> None:
    assert flatfile.sniff_delimiter(sample) == expected


def test_read_csv_bytes_bounds_the_sniff_sample_on_a_large_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_SNIFF_BYTES` bounds the UTF-8 decode, so the runner — which reads whole
    objects by design — doesn't materialise a multi-GB file as a str just to pick
    a delimiter.

    Asserted by observing the sample handed to the sniffer, not by parse
    correctness: the bound is a RESOURCE property, and a correctness assertion
    passes with or without the slice (verified by mutation), so it would pin
    nothing.
    """
    seen: list[bytes] = []

    def _spy(sample: bytes) -> str:
        seen.append(sample)
        return ";"

    monkeypatch.setattr(flatfile, "sniff_delimiter", _spy)
    raw = io.BytesIO(b"alpha;beta\n" + b"1;2\n" * 20_000)
    df = flatfile.read_csv_bytes(raw)

    assert len(raw.getvalue()) > flatfile._SNIFF_BYTES  # the fixture must exceed it
    assert seen and len(seen[0]) == flatfile._SNIFF_BYTES
    assert list(df.columns) == ["alpha", "beta"] and len(df) == 20_000


def test_sniff_delimiter_never_picks_a_delimiter_outside_the_allowlist() -> None:
    """Left free, csv.Sniffer nominates letters/spaces on prose-ish headers. The
    allowlist keeps a bad guess bounded to a comma."""
    assert flatfile.sniff_delimiter(b"name title\nalice engineer\nbob analyst\n") == ","


def test_read_csv_bytes_parses_a_semicolon_file_correctly() -> None:
    df = flatfile.read_csv_bytes(io.BytesIO(b"a;b;c\n1;2;3\n4;5;6\n"))
    assert list(df.columns) == ["a", "b", "c"] and len(df) == 2


def test_read_csv_bytes_rewinds_a_consumed_buffer() -> None:
    """Callers may hand over a buffer already read (the sniff itself consumes it),
    so the parse must not silently see zero bytes."""
    raw = io.BytesIO(b"a;b\n1;2\n")
    raw.read()
    df = flatfile.read_csv_bytes(raw)
    assert list(df.columns) == ["a", "b"] and len(df) == 1


def test_read_csv_bytes_passes_through_reader_kwargs() -> None:
    df = flatfile.read_csv_bytes(io.BytesIO(b"a;b;c\n1;2;3\n4;5;6\n"), nrows=1, usecols=["a", "c"])
    assert list(df.columns) == ["a", "c"] and len(df) == 1


def test_read_dataframe_parses_a_semicolon_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """#476: a `;`-delimited file used to yield ONE bogus column (the whole header
    line) with no error — a silent wrong answer for every check on that file."""
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: b"a;b\n1;2\n3;4\n")
    df = flatfile.read_dataframe(conn_type="s3", config={}, path="x.csv", secret="s")
    assert list(df.columns) == ["a", "b"] and len(df) == 2


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


# ── run_monitors (#520) ──


def _monitor_runner() -> Any:
    return flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="s")


def _spec(kind: str, **config: Any) -> Any:
    from backend.app.datasources.base import MonitorSpec

    return MonitorSpec(kind=kind, config=config)


_LANDED = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)


def _patch_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mtime: datetime | None = _LANDED,
    csv: bytes = b"id,load_ts\n1,2026-06-29T00:00:00\n2,2026-06-28T00:00:00\n",
    reads: list[int] | None = None,
) -> None:
    """Stub both live seams: the listing (arrival time) and the download."""
    monkeypatch.setattr(flatfile, "file_last_modified", lambda **k: mtime)

    def _download(**_k: Any) -> bytes:
        if reads is not None:
            reads.append(1)
        return csv

    monkeypatch.setattr(flatfile, "download_bytes", _download)


def test_volume_monitor_counts_rows_of_the_resolved_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("volume", min_rows=5, max_rows=10)]
    )
    assert out[0].errored is False
    # 2 rows against a floor of 5 → 60% short.
    assert out[0].metric_value == pytest.approx(60.0)


def test_freshness_with_a_column_uses_the_in_file_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same semantics as the SQL runners: the newest timestamp INSIDE the data."""
    _patch_store(monkeypatch)
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="load_ts")]
    )
    assert out[0].errored is False
    assert out[0].observed_value is not None
    assert out[0].observed_value["max_timestamp"].startswith("2026-06-29T00:00:00")


def test_freshness_without_a_column_uses_file_arrival_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#520's headline case — and the one no SQL datasource can express."""
    _patch_store(monkeypatch, mtime=datetime(2026, 6, 20, tzinfo=UTC))
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness")]
    )
    assert out[0].errored is False
    assert out[0].observed_value is not None
    assert out[0].observed_value["max_timestamp"].startswith("2026-06-20")
    assert out[0].expected_value == {"monitor": "freshness", "source": "file_modified_time"}


def test_arrival_time_freshness_never_downloads_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of arrival-time freshness is that it costs a LISTING, not a
    data read — otherwise it is strictly worse than the in-file MAX it replaces."""
    reads: list[int] = []
    _patch_store(monkeypatch, reads=reads)
    _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness")]
    )
    assert reads == []


def test_the_file_is_downloaded_at_most_once_across_monitors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three content-needing monitors on one file must not pull the object three
    times — the memo is the flat-file analogue of the SQL runners' one connection."""
    reads: list[int] = []
    _patch_store(monkeypatch, reads=reads)
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv",
        schema=None,
        monitors=[
            _spec("volume", min_rows=1, max_rows=10),
            _spec("freshness", column="load_ts"),
            _spec("volume", min_rows=1, max_rows=10),
        ],
    )
    assert [o.errored for o in out] == [False, False, False]
    assert len(reads) == 1


def test_a_failed_download_is_attempted_once_not_once_per_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The memo caches the ATTEMPT, not the frame. Memoizing only successes leaves
    a failure unmemoised, so every later monitor retries the whole download — five
    monitors against a failing 2 GB object would be five full downloads, and a
    transient failure would produce inconsistent outcomes inside one run.

    This is the #904 shape exactly: the defect lives in state carried ACROSS
    iterations, which a single-iteration test can't see."""
    reads: list[int] = []

    def _boom(**_k: Any) -> bytes:
        reads.append(1)
        raise RuntimeError("connection reset")

    monkeypatch.setattr(flatfile, "file_last_modified", lambda **k: _LANDED)
    monkeypatch.setattr(flatfile, "download_bytes", _boom)

    out = _monitor_runner().run_monitors(
        table="raw/orders.csv",
        schema=None,
        monitors=[
            _spec("volume", min_rows=1, max_rows=10),
            _spec("freshness", column="load_ts"),
            _spec("volume", min_rows=1, max_rows=10),
        ],
    )
    assert [o.errored for o in out] == [True, True, True]
    assert len(reads) == 1


def test_a_read_failure_message_is_classified_never_the_raw_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A monitor's error message is persisted to `results` and rendered in the UI,
    alerts and MCP output. Azure auth failures on this project have carried the SAS
    query string in their text (#828/#839), so the reason must be CLASSIFIED — the
    raw exception is logged (where the redactor sits), never echoed outward."""
    secret_ish = "sig=AbC123SuperSecretSasToken&se=2027"

    def _boom(**_k: Any) -> bytes:
        raise RuntimeError(f"auth failed for https://acct.blob.core.windows.net/x?{secret_ish}")

    monkeypatch.setattr(flatfile, "file_last_modified", lambda **k: _LANDED)
    monkeypatch.setattr(flatfile, "download_bytes", _boom)

    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("volume", min_rows=1, max_rows=2)]
    )
    message = out[0].error_message or ""
    assert out[0].errored is True
    assert secret_ish not in message
    assert "auth failed" not in message
    assert "could not read" in message


def test_a_missing_file_errors_rather_than_reporting_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing object is exactly the incident arrival-time freshness exists to
    catch, so a None arrival time must NOT read as age zero."""
    _patch_store(monkeypatch, mtime=None)
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness")]
    )
    assert out[0].errored is True
    assert out[0].metric_value is None


def test_an_unreachable_store_fails_the_whole_run_not_one_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The open-connection-first contract the SQL and Iceberg runners keep: a bad
    credential is a run failure, not N identical per-check errors."""

    def _boom(**_k: Any) -> Any:
        raise RuntimeError("credential expired")

    monkeypatch.setattr(flatfile, "file_last_modified", _boom)
    with pytest.raises(RuntimeError, match="credential expired"):
        _monitor_runner().run_monitors(
            table="raw/orders.csv",
            schema=None,
            monitors=[_spec("volume", min_rows=1, max_rows=2)],
        )


def test_an_unknown_freshness_column_errors_only_that_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv",
        schema=None,
        monitors=[_spec("freshness", column="nope"), _spec("volume", min_rows=1, max_rows=10)],
    )
    assert out[0].errored is True and "not in" in (out[0].error_message or "")
    assert out[1].errored is False


def test_an_all_null_freshness_column_cannot_be_assessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty MAX must route through "can't be assessed", never age zero — a
    silent green on a column that carries no timestamps at all."""
    _patch_store(monkeypatch, csv=b"id,load_ts\n1,\n2,\n")
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="load_ts")]
    )
    assert out[0].errored is True
    assert out[0].metric_value is None


def test_freshness_column_works_on_a_real_parquet_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rides REAL Parquet bytes through the real `read_dataframe`, not a hand-built
    frame — because that difference was the whole bug.

    `read_dataframe` reads Parquet with `dtype_backend="pyarrow"`, so a timestamp
    column arrives as `timestamp[ns][pyarrow]`, for which `is_datetime64_any_dtype`
    is **False**. Column freshness therefore failed on every Parquet file with
    "your timestamp column is not a timestamp" — while the entire suite stayed
    green, because every other fixture here builds a numpy-backed DataFrame by
    hand. Same shape as the #823 lineage bug: the fixture encoded our mental model
    instead of the real payload.
    """
    buf = io.BytesIO()
    pd.DataFrame(
        {"id": [1, 2], "load_ts": pd.to_datetime(["2026-06-28", "2026-06-29"])}
    ).to_parquet(buf)
    _patch_store(monkeypatch)
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: buf.getvalue())

    out = _monitor_runner().run_monitors(
        table="raw/orders.parquet",
        schema=None,
        monitors=[_spec("freshness", column="load_ts")],
    )
    assert out[0].errored is False, out[0].error_message
    assert out[0].observed_value is not None
    assert out[0].observed_value["max_timestamp"].startswith("2026-06-29")


def test_volume_works_on_a_real_parquet_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    buf = io.BytesIO()
    pd.DataFrame({"id": [1, 2, 3]}).to_parquet(buf)
    _patch_store(monkeypatch)
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: buf.getvalue())
    out = _monitor_runner().run_monitors(
        table="raw/orders.parquet", schema=None, monitors=[_spec("volume", min_rows=3, max_rows=5)]
    )
    assert out[0].errored is False and out[0].metric_value == 0.0


def test_arrow_backed_numeric_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The epoch-trap guard must survive the Arrow-dtype fix: widening the temporal
    check must not accidentally let `int64[pyarrow]` through."""
    buf = io.BytesIO()
    pd.DataFrame({"id": [1, 2], "order_no": [1001, 1002]}).to_parquet(buf)
    _patch_store(monkeypatch)
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: buf.getvalue())
    out = _monitor_runner().run_monitors(
        table="raw/orders.parquet", schema=None, monitors=[_spec("freshness", column="order_no")]
    )
    assert out[0].errored is True
    assert "not a date/timestamp" in (out[0].error_message or "")


def test_a_numeric_freshness_column_is_refused_not_read_as_epoch_offsets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nastiest failure mode this path can have. `pd.to_datetime` reads integers
    as epoch offsets, so a freshness monitor pointed at an id column would date the
    data to 1970 and fire CRITICAL staleness forever — a confident wrong answer,
    and one that looks exactly like a real incident. It must refuse instead."""
    _patch_store(monkeypatch, csv=b"id,order_no\n1,1001\n2,1002\n")
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="order_no")]
    )
    assert out[0].errored is True
    assert out[0].metric_value is None
    assert "not a date/timestamp" in (out[0].error_message or "")


def test_csv_string_timestamps_are_parsed_not_string_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CSV's timestamp column is object-dtype STRINGS (Parquet's is a real
    datetime). Lexical max agrees with chronological max for ISO-8601, so the bug
    hides behind the common format — this fixture picks one where they DISAGREE:
    lexically "2026-Nov-30" > "2026-Dec-01" (N > D), chronologically it's the
    reverse. A string max would report November as the newest data.

    Deliberately unambiguous, too: a `29/06/2026` fixture would lean on pandas'
    day-first *inference*, which is version-dependent and would make this test
    assert the parser's guess rather than our behaviour."""
    _patch_store(
        monkeypatch,
        csv=b"id,load_ts\n1,2026-Nov-30 00:00\n2,2026-Dec-01 00:00\n",
    )
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="load_ts")]
    )
    assert out[0].errored is False
    assert out[0].observed_value is not None
    assert out[0].observed_value["max_timestamp"].startswith("2026-12-01")


def test_an_unparseable_text_freshness_column_cannot_be_assessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch, csv=b"id,load_ts\n1,not-a-date\n2,also-not\n")
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="load_ts")]
    )
    assert out[0].errored is True
    assert out[0].metric_value is None


def test_runner_advertises_the_kinds_it_implements() -> None:
    """#429: the run-path gate reads this, so it must match reality."""
    assert flatfile.FlatFileCheckRunner.supported_monitor_kinds == frozenset(
        {"freshness", "volume"}
    )


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


def test_run_checks_index_columns_capture_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    # #415: requesting index_columns makes GX return a per-row unexpected_index_list
    # carrying the identifier column + the failing value — the row locator.
    df = pd.DataFrame(
        {
            "order_number": ["ORD-1", None, "ORD-3", None],
            "customer_id": [4471, 8823, 91, 20455],
        }
    )
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="data/orders.parquet",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "order_number"})],
        index_columns=["customer_id"],
    )
    sample = outcome.checks[0].sample_failures
    assert sample is not None
    rows = sample["unexpected_index_list"]
    # the two null rows, each dict carrying the identifier + the (null) tested value
    assert {r["customer_id"] for r in rows} == {8823, 20455}
    assert all("order_number" in r for r in rows)


def test_run_checks_bad_index_column_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # An absent identifier column errors GX's index metric on every check; the runner
    # falls back to a plain run so the checks still evaluate (no index, not all-errored).
    df = pd.DataFrame({"order_number": ["ORD-1", None, "ORD-3"]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="data/orders.parquet",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "order_number"})],
        index_columns=["does_not_exist"],
    )
    assert outcome.checks[0].errored is False
    assert outcome.checks[0].success is False  # the real null failure still surfaces
    assert "unexpected_index_list" not in (outcome.checks[0].sample_failures or {})


def test_run_checks_errored_check_flagged_without_failing_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check that raises while evaluating (here: referencing a missing column)
    is flagged `errored` via GX's per-expectation `exception_info` (#122) — real
    GX end to end — while a sibling on a real column still evaluates cleanly. This
    is the producer the run-service maps to `error`. (The `exception_info` shape
    branches are unit-tested directly in `test_gx_runner.py`.)"""
    df = pd.DataFrame({"id": [1, 2, 3]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="data/orders.csv",
        schema=None,
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "does_not_exist"}),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )
    by_type_first = outcome.checks[0]
    assert by_type_first.errored is True
    assert by_type_first.error_message and "does_not_exist" in by_type_first.error_message
    # the sibling on a real column evaluated cleanly — not errored
    assert outcome.checks[1].errored is False
    assert outcome.checks[1].success is True


def test_run_checks_errored_check_maps_to_its_own_spec_despite_gx_reorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#767: GX 1.17 `graph_validate` returns errored expectations FIRST, so the
    outcome list order ≠ submission order once anything errors. The errored check
    here is submitted **last** (so the reorder actively moves it to the front) — the
    outcome must still land 1:1 with the submitted specs, keyed by `dataq_index`, or
    the run-service's positional zip stamps result content onto the wrong `check_id`.

    Pre-fix (verbatim GX order), `outcome.checks[2]` would be the *not-null-on-id*
    result, not the errored one — the live cross-wiring."""
    df = pd.DataFrame({"id": [1, 2, 3], "amt": [10, 20, 30]})
    runner = _runner_over(df, monkeypatch)
    submitted = [
        CheckSpec("expect_table_row_count_to_be_between", {"min_value": 1, "max_value": 10}),
        CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        CheckSpec(
            "expect_column_values_to_be_between", {"column": "does_not_exist", "min_value": 0}
        ),
    ]
    outcome = runner.run_checks(table="data/orders.csv", schema=None, checks=submitted)
    # Positional 1:1 with what was submitted — this is the contract run_service zips on.
    assert [c.expectation_type for c in outcome.checks] == [s.expectation_type for s in submitted]
    row_count, not_null_id, bad_col = outcome.checks
    assert row_count.errored is False and row_count.success is True
    assert not_null_id.errored is False and not_null_id.success is True
    assert not_null_id.expected_value == {"column": "id"}
    # the errored check keeps ITS identity: the missing-column error, not a sibling's
    assert bad_col.errored is True
    assert bad_col.error_message and "does_not_exist" in bad_col.error_message
    assert bad_col.expected_value == {"column": "does_not_exist", "min_value": 0}


def test_run_checks_duplicate_identical_expectations_stay_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#767 duplicate-safety: two checks with the *same* type+kwargs are ambiguous to
    match by (type, kwargs), but the positional `dataq_index` marker keeps them 1:1
    with submission order even when one errors and GX reorders."""
    df = pd.DataFrame({"id": [1, 2, 3]})
    runner = _runner_over(df, monkeypatch)
    outcome = runner.run_checks(
        table="data/orders.csv",
        schema=None,
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "nope"}),  # errors
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )
    assert len(outcome.checks) == 3
    assert outcome.checks[0].errored is False and outcome.checks[0].success is True
    assert outcome.checks[1].errored is True  # the middle (errored) one stays in the middle
    assert outcome.checks[2].errored is False and outcome.checks[2].success is True


# ── batch resolution (pure resolve_batch + mocked list orchestrator) ──


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


# ── adversarial-input contract for the GX runner ──

import pytest as _pytest  # noqa: E402

from backend.tests.support.adversarial import ADVERSARIAL_FRAMES  # noqa: E402


@_pytest.mark.parametrize(
    ("name", "frame"), ADVERSARIAL_FRAMES, ids=[n for n, _ in ADVERSARIAL_FRAMES]
)
def test_flatfile_runner_survives_adversarial_frame(
    name: str, frame: Any, monkeypatch: _pytest.MonkeyPatch
) -> None:
    # the runner must map a real GX run over hostile data to a SuiteOutcome, not crash.
    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: frame)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    outcome = runner.run_checks(
        table="f.parquet",
        schema=None,
        checks=[
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 0, "max_value": 10**9})
        ],
    )
    assert isinstance(outcome.success, bool)
    assert outcome.checks[0].expectation_type == "expect_table_row_count_to_be_between"


# ── live-seam wrappers: download_bytes / list_files (W8 coverage audit) ──────
# The boto3/azure SDK clients are the transport boundary; stubs stand in for
# them so the dispatch (s3 vs adls), FileRef mapping, and close() discipline
# are what's under test.

_S3_CONFIG = {"bucket": "raw", "region": "us-west-2", "access_key_id": "AKIAX"}
_ADLS_CONFIG = {"account_url": "https://acct.blob.core.windows.net", "container": "raw"}


# ── file_last_modified (live seam) ──


class _HeadS3Stub:
    """Minimal S3 client stub: head_object only."""

    def __init__(self, *, modified: datetime | None = _LANDED, error_code: str | None = None):
        self._modified = modified
        self._error_code = error_code
        self.calls: list[tuple[str, str]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 (boto3 kwargs)
        self.calls.append((Bucket, Key))
        if self._error_code is not None:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": self._error_code}}, "HeadObject")
        return {"LastModified": self._modified}


def test_file_last_modified_s3_heads_the_exact_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single metadata call, not a prefix listing: this runs on every scheduled
    monitor run, and `data/orders.csv` among dated siblings would otherwise drain
    every page each time — the unbounded-read-on-a-scheduled-path defect (#854).
    Heading the exact key is also exact by construction rather than by filtering."""
    stub = _HeadS3Stub()
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: stub)
    got = flatfile.file_last_modified(
        conn_type="s3", config=_S3_CONFIG, path="orders/a.csv", secret="s"
    )
    assert got == _LANDED
    assert stub.calls == [(_S3_CONFIG["bucket"], "orders/a.csv")]


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_file_last_modified_s3_missing_object_is_none(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """Absent → None, which the caller turns into a per-check error. A missing file
    is the incident this monitor exists to catch, so it must not read as fresh."""
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: _HeadS3Stub(error_code=code))
    assert (
        flatfile.file_last_modified(
            conn_type="s3", config=_S3_CONFIG, path="orders/gone.csv", secret="s"
        )
        is None
    )


def test_file_last_modified_s3_other_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    """This call is also the store-reachability probe, so an auth/permission failure
    must fail the whole run rather than be mistaken for a missing file."""
    monkeypatch.setattr(
        flatfile, "_s3_client", lambda cfg, secret: _HeadS3Stub(error_code="AccessDenied")
    )
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        flatfile.file_last_modified(
            conn_type="s3", config=_S3_CONFIG, path="orders/a.csv", secret="s"
        )


class _HeadBlobStub:
    """Minimal ADLS BlobServiceClient stub for get_blob_properties."""

    def __init__(self, *, modified: datetime | None = _LANDED, missing: bool = False):
        self._modified = modified
        self._missing = missing
        self.closed = False

    def get_blob_client(self, *, container: str, blob: str) -> Any:
        outer = self

        class _Blob:
            def get_blob_properties(self) -> Any:
                if outer._missing:
                    from azure.core.exceptions import ResourceNotFoundError

                    raise ResourceNotFoundError("nope")
                return SimpleNamespace(last_modified=outer._modified)

        return _Blob()

    def close(self) -> None:
        self.closed = True


def test_file_last_modified_adls_reads_blob_properties_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _HeadBlobStub()
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)
    got = flatfile.file_last_modified(
        conn_type="adls_gen2", config=_ADLS_CONFIG, path="orders/a.csv", secret="sas"
    )
    assert got == _LANDED
    assert stub.closed


def test_file_last_modified_adls_missing_blob_is_none_and_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _HeadBlobStub(missing=True)
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)
    got = flatfile.file_last_modified(
        conn_type="adls_gen2", config=_ADLS_CONFIG, path="orders/gone.csv", secret="sas"
    )
    assert got is None
    assert stub.closed  # the finally must run on the not-found path too


class _S3Stub:
    def __init__(self) -> None:
        self.pages = [
            {
                "Contents": [
                    {"Key": "orders/a.csv", "LastModified": datetime(2026, 7, 1, tzinfo=UTC)}
                ]
            },
            {"Contents": [{"Key": "orders/b.csv"}]},  # store reports no mtime
            {},  # page with no Contents at all
        ]

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 — boto3 kwargs
        assert (Bucket, Key) == ("raw", "orders/a.csv")
        return {"Body": io.BytesIO(b"col\n1\n")}

    def get_paginator(self, name: str) -> Any:
        assert name == "list_objects_v2"
        pages = self.pages
        return SimpleNamespace(paginate=lambda Bucket, Prefix: iter(pages))  # noqa: N803


class _BlobStub:
    """BlobServiceClient stand-in tracking the close() the finally owes."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get_blob_client(self, container: str, blob: str) -> Any:
        assert container == "raw"
        return SimpleNamespace(download_blob=lambda: SimpleNamespace(readall=lambda: b"bytes!"))

    def get_container_client(self, container: str) -> Any:
        assert container == "raw"
        blobs = [
            SimpleNamespace(name="orders/a.csv", last_modified=datetime(2026, 7, 1, tzinfo=UTC)),
            SimpleNamespace(name="orders/b.csv", last_modified=None),
        ]
        return SimpleNamespace(list_blobs=lambda name_starts_with: iter(blobs))


def test_download_bytes_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: _S3Stub())
    data = flatfile.download_bytes(
        conn_type="s3", config=_S3_CONFIG, path="orders/a.csv", secret="s"
    )
    assert data == b"col\n1\n"


def test_download_bytes_adls_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _BlobStub()
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)
    data = flatfile.download_bytes(
        conn_type="adls_gen2", config=_ADLS_CONFIG, path="orders/a.csv", secret="sas"
    )
    assert data == b"bytes!"
    assert stub.closed  # the finally must release the connection pool


def test_list_files_s3_maps_pages_and_missing_mtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: _S3Stub())
    refs = flatfile.list_files(conn_type="s3", config=_S3_CONFIG, prefix="orders/", secret="s")
    assert [r.path for r in refs] == ["orders/a.csv", "orders/b.csv"]
    assert refs[0].last_modified is not None and refs[1].last_modified is None


def test_list_files_adls_maps_blobs_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _BlobStub()
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)
    refs = flatfile.list_files(
        conn_type="adls_gen2", config=_ADLS_CONFIG, prefix="orders/", secret="sas"
    )
    assert [r.path for r in refs] == ["orders/a.csv", "orders/b.csv"]
    assert stub.closed


def test_s3_client_builds_with_failfast_timeouts() -> None:
    """Construction only — no network. Asserts the fail-fast timeout config."""
    from backend.app.datasources.s3 import S3Config

    client = flatfile._s3_client(S3Config.model_validate(_S3_CONFIG), "secret")
    assert client.meta.config.connect_timeout == flatfile._CONNECT_TIMEOUT
    assert client.meta.config.read_timeout == flatfile._READ_TIMEOUT
    assert client.meta.region_name == "us-west-2"


def test_blob_service_builds_against_account_url() -> None:
    from backend.app.datasources.adls import AdlsConfig

    client = flatfile._blob_service(AdlsConfig.model_validate(_ADLS_CONFIG), "sas-token")
    try:
        assert client.account_name == "acct"
    finally:
        client.close()
