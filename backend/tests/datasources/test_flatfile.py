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

from backend.app.core.config import get_settings
from backend.app.datasources import flatfile
from backend.app.datasources.base import SAMPLE_ROW_CAP, CheckSpec, SampleSpec
from backend.app.datasources.sampling import SamplingDrawError, ScanTooLargeError
from backend.tests.support.fake_secret_store import FakeSecretStore

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
    content: bytes = b"id,load_ts\n1,2026-06-29T00:00:00\n2,2026-06-28T00:00:00\n",
    reads: list[int] | None = None,
    ranges: list[tuple[int, int]] | None = None,
    size: int | None = None,
) -> None:
    """Stub every live seam over ONE canned object: the metadata call (arrival time
    + byte size), the whole-object download, and the range reads (#882/#942/#595).

    All of them serve the same bytes, so a test can assert *which* seam a code path
    chose — the difference between "counted the rows" and "counted the rows
    without pulling a 2 GB object" is invisible if the fake only exposes one way in.

    ``size`` overrides the reported byte length independently of ``content``, so a
    guardrail test can present a huge object without materialising one.
    """
    monkeypatch.setattr(
        flatfile,
        "file_stat",
        lambda **k: flatfile.FileStat(
            last_modified=mtime, size=len(content) if size is None else size
        ),
    )

    def _download(**_k: Any) -> bytes:
        if reads is not None:
            reads.append(1)
        return content

    def _read_range(*, start: int, length: int, **_k: Any) -> bytes:
        if ranges is not None:
            ranges.append((start, length))
        return content[start : start + length]

    monkeypatch.setattr(flatfile, "download_bytes", _download)
    monkeypatch.setattr(flatfile, "object_size", lambda **k: len(content))
    monkeypatch.setattr(flatfile, "read_range", _read_range)


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

    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(_LANDED, 128))
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
    # A distinctive sentinel, NOT a credential-shaped string: CLAUDE.md forbids a
    # credential in any tracked file even as a mock, and a realistic SAS here
    # would trip secret scanning for no extra assurance — the property under test
    # is "the raw exception text is not echoed", whatever that text happens to be.
    detail = "UPSTREAM-DETAIL-THAT-MUST-NOT-BE-ECHOED"

    def _boom(**_k: Any) -> bytes:
        raise RuntimeError(f"auth failed: {detail}")

    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(_LANDED, 128))
    monkeypatch.setattr(flatfile, "download_bytes", _boom)

    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("volume", min_rows=1, max_rows=2)]
    )
    message = out[0].error_message or ""
    assert out[0].errored is True
    assert detail not in message
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

    monkeypatch.setattr(flatfile, "file_stat", _boom)
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
    _patch_store(monkeypatch, content=b"id,load_ts\n1,\n2,\n")
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
    _patch_store(monkeypatch, content=buf.getvalue())

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
    _patch_store(monkeypatch, content=buf.getvalue())
    out = _monitor_runner().run_monitors(
        table="raw/orders.parquet", schema=None, monitors=[_spec("volume", min_rows=3, max_rows=5)]
    )
    assert out[0].errored is False and out[0].metric_value == 0.0


def test_arrow_backed_numeric_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The epoch-trap guard must survive the Arrow-dtype fix: widening the temporal
    check must not accidentally let `int64[pyarrow]` through."""
    buf = io.BytesIO()
    pd.DataFrame({"id": [1, 2], "order_no": [1001, 1002]}).to_parquet(buf)
    _patch_store(monkeypatch, content=buf.getvalue())
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
    _patch_store(monkeypatch, content=b"id,order_no\n1,1001\n2,1002\n")
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
        content=b"id,load_ts\n1,2026-Nov-30 00:00\n2,2026-Dec-01 00:00\n",
    )
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="load_ts")]
    )
    assert out[0].errored is False
    assert out[0].observed_value is not None
    assert out[0].observed_value["max_timestamp"].startswith("2026-12-01")


def test_a_mostly_unparseable_freshness_column_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`errors="coerce"` drops what it can't parse, so 9 junk values and 1 real date
    would yield a confident metric derived from that ONE row — which then bands as
    critically stale forever. Same silent-wrong-answer class as the epoch trap:
    "you pointed at the wrong column" must surface as an error, not a number.

    The junk is `pending`, deliberately NOT `n/a`: pandas maps `n/a` to NaN at
    parse time, so it is dropped as a NULL long before this guard and behaves
    correctly already. Only junk pandas keeps as a string reaches the coercion."""
    rows = b"".join(b"%d,pending\n" % i for i in range(9))
    _patch_store(monkeypatch, content=b"id,load_ts\n" + rows + b"9,2020-01-01\n")
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="load_ts")]
    )
    assert out[0].errored is True
    assert out[0].metric_value is None
    assert "mostly not timestamps" in (out[0].error_message or "")


def test_a_minority_of_junk_still_parses_like_nulls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The inverse guard: a few bad values behave like NULLs (matching `MAX` in
    SQL), so the threshold must not turn ordinary dirty data into an outage."""
    rows = b"".join(b"%d,2020-01-0%d\n" % (i, (i % 9) + 1) for i in range(9))
    _patch_store(monkeypatch, content=b"id,load_ts\n" + rows + b"9,n/a\n")
    out = _monitor_runner().run_monitors(
        table="raw/orders.csv", schema=None, monitors=[_spec("freshness", column="load_ts")]
    )
    assert out[0].errored is False, out[0].error_message


def test_an_unparseable_text_freshness_column_cannot_be_assessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch, content=b"id,load_ts\n1,not-a-date\n2,also-not\n")
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
        conn_type="s3",
        config={"bucket": "b"},
        secret_ref="ref",
        secret_store=FakeSecretStore(default="tok", raise_on_write=True),
    )
    assert isinstance(runner, flatfile.FlatFileCheckRunner)


def test_build_flatfile_runner_rejects_non_flatfile_type() -> None:
    with pytest.raises(ValueError, match="not a flat-file datasource"):
        flatfile.build_flatfile_runner(
            conn_type="snowflake",
            config={},
            secret_ref="ref",
            secret_store=FakeSecretStore(default="tok", raise_on_write=True),
        )


def test_build_flatfile_runner_requires_secret_ref() -> None:
    with pytest.raises(ValueError, match="requires secret_ref"):
        flatfile.build_flatfile_runner(
            conn_type="s3",
            config={},
            secret_ref=None,
            secret_store=FakeSecretStore(default="tok", raise_on_write=True),
        )


# ── FlatFileCheckRunner.run_checks (real GX on an in-memory DataFrame) ──


def _runner_over(df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: df)
    # `run_checks` probes the object's size before materialising it (#595), so the
    # metadata seam is stubbed here too — a small object, well under any cap.
    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(_LANDED, 4096))
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


def test_run_checks_index_list_is_capped_at_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1196: the pandas execution engine returns `unexpected_index_list` FULL under
    `result_format="COMPLETE"` (unlike `partial_unexpected_list`, capped at 20 on every
    engine). Real GX end to end over a frame with hundreds of failing rows: the captured
    sample must be bounded, while the aggregate counts still report the true totals.
    A unit test over `_extract_sample_failures` alone could not prove GX really hands us
    the untruncated list — that shape comes from the engine, not from our model."""
    failing = 500
    df = pd.DataFrame(
        {
            "order_number": [None] * failing,
            "customer_id": list(range(failing)),
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
    assert len(sample["unexpected_index_list"]) == SAMPLE_ROW_CAP
    assert len(sample["partial_unexpected_list"]) <= SAMPLE_ROW_CAP
    # the cap trims the sample, never the reported totals
    assert sample["unexpected_count"] == failing
    assert sample["unexpected_percent"] == 100.0


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


def _fake_listing(files: list[Any]) -> Any:
    """A stub for `iter_files` that is a real GENERATOR, like the seam it replaces.

    Deliberately not `iter(list)`: the live seam owns a client it releases in a
    `finally`, so callers may close it — and a stub that isn't closeable would
    hide a caller doing exactly that. A fixture must not be easier to satisfy than
    the thing it stands in for.
    """

    def _gen(**_kwargs: Any) -> Any:
        yield from files

    return _gen


def test_resolve_batch_file_lists_then_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatfile, "iter_files", _fake_listing(_BATCH_FILES))
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
    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(_LANDED, 4096))
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

    def __init__(
        self, *, modified: datetime | None = _LANDED, missing: bool = False, size: int = 4096
    ):
        self._modified = modified
        self._missing = missing
        self._size = size
        self.closed = False

    def get_blob_client(self, *, container: str, blob: str) -> Any:
        outer = self

        class _Blob:
            def get_blob_properties(self) -> Any:
                if outer._missing:
                    from azure.core.exceptions import ResourceNotFoundError

                    raise ResourceNotFoundError("nope")
                return SimpleNamespace(last_modified=outer._modified, size=outer._size)

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


def test_s3_client_resolves_a_compatible_endpoint() -> None:
    """A real boto3 client, so this proves boto3 *honoured* the kwargs (#1063).

    Asserting on a recorded kwarg would only prove we passed something; the
    question that matters is what the client ends up addressing, and that is
    botocore's answer, not ours. Construction only — no network.
    """
    from backend.app.datasources.s3 import S3Config

    client = flatfile._s3_client(
        S3Config.model_validate({**_S3_CONFIG, "endpoint_url": "http://minio:9000"}), "secret"
    )
    assert client.meta.endpoint_url == "http://minio:9000"
    assert client.meta.config.s3 == {"addressing_style": "path"}


def test_s3_client_without_an_endpoint_still_resolves_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression guard for every existing AWS connection (#1063).

    If `auto` ever started pinning an addressing style unconditionally, this is
    what would catch it — the AWS client must resolve the regional endpoint and
    leave `config.s3` unset.

    The ambient endpoint vars are cleared first: botocore >= 1.31 honours
    `AWS_ENDPOINT_URL[_S3]`, so on a developer machine that exports one (which is
    exactly what someone working against MinIO would do) this would otherwise fail
    for a reason that has nothing to do with the code under test.
    """
    from backend.app.datasources.s3 import S3Config

    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)

    client = flatfile._s3_client(S3Config.model_validate(_S3_CONFIG), "secret")
    assert client.meta.endpoint_url == "https://s3.us-west-2.amazonaws.com"
    assert client.meta.config.s3 is None


def test_blob_service_builds_against_account_url() -> None:
    from backend.app.datasources.adls import AdlsConfig

    client = flatfile._blob_service(AdlsConfig.model_validate(_ADLS_CONFIG), "sas-token")
    try:
        assert client.account_name == "acct"
    finally:
        client.close()


# ── bounded reads (#882 / #942) ──


def _parquet(rows: int = 3) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(
        {"id": range(rows), "load_ts": pd.date_range("2026-06-01", periods=rows)}
    ).to_parquet(buf, index=False)
    return buf.getvalue()


def test_a_volume_monitor_never_materialises_the_file_as_a_dataframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#942's headline: a nightly row count on a multi-GB Parquet used to pull the
    whole object into worker memory and fully materialise it as a pandas frame.

    Asserted by observing the SEAM taken, not the count — the count was always
    right, which is exactly why this went unnoticed. A correctness-only assertion
    passes with or without the fix.
    """
    reads: list[int] = []
    _patch_store(monkeypatch, content=_parquet(), reads=reads)

    out = _monitor_runner().run_monitors(
        table="raw/orders.parquet", schema=None, monitors=[_spec("volume", min_rows=3, max_rows=5)]
    )

    assert out[0].errored is False and out[0].metric_value == 0.0
    assert reads == [], "the whole object was downloaded to produce a row count"


def test_a_parquet_row_count_reads_only_a_fraction_of_the_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parquet states its own row count in the footer, so the count should cost a
    couple of small range GETs rather than the object — whatever its size."""
    content = _parquet(rows=20_000)
    ranges: list[tuple[int, int]] = []
    _patch_store(monkeypatch, content=content, ranges=ranges)

    assert (
        flatfile.row_count(conn_type="s3", config={}, path="raw/orders.parquet", secret="s")
        == 20_000
    )
    assert ranges, "no range read was issued at all"
    assert sum(length for _, length in ranges) < len(content)


def test_a_csv_row_count_is_not_a_newline_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CSV has no footer, so rows must be scanned — but counting newlines would
    be a *plausible wrong answer*: a quoted field may legally contain one. Trading
    an exact count for a believable one is the trade this codebase keeps paying for.
    """
    content = b'a,b\n1,"line one\nline two"\n2,"x"\n'
    _patch_store(monkeypatch, content=content)

    assert flatfile.row_count(conn_type="s3", config={}, path="raw/x.csv", secret="s") == 2


def test_volume_and_column_freshness_together_read_the_object_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#942 AC 2. Column freshness genuinely needs the frame; volume must then come
    off that frame rather than issuing a second, independent read of the same file."""
    reads: list[int] = []
    ranges: list[tuple[int, int]] = []
    _patch_store(monkeypatch, content=_parquet(), reads=reads, ranges=ranges)

    out = _monitor_runner().run_monitors(
        table="raw/orders.parquet",
        schema=None,
        monitors=[_spec("volume", min_rows=1, max_rows=10), _spec("freshness", column="load_ts")],
    )

    assert [o.errored for o in out] == [False, False]
    assert len(reads) == 1
    assert ranges == [], "a second read was issued alongside the frame the run already had"


def test_a_failing_row_count_is_attempted_once_not_once_per_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count gets the same attempt-memo the frame has. Without it, three volume
    monitors on one target re-scan a failing object three times, and a transient
    failure yields inconsistent outcomes inside a single run."""
    attempts: list[int] = []
    _patch_store(monkeypatch, content=_parquet())

    def _boom(**_k: Any) -> int:
        attempts.append(1)
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(flatfile, "row_count", _boom)
    out = _monitor_runner().run_monitors(
        table="raw/orders.parquet",
        schema=None,
        monitors=[_spec("volume", min_rows=1, max_rows=2) for _ in range(3)],
    )

    assert [o.errored for o in out] == [True, True, True]
    assert len(attempts) == 1


def test_a_csv_head_read_drops_a_row_the_range_cut_in_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A byte range almost always ends mid-row, and a half-row parses as a COMPLETE
    row with empty trailing fields — silently changing an inferred dtype. So the
    trailing partial line is discarded before parsing."""
    content = b"id,name\n" + b"".join(b"%d,abcdefghij\n" % i for i in range(200_000))
    _patch_store(monkeypatch, content=content)
    monkeypatch.setattr(flatfile, "_CSV_HEAD_BYTES", 5_000)

    df = flatfile.read_csv_head(conn_type="s3", config={}, path="x.csv", secret="s", rows=100_000)

    assert list(df.columns) == ["id", "name"]
    # Every row that survived is whole — a truncated tail would show as a NaN name.
    assert df["name"].notna().all()
    assert len(df) < 200_000  # it really was a bounded read


def test_a_range_reader_coalesces_small_sequential_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading a footer apart in small steps must not become one request per step —
    swapping one download for thousands of round trips is not an improvement."""
    _patch_store(monkeypatch, content=b"x" * 100_000)
    reader = flatfile.RangeReader(conn_type="s3", config={}, path="x.parquet", secret="s")

    for _ in range(50):
        reader.read(8)

    assert reader.requests == 1


# ── the range seam AT the driver boundary (#882) ──
#
# Every other IO primitive in this module has a test that stubs one level BELOW
# it (`_s3_client` / `_blob_service`) and asserts the real call shape. The range
# seam needs the same: every test above stubs `read_range`/`object_size`
# themselves, so an off-by-one in the `Range` header — or the wrong metadata
# field — would pass all of them and only surface against a live store. That is
# the failure mode #953 cost us a whole feature to learn.


class _RangeS3Stub:
    """boto3 stand-in recording the exact Range header and serving it honestly."""

    def __init__(self, content: bytes = b"0123456789") -> None:
        self.content = content
        self.ranges: list[str] = []

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 — boto3 kwargs
        assert (Bucket, Key) == ("raw", "orders/a.csv")
        return {"ContentLength": len(self.content), "LastModified": _LANDED}

    def get_object(self, Bucket: str, Key: str, Range: str) -> dict[str, Any]:  # noqa: N803
        self.ranges.append(Range)
        first, _, last = Range.removeprefix("bytes=").partition("-")
        # Inclusive end, exactly as RFC 7233 (and S3) read it.
        return {"Body": io.BytesIO(self.content[int(first) : int(last) + 1])}


def test_read_range_s3_asks_for_an_inclusive_byte_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """`bytes=0-3` is FOUR bytes, not three. An off-by-one here silently drops a
    byte off every window — which, on a Parquet footer, is a corrupt read."""
    stub = _RangeS3Stub()
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: stub)

    got = flatfile.read_range(
        conn_type="s3", config=_S3_CONFIG, path="orders/a.csv", secret="s", start=2, length=4
    )

    assert got == b"2345"
    assert stub.ranges == ["bytes=2-5"]


def test_object_size_s3_reads_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: _RangeS3Stub(b"abcdefg"))
    assert (
        flatfile.object_size(conn_type="s3", config=_S3_CONFIG, path="orders/a.csv", secret="s")
        == 7
    )


class _RangeBlobStub:
    """ADLS stand-in recording (offset, length) and tracking the owed close()."""

    def __init__(self, content: bytes = b"0123456789") -> None:
        self.content = content
        self.calls: list[tuple[int | None, int | None]] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def get_blob_client(self, *, container: str, blob: str) -> Any:
        assert container == "raw"
        outer = self

        def _download(*, offset: int | None = None, length: int | None = None) -> Any:
            outer.calls.append((offset, length))
            start = offset or 0
            end = start + length if length is not None else len(outer.content)
            return SimpleNamespace(readall=lambda: outer.content[start:end])

        return SimpleNamespace(
            download_blob=_download,
            get_blob_properties=lambda: SimpleNamespace(
                size=len(outer.content), last_modified=_LANDED
            ),
        )


def test_read_range_adls_uses_offset_length_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Azure takes offset+length, not an inclusive end — the one place the two
    stores' range dialects differ, and the client still owes a close()."""
    stub = _RangeBlobStub()
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)

    got = flatfile.read_range(
        conn_type="adls_gen2",
        config=_ADLS_CONFIG,
        path="orders/a.csv",
        secret="sas",
        start=2,
        length=4,
    )

    assert got == b"2345"
    assert stub.calls == [(2, 4)]
    assert stub.closed


def test_object_size_adls_reads_size_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _RangeBlobStub(b"abcdefg")
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)
    assert (
        flatfile.object_size(
            conn_type="adls_gen2", config=_ADLS_CONFIG, path="orders/a.csv", secret="sas"
        )
        == 7
    )
    assert stub.closed  # the finally must release the connection pool


def test_read_range_of_nothing_asks_the_store_for_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-length range is a no-op, not `bytes=5-4` — which S3 rejects outright."""
    stub = _RangeS3Stub()
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: stub)
    assert (
        flatfile.read_range(
            conn_type="s3", config=_S3_CONFIG, path="orders/a.csv", secret="s", start=5, length=0
        )
        == b""
    )
    assert stub.ranges == []


# ── degenerate objects through the new seam ──


def test_a_csv_with_only_a_header_counts_zero_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero rows is a real answer — and the one a volume monitor most needs to get
    right, since "the producer wrote a header and no data" is an incident."""
    _patch_store(monkeypatch, content=b"id,name\n")
    assert flatfile.row_count(conn_type="s3", config={}, path="x.csv", secret="s") == 0


def test_a_head_read_of_a_file_smaller_than_the_window_keeps_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trailing-partial-line trim must not fire when the range covered the whole
    object — that would silently discard a genuinely-complete last row."""
    _patch_store(monkeypatch, content=b"id,name\n1,a\n2,b\n3,c")  # no trailing newline
    df = flatfile.read_csv_head(conn_type="s3", config={}, path="x.csv", secret="s", rows=100)
    assert len(df) == 3


def test_a_head_read_of_a_file_exactly_the_window_size_keeps_its_last_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary the first cut of this got wrong: an object whose size EQUALS the
    window was read completely, but "I got a full window" was taken as evidence of
    truncation, so its real last row was dropped."""
    content = b"id,name\n1,a\n2,bcd"
    monkeypatch.setattr(flatfile, "_CSV_HEAD_BYTES", len(content))
    _patch_store(monkeypatch, content=content)

    df = flatfile.read_csv_head(conn_type="s3", config={}, path="x.csv", secret="s", rows=100)

    assert len(df) == 2 and df["name"].tolist() == ["a", "bcd"]


def test_a_malformed_freshness_column_errors_only_its_own_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The isolation contract `run_monitor_specs` exists to keep. Deciding up front
    whether the frame is needed must not evaluate a bad column config OUTSIDE the
    per-monitor guard: that raises out of `run_monitors`, fails the whole run, and
    persists nothing — so one hand-edited config would take down every sibling
    check on the target."""
    _patch_store(monkeypatch)

    out = _monitor_runner().run_monitors(
        table="raw/orders.csv",
        schema=None,
        monitors=[
            _spec("volume", min_rows=1, max_rows=10),
            _spec("freshness", column="not a column!"),
        ],
    )

    assert [o.errored for o in out] == [False, True]
    assert out[0].metric_value == 0.0  # the valid sibling still produced a result


# ── bounded batch listing (#943) ──


def test_resolve_batch_consumes_a_one_shot_iterator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listing is streamed, so resolution gets ONE pass over it. A second pass
    would silently see an exhausted iterator and pick from nothing."""
    files = iter(_BATCH_FILES)
    assert flatfile.resolve_batch(files, pattern=_PATTERN) == "data/orders_2026-06-03.csv"


def test_resolve_batch_holds_only_the_running_best_not_the_listing() -> None:
    """#943's actual defect: every matching object was retained in a list, on a path
    that runs per scheduled run, for a prefix whose history only grows.

    Asserted by RETENTION — weakrefs to each yielded entry, checked after the fold.
    A correctness assertion passes either way (the answer was always right), and a
    "did it stream?" assertion is easy to write so loosely that it measures the
    test's own bookkeeping instead of the code's.
    """
    import gc
    import weakref

    seen: list[Any] = []
    peak: list[int] = []

    def _huge() -> Any:
        for index, year in enumerate(range(2000, 4000)):
            # Measure BEFORE creating this entry, so our own loop variable isn't
            # counted. Measuring after the fold returns would prove nothing — every
            # local dies at return, so even a full materialisation looks clean.
            if index and index % 500 == 0:
                gc.collect()
                peak.append(sum(1 for ref in seen if ref() is not None))
            entry = flatfile.FileRef(f"data/orders_{year}-01-01.csv", _dt(1))
            seen.append(weakref.ref(entry))
            yield entry

    got = flatfile.resolve_batch(_huge(), pattern=_PATTERN)

    assert got == "data/orders_3999-01-01.csv"
    # The running bests, plus the entry the consumer is holding at the checkpoint.
    assert peak and max(peak) <= 4, f"peak retention {max(peak)} of {len(seen)} listed entries"


@pytest.mark.parametrize(
    ("files", "kwargs"),
    [
        (_BATCH_FILES, {"pattern": _PATTERN}),
        (_BATCH_FILES, {"pattern": _PATTERN, "strategy": "specific", "batch": "2026-06-02"}),
        # No capture group → the mtime fallback, including its path tie-break.
        (
            [flatfile.FileRef("a/load.csv", _dt(5)), flatfile.FileRef("b/load.csv", _dt(5))],
            {"pattern": r"load\.csv"},
        ),
        # An optional group that didn't participate on some files.
        (
            [
                flatfile.FileRef("orders_.csv", _dt(9)),
                flatfile.FileRef("orders_2026-06-01.csv", _dt(1)),
            ],
            {"pattern": r"orders_(\d{4}-\d{2}-\d{2})?\.csv"},
        ),
        # Two files sharing the greatest batch key — first-seen must win, as `max` did.
        (
            [
                flatfile.FileRef("data/orders_2026-06-03.csv", _dt(1)),
                flatfile.FileRef("x/data/orders_2026-06-03.csv", _dt(9)),
            ],
            {"pattern": _PATTERN},
        ),
    ],
)
def test_the_streaming_fold_picks_what_the_old_max_did(files: Any, kwargs: Any) -> None:
    """Equivalence guard for the rewrite. `max()` returns the FIRST maximal element;
    a fold using `>` keeps the first too — but only if written that way, and a
    `>=` would silently flip every tie. Pinned against the original formulation."""
    import re as _re

    compiled = _re.compile(kwargs["pattern"])
    matches = [(f, m) for f in files if (m := compiled.search(f.path))]
    if kwargs.get("strategy") == "specific":
        hits = [f for f, m in matches if m.groups() and m.group(1) == kwargs["batch"]]
        expected = flatfile._most_recent(hits)
    else:
        keyed = [(f, m.group(1)) for f, m in matches if m.groups() and m.group(1) is not None]
        expected = (
            max(keyed, key=lambda fk: fk[1])[0].path
            if keyed
            else flatfile._most_recent([f for f, _ in matches])
        )

    assert flatfile.resolve_batch(iter(files), **kwargs) == expected


def test_a_runaway_prefix_is_refused_rather_than_answered_from_a_partial_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both stores list ASCENDING, so the newest key — the one `latest` wants — is
    exactly what a truncated scan misses. Answering from a partial listing would
    return a confidently wrong, older batch; refusing is the honest outcome."""
    monkeypatch.setattr(flatfile, "_BATCH_LISTING_MAX", 3)
    monkeypatch.setattr(
        flatfile,
        "iter_files",
        _fake_listing([flatfile.FileRef(f"data/orders_2026-06-{i:02d}.csv") for i in range(1, 10)]),
    )

    with pytest.raises(flatfile.BatchListingTooLargeError, match="narrow the prefix"):
        flatfile.resolve_batch_file(
            conn_type="s3", config={}, secret="s", prefix="data/", pattern=_PATTERN
        )


def test_a_runaway_prefix_does_not_read_as_a_missing_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`BatchNotFoundError` means "the data hasn't landed" and SKIPS the run (#122).
    A refused listing is a failure, not an absence — conflating them would turn a
    broken target into a run that quietly reports success every night."""
    assert not issubclass(flatfile.BatchListingTooLargeError, flatfile.BatchNotFoundError)


def test_iter_files_releases_the_adls_client_when_abandoned_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generator holds the client open across yields, so a caller that stops
    early must still return it to the pool — otherwise a refused listing leaks a
    connection on every scheduled run."""
    stub = _BlobStub()
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)

    stream = flatfile.iter_files(
        conn_type="adls_gen2", config=_ADLS_CONFIG, prefix="orders/", secret="sas"
    )
    next(stream)  # start it, then walk away
    assert stub.closed is False
    stream.close()

    assert stub.closed is True


def test_resolve_batch_file_closes_the_listing_when_resolution_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same release, through the real call path: raising mid-listing must not
    skip the client's close().

    This pins the OUTCOME, not the mechanism — and cannot distinguish them: on
    CPython the abandoned generator is finalised by refcounting the moment
    `resolve_batch` raises, so the client is released with or without the explicit
    `closing()`. The explicit form is kept anyway rather than depending on a
    refcounting detail no other implementation guarantees; verified by mutation
    that this test does not prove it, so nobody later reads it as if it did.
    """
    stub = _BlobStub()
    monkeypatch.setattr(flatfile, "_blob_service", lambda acfg, secret: stub)
    monkeypatch.setattr(flatfile, "_BATCH_LISTING_MAX", 1)

    with pytest.raises(flatfile.BatchListingTooLargeError):
        flatfile.resolve_batch_file(
            conn_type="adls_gen2",
            config=_ADLS_CONFIG,
            secret="sas",
            prefix="orders/",
            pattern=r"orders/(\w)\.csv",
        )

    assert stub.closed is True


# ── sampling + the scan guardrail (#595) ─────────────────────────────────────


def _set_cap(monkeypatch: pytest.MonkeyPatch, name: str, value: int) -> None:
    """Point a scan cap at ``value`` for this test (cached Settings rebuild)."""
    monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


def _csv_bytes(rows: int) -> bytes:
    header = b"id,name\n"
    body = b"".join(f"{i},n{i}\n".encode() for i in range(rows))
    return header + body


def _sampled(
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: bytes,
    path: str,
    strategy: str,
    rows: int,
    seed: int | None = None,
) -> tuple[Any, dict[str, Any]]:
    _patch_store(monkeypatch, content=content)
    return flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path=path,
        secret="s",
        sample=SampleSpec(strategy=strategy, rows=rows, seed=seed),
    )


@pytest.mark.parametrize("path", ["raw/big.csv", "raw/big.parquet"])
def test_a_head_sample_returns_exactly_the_first_n_rows(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _csv_bytes(500) if path.endswith(".csv") else _parquet(rows=500)
    frame, record = _sampled(monkeypatch, content=content, path=path, strategy="head", rows=50)
    assert len(frame) == 50
    assert record["sampled"] is True
    assert record["rows"] == 50
    # A head sample deliberately does NOT pay for a count, so the population size
    # is honestly unknown rather than guessed.
    assert record["total_rows"] is None


def test_a_head_sample_keeps_the_first_rows_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, _ = _sampled(
        monkeypatch,
        content=_csv_bytes(100),
        path="raw/big.csv",
        strategy="head",
        rows=5,
    )
    assert list(frame["id"]) == [0, 1, 2, 3, 4]


def test_a_head_sample_larger_than_the_file_reports_a_complete_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The off-by-one that matters: reading `rows + 1` is what distinguishes "the
    file has exactly N rows" from "the file has more". Getting this wrong puts a
    "sampled" caveat on every small target, which trains users to ignore it."""
    frame, record = _sampled(
        monkeypatch,
        content=_csv_bytes(12),
        path="raw/small.csv",
        strategy="head",
        rows=1000,
    )
    assert len(frame) == 12
    assert record["sampled"] is False
    assert record["total_rows"] == 12


def test_a_head_sample_of_exactly_the_file_length_is_not_a_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary the probe row exists for: 12 rows requested, 12 rows in the
    file. Nothing was left behind, so the verdict is complete."""
    frame, record = _sampled(
        monkeypatch,
        content=_csv_bytes(12),
        path="raw/small.csv",
        strategy="head",
        rows=12,
    )
    assert len(frame) == 12
    assert record["sampled"] is False and record["total_rows"] == 12


def test_a_head_sample_one_row_short_of_the_file_is_a_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, record = _sampled(
        monkeypatch,
        content=_csv_bytes(12),
        path="raw/small.csv",
        strategy="head",
        rows=11,
    )
    assert len(frame) == 11
    assert record["sampled"] is True and record["total_rows"] is None


@pytest.mark.parametrize("path", ["raw/big.csv", "raw/big.parquet"])
def test_a_random_sample_draws_n_distinct_rows_from_across_the_file(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = _csv_bytes(500) if path.endswith(".csv") else _parquet(rows=500)
    frame, record = _sampled(
        monkeypatch, content=content, path=path, strategy="random", rows=40, seed=11
    )
    ids = list(frame["id"])
    assert len(ids) == 40
    assert len(set(ids)) == 40
    assert record == {
        "strategy": "random",
        "requested_rows": 40,
        "rows": 40,
        "total_rows": 500,
        "sampled": True,
        "seed": 11,
    }
    # Not merely "40 rows": a head sample would give 40 too. What makes it random
    # is that the draw reaches past the head of the file.
    assert max(ids) > 100


def test_a_random_sample_is_reproducible_under_a_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _ = _sampled(
        monkeypatch,
        content=_csv_bytes(300),
        path="raw/big.csv",
        strategy="random",
        rows=20,
        seed=5,
    )
    second, _ = _sampled(
        monkeypatch,
        content=_csv_bytes(300),
        path="raw/big.csv",
        strategy="random",
        rows=20,
        seed=5,
    )
    assert list(first["id"]) == list(second["id"])


def test_a_random_sample_bigger_than_the_file_reads_it_all_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame, record = _sampled(
        monkeypatch,
        content=_csv_bytes(9),
        path="raw/small.csv",
        strategy="random",
        rows=500,
        seed=1,
    )
    assert len(frame) == 9
    assert record["sampled"] is False and record["total_rows"] == 9


def test_sampling_an_empty_file_yields_a_typed_empty_frame_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A header-only CSV is a real landing-zone state. The frame must keep its
    COLUMNS, or every check errors with "column not found" instead of failing
    honestly against zero rows."""
    frame, record = _sampled(
        monkeypatch,
        content=b"id,name\n",
        path="raw/empty.csv",
        strategy="head",
        rows=10,
    )
    assert list(frame.columns) == ["id", "name"]
    assert len(frame) == 0
    assert record["sampled"] is False


def test_sampling_an_unknown_file_format_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch)
    with pytest.raises(ValueError, match="unsupported flat-file format"):
        flatfile.read_sampled_dataframe(
            conn_type="s3",
            config={},
            path="raw/orders.txt",
            secret="s",
            sample=SampleSpec(strategy="head", rows=10),
        )


def test_a_sampled_head_read_never_pulls_the_whole_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property the whole feature rests on. A sampled read that still
    downloads the object would be the same OOM wearing a caveat — so this asserts
    the SEAM (no whole-object download, bounded range bytes), not the row count,
    which is right either way.

    The object is deliberately built much larger than the head window, so "it read
    less than the file" is a real property rather than an accident of a fixture
    that fits in one range.
    """
    content = _csv_bytes(900_000)
    assert len(content) > 4 * flatfile._CSV_HEAD_BYTES, "fixture must dwarf the head window"
    reads: list[int] = []
    ranges: list[tuple[int, int]] = []
    _patch_store(monkeypatch, content=content, reads=reads, ranges=ranges)

    frame, _ = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/big.csv",
        secret="s",
        sample=SampleSpec(strategy="head", rows=100),
    )

    assert len(frame) == 100
    assert reads == [], "the whole object was downloaded for a 100-row sample"
    # 100 rows live in the first kilobytes, so ONE head window covers them. The
    # bound is the window, not the object — which is what makes this survive 2 GB.
    assert sum(length for _, length in ranges) <= flatfile._CSV_HEAD_BYTES


def test_a_sampled_run_stamps_every_result_with_what_it_saw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The acceptance criterion in one test: a check that passed on a sample must
    say so, on the result row rather than only in the run log."""
    _patch_store(monkeypatch, content=_csv_bytes(500))
    runner = flatfile.FlatFileCheckRunner(
        conn_type="s3",
        config={},
        secret="x",
        sampling=SampleSpec(strategy="head", rows=10),
    )
    outcome = runner.run_checks(
        table="raw/big.csv",
        schema=None,
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "name"}),
        ],
    )
    assert [c.success for c in outcome.checks] == [True, True]
    assert all(c.sampling is not None and c.sampling["sampled"] is True for c in outcome.checks)


def test_an_unsampled_run_records_no_sampling_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` — not a `sampled: false` record — so the read API's "complete read"
    case is the same shape for a suite that never opted in and for every result
    written before the feature existed."""
    _patch_store(monkeypatch, content=_csv_bytes(20))
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    outcome = runner.run_checks(
        table="raw/small.csv",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].sampling is None


def test_an_oversized_file_is_refused_before_it_is_downloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#755's failure mode, inverted: instead of SIGKILLing the child and leaving
    the run `running` for an hour with no memory-attributed reason, the run ends
    with a sentence naming the knob."""
    reads: list[int] = []
    _patch_store(monkeypatch, content=_csv_bytes(10), reads=reads, size=999_000_000)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")

    with pytest.raises(ScanTooLargeError, match="RUN_MAX_SCAN_BYTES"):
        runner.run_checks(
            table="raw/huge.csv",
            schema=None,
            checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
        )
    assert reads == [], "the guardrail must refuse BEFORE the download, not after"


def test_a_sampled_run_is_allowed_past_the_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sampling is the sanctioned way past the size probe: the read is bounded by
    the sample, so the object's own size stops being a memory fact. If the cap
    still applied, the feature would be unreachable exactly where it is needed."""
    _patch_store(monkeypatch, content=_csv_bytes(500), size=999_000_000)
    runner = flatfile.FlatFileCheckRunner(
        conn_type="s3",
        config={},
        secret="x",
        sampling=SampleSpec(strategy="head", rows=25),
    )
    outcome = runner.run_checks(
        table="raw/huge.csv",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].sampling is not None
    assert outcome.checks[0].sampling["rows"] == 25


def test_a_sample_over_the_row_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_store(monkeypatch, content=_csv_bytes(10))
    runner = flatfile.FlatFileCheckRunner(
        conn_type="s3",
        config={},
        secret="x",
        sampling=SampleSpec(strategy="head", rows=9_000_000),
    )
    with pytest.raises(ScanTooLargeError, match="sample of 9,000,000"):
        runner.run_checks(
            table="raw/x.csv",
            schema=None,
            checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
        )


def test_a_disabled_byte_cap_lets_a_huge_file_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The off-switch has to actually be off — a cap of 0 read as "zero bytes
    allowed" would refuse every run on the operator's own instruction to stop
    checking.

    It must also stop *probing*: an operator who disables the guardrail should not
    keep paying a metadata round trip per run for a number nobody reads. Asserted
    on the seam, because "the run succeeded" is true either way."""
    _set_cap(monkeypatch, "RUN_MAX_SCAN_BYTES", 0)
    _patch_store(monkeypatch, content=_csv_bytes(10), size=999_000_000)
    stats: list[int] = []

    def _stat(**_k: Any) -> Any:
        stats.append(1)
        return flatfile.FileStat(_LANDED, 999_000_000)

    monkeypatch.setattr(flatfile, "file_stat", _stat)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    outcome = runner.run_checks(
        table="raw/huge.csv",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].success is True
    assert stats == [], "a disabled cap must skip the probe, not just ignore its answer"


def test_a_store_that_reports_no_size_is_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing content-length is not evidence of a large file. Refusing on an
    unknown size would break every run against a store that omits the header — a
    guardrail failing closed on ignorance, which is worse than the risk it guards."""
    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(_LANDED, None))
    monkeypatch.setattr(flatfile, "download_bytes", lambda **k: _csv_bytes(5))
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    outcome = runner.run_checks(
        table="raw/x.csv",
        schema=None,
        checks=[CheckSpec("expect_column_values_to_not_be_null", {"column": "id"})],
    )
    assert outcome.checks[0].success is True


def test_a_column_freshness_monitor_on_an_oversized_file_errors_only_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guardrail covers the monitor path too — a column-freshness monitor
    pulls the same whole object a check run does. It must NOT sample instead: a
    MAX over a sample is a *smaller* maximum, so a sampled freshness monitor
    reports healthy data as critically stale. Arrival-time freshness never touches
    the frame, so it stays available beside the refused one."""
    content = b"id,load_ts\n1,2026-06-29T00:00:00\n"
    _patch_store(monkeypatch, content=content, size=999_000_000)
    runner = flatfile.FlatFileCheckRunner(
        conn_type="s3",
        config={},
        secret="x",
        sampling=SampleSpec(strategy="head", rows=10),
    )
    out = runner.run_monitors(
        table="raw/huge.csv",
        schema=None,
        monitors=[_spec("freshness", column="load_ts"), _spec("freshness")],
    )
    assert out[0].errored is True
    assert "RUN_MAX_SCAN_BYTES" in (out[0].error_message or "")
    assert out[1].errored is False, "arrival-time freshness needs no data read"


def test_the_refusal_message_survives_the_monitor_loops_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_monitor_specs` classifies unmarked exceptions into a generic sentence,
    because a driver message can carry a credential (#828/#900). This one is
    DataQ-authored, so it is `SafeMonitorError`-marked and persists verbatim —
    otherwise the most actionable error in the feature would read "the run failed
    to execute"."""
    _patch_store(monkeypatch, content=b"id,load_ts\n1,2026-06-29T00:00:00\n", size=999_000_000)
    out = _monitor_runner().run_monitors(
        table="raw/huge.csv",
        schema=None,
        monitors=[_spec("freshness", column="load_ts")],
    )
    assert "over the scan cap" in (out[0].error_message or "")


def test_the_monitor_guardrail_costs_no_extra_metadata_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The establishment probe already asks the store for this object's metadata,
    and it now returns the size alongside the arrival time. A second HEAD per run
    on a per-schedule path is exactly the overhead #854 exists to remove."""
    stats: list[int] = []

    def _stat(**_k: Any) -> Any:
        stats.append(1)
        return flatfile.FileStat(_LANDED, 4096)

    monkeypatch.setattr(flatfile, "file_stat", _stat)
    monkeypatch.setattr(
        flatfile, "download_bytes", lambda **k: b"id,load_ts\n1,2026-06-29T00:00:00\n"
    )
    _monitor_runner().run_monitors(
        table="raw/orders.csv",
        schema=None,
        monitors=[_spec("freshness", column="load_ts"), _spec("freshness")],
    )
    assert stats == [1]


def test_a_csv_head_window_grows_until_it_holds_the_requested_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A head sample bigger than the starting window must not come back short.
    The failure this guards is silent: a truncated read yields FEWER rows, every
    check still evaluates, and the run reports a clean verdict on a fraction of
    the data it claimed."""
    content = _csv_bytes(400_000)
    ranges: list[tuple[int, int]] = []
    _patch_store(monkeypatch, content=content, ranges=ranges)

    frame, record = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/big.csv",
        secret="s",
        # Comfortably more rows than one `_CSV_HEAD_BYTES` window holds.
        sample=SampleSpec(strategy="head", rows=200_000),
    )

    assert len(frame) == 200_000
    assert record["sampled"] is True
    assert len(ranges) > 1, "the window never grew — this test would pass trivially"


def test_a_csv_head_sample_keeps_the_unsampled_readers_dtypes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turning sampling on must not change a check's verdict through a DTYPE
    change. CSV head goes through `read_csv_bytes` exactly like a full read, so
    the two frames must type identically over the same rows."""
    content = _csv_bytes(50)
    _patch_store(monkeypatch, content=content)
    sampled, _ = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/x.csv",
        secret="s",
        sample=SampleSpec(strategy="head", rows=10),
    )
    full = flatfile.read_dataframe(conn_type="s3", config={}, path="raw/x.csv", secret="s")
    assert list(sampled.dtypes) == list(full.dtypes)


def test_a_csv_head_sample_respects_a_non_comma_delimiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#476's defect, in the new reader: a `;`-delimited file parsed with the
    pandas default comma yields ONE column named after the whole header — and
    every column check then errors, or worse, a row-count check passes on garbage.
    The sampled path shares the sniffing seam precisely so it cannot regress."""
    content = b"id;name\n" + b"".join(f"{i};n{i}\n".encode() for i in range(50))
    _patch_store(monkeypatch, content=content)
    frame, _ = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/semi.csv",
        secret="s",
        sample=SampleSpec(strategy="head", rows=10),
    )
    assert list(frame.columns) == ["id", "name"]


def test_a_csv_head_sample_never_splits_a_row_across_the_window_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A byte range almost always ends mid-row, and a half-row parses as a
    COMPLETE row with empty trailing fields — silently changing an inferred dtype
    (a truncated `12345` becomes `123`). The last partial line is dropped for the
    same reason `read_csv_head` drops it."""
    # Rows are wide enough that the first window is guaranteed to cut one.
    wide = b"id,payload\n" + b"".join(f"{i},{'x' * 500}\n".encode() for i in range(10_000))
    _patch_store(monkeypatch, content=wide)
    frame, _ = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/wide.csv",
        secret="s",
        sample=SampleSpec(strategy="head", rows=100),
    )
    assert len(frame) == 100
    # Every retained row must be whole: same id sequence, same payload length.
    assert list(frame["id"]) == list(range(100))
    assert set(frame["payload"].str.len()) == {500}


# ── /code-review follow-ups: C4, C6, J1 (#595) ───────────────────────────────


def _quoted_csv(rows: int) -> bytes:
    """A CSV whose every row carries an embedded newline inside a quoted field.

    The layout C4 is about: legal RFC 4180, handled perfectly by the full read,
    and a byte-range cut at the last raw `\\n` lands *inside* the quotes.
    """
    body = b"".join(f'{i},"line one\nline two {i}"\n'.encode() for i in range(rows))
    return b"id,note\n" + body


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"a,b\n1,2\n3,4\n", b"a,b\n1,2\n3,4"),  # trailing newline is a boundary
        (b"a,b\n1,2\n3,", b"a,b\n1,2"),  # partial final row dropped
        (b'a,b\n1,"x\ny"\n2,', b'a,b\n1,"x\ny"'),  # newline INSIDE quotes is not a cut
        (b'a,b\n1,"x\ny', b"a,b"),  # cut lands mid-quote: fall back further
        (b"one enormous line with no newline", b"one enormous line with no newline"),
        (
            b'a,b\n1,"he said ""hi""\nnext"\n2,',
            b'a,b\n1,"he said ""hi""\nnext"',
        ),  # "" escape
    ],
)
def test_the_row_boundary_is_quote_aware(raw: bytes, expected: bytes) -> None:
    """C4. A newline is NOT a row boundary — a quoted field may contain one, and
    cutting there leaves an unterminated quote that pandas rejects outright with
    "EOF inside string". `csv_row_count` already refuses to equate the two; this
    is the same rule for the head path, including the ``""`` escape (which adds
    two quotes, so parity still answers "am I inside a field")."""
    assert flatfile.trim_to_row_boundary(raw) == expected


def test_a_head_sample_of_a_quoted_csv_parses_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C4 end to end. Before the quote-aware cut this raised
    `pandas.errors.ParserError: EOF inside string` — on a file the unsampled read
    handles perfectly, intermittently, decided by nothing but where the growing
    window happened to land."""
    content = _quoted_csv(60_000)
    _patch_store(monkeypatch, content=content)

    frame, record = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/quoted.csv",
        secret="s",
        sample=SampleSpec(strategy="head", rows=50),
    )

    assert len(frame) == 50
    assert record["sampled"] is True
    # Every retained row must be WHOLE — the embedded newline is data, not a
    # row break, so the note column keeps both of its lines.
    assert list(frame["id"]) == list(range(50))
    assert all("line one\nline two" in note for note in frame["note"])


def test_the_bounded_schema_head_read_is_quote_aware_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same defect lived in `read_csv_head` (#882) before #595 touched it —
    the schema/profile read every schema-drift run and dry-run preview takes. Age
    is not a disposition (CONTRIBUTING 3a), and both paths now share one cut."""
    _patch_store(monkeypatch, content=_quoted_csv(200_000))
    frame = flatfile.read_csv_head(conn_type="s3", config={}, path="raw/q.csv", secret="s", rows=5)
    assert list(frame.columns) == ["id", "note"]
    assert len(frame) == 5


def test_a_row_count_expectation_is_refused_on_a_sampled_flat_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C6. The expectation would observe the SAMPLE and report it as the file's
    size — a healthy 5M-row file with `min_value=4M` failing critically forever.
    Per-check `error` (#122), so its siblings on the same frame still evaluate."""
    _patch_store(monkeypatch, content=_csv_bytes(500))
    runner = flatfile.FlatFileCheckRunner(
        conn_type="s3",
        config={},
        secret="x",
        sampling=SampleSpec(strategy="head", rows=10),
    )
    outcome = runner.run_checks(
        table="raw/big.csv",
        schema=None,
        checks=[
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 400}),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "name"}),
        ],
    )

    assert [c.errored for c in outcome.checks] == [False, True, False]
    assert "row-count expectation cannot run against a sampled dataset" in (
        outcome.checks[1].error_message or ""
    )
    # Submission order preserved — `run_service` zips these onto its own list, so
    # a shuffle would attribute results to the wrong checks.
    assert [c.expectation_type for c in outcome.checks] == [
        "expect_column_values_to_not_be_null",
        "expect_table_row_count_to_be_between",
        "expect_column_values_to_not_be_null",
    ]
    assert outcome.success is False


def test_a_refused_row_count_check_carries_no_sampling_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record describes a READ, and a refused check performed none — claiming
    it saw 10 rows would be the overclaim the field exists to prevent. Its message
    already names sampling as the cause."""
    _patch_store(monkeypatch, content=_csv_bytes(500))
    runner = flatfile.FlatFileCheckRunner(
        conn_type="s3",
        config={},
        secret="x",
        sampling=SampleSpec(strategy="head", rows=10),
    )
    outcome = runner.run_checks(
        table="raw/big.csv",
        schema=None,
        checks=[
            CheckSpec("expect_table_row_count_to_be_between", {"min_value": 400}),
            CheckSpec("expect_column_values_to_not_be_null", {"column": "id"}),
        ],
    )
    assert outcome.checks[0].sampling is None
    assert outcome.checks[1].sampling is not None


def test_a_row_count_expectation_runs_normally_on_an_unsampled_flat_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoped to sampling: unsampled, the count IS the file's and the expectation
    is valid. A blanket ban would delete a working check from every suite."""
    _patch_store(monkeypatch, content=_csv_bytes(5))
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    outcome = runner.run_checks(
        table="raw/small.csv",
        schema=None,
        checks=[
            CheckSpec(
                "expect_table_row_count_to_be_between",
                {"min_value": 1, "max_value": 10},
            )
        ],
    )
    assert outcome.checks[0].success is True


def test_a_file_that_shrank_between_the_count_and_the_take_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J1. A random sample is inherently two passes — count, then take — and a
    landing zone is exactly where an object is re-uploaded between them. If it
    shrank, drawn positions fall past the new end and the take comes back short
    while `total_rows` still reports the old population: a sample both smaller and
    less representative than the record claims, with nothing saying so."""
    big = _csv_bytes(1_000)
    small = _csv_bytes(20)
    calls: list[int] = []

    def _counting_read_range(*, start: int, length: int, **_k: Any) -> bytes:
        # First pass (the row count) sees the big file; every later read sees the
        # replacement — the re-upload, reproduced deterministically.
        calls.append(1)
        content = big if len(calls) <= 2 else small
        return content[start : start + length]

    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(_LANDED, len(big)))
    monkeypatch.setattr(flatfile, "read_range", _counting_read_range)
    monkeypatch.setattr(flatfile, "object_size", lambda **k: len(big))

    with pytest.raises(SamplingDrawError, match="changed while it was being sampled"):
        flatfile.read_sampled_dataframe(
            conn_type="s3",
            config={},
            path="raw/moving.csv",
            secret="s",
            sample=SampleSpec(strategy="random", rows=100, seed=1),
        )


def test_a_random_sample_covering_the_whole_file_never_builds_an_index_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J4. `list(range(total))` is ~40 MB of Python ints at 1.4M rows, and
    `take_indices` would gather-copy every batch through it purely to reproduce
    the batches it was handed — all for a "sample" that changes nothing. Asserted
    on the SEAM, because the returned frame is identical either way."""
    _patch_store(monkeypatch, content=_csv_bytes(30))
    monkeypatch.setattr(
        flatfile,
        "take_indices",
        lambda *a, **k: pytest.fail("a full-coverage sample must not gather through indices"),
    )

    frame, record = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/small.csv",
        secret="s",
        sample=SampleSpec(strategy="random", rows=500, seed=1),
    )
    assert len(frame) == 30
    assert record["sampled"] is False and record["total_rows"] == 30
