"""JSON flat files (#1677) — every case parses REAL JSON bytes through the real
readers (`pyarrow.json` behind `jsonfile`); no frame is hand-built in the shape the
runner expects, because the dtype the driver hands back is what these tests are for.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from backend.app.datasources import flatfile, jsonfile
from backend.app.datasources.base import CheckSpec, MonitorSpec, SampleSpec

_LANDED = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)


def _lines(rows: list[Any]) -> bytes:
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


def _array(rows: list[Any], *, indent: int | None = 2) -> bytes:
    return json.dumps(rows, indent=indent).encode()


def _patch_store(
    monkeypatch: pytest.MonkeyPatch, content: bytes, *, downloads: list[int] | None = None
) -> None:
    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(_LANDED, len(content)))

    def _download(**_k: Any) -> bytes:
        if downloads is not None:
            downloads.append(1)
        return content

    monkeypatch.setattr(flatfile, "download_bytes", _download)
    monkeypatch.setattr(flatfile, "object_size", lambda **k: len(content))
    monkeypatch.setattr(
        flatfile, "read_range", lambda *, start, length, **_k: content[start : start + length]
    )


def _read(content: bytes, path: str = "raw/orders.jsonl") -> pd.DataFrame:
    return jsonfile.to_frame(jsonfile.read_table(lambda: io.BytesIO(content)))


_ROWS = [
    {"id": 1, "ts": "2026-06-28T10:00:00Z", "amount": 1.5, "ok": True, "note": None},
    {"id": 2, "ts": "2026-06-29T10:00:00Z", "amount": 2, "ok": False, "note": "b"},
    {"id": None, "ts": "2026-06-27", "amount": None, "ok": None, "note": "c"},
]


# ── format + shapes ──


@pytest.mark.parametrize("path", ["a.jsonl", "A.NDJSON", "x/y.json"])
def test_json_extensions_read_as_json(path: str) -> None:
    assert flatfile.format_from_path(path) == "json"


@pytest.mark.parametrize(
    "content",
    [_lines(_ROWS), _array(_ROWS), _array(_ROWS, indent=None), b"\xef\xbb\xbf" + _array(_ROWS)],
    ids=["lines", "array-pretty", "array-compact", "array-bom"],
)
def test_both_shapes_read_to_the_same_typed_frame(content: bytes) -> None:
    frame = _read(content)
    assert list(frame.columns) == ["id", "ts", "amount", "ok", "note"]
    assert {c: str(t) for c, t in frame.dtypes.items()} == {
        "id": "int64[pyarrow]",
        "ts": "string[pyarrow]",
        "amount": "double[pyarrow]",
        "ok": "bool[pyarrow]",
        "note": "string[pyarrow]",
    }
    # An integer column with a null stays an integer — no numpy float widening.
    assert frame["id"].tolist()[:2] == [1, 2] and pd.isna(frame["id"].iloc[2])


def test_iso_strings_stay_text_exactly_as_written() -> None:
    """pyarrow alone would read whole-second ISO strings as `timestamp[s]` (dropping the
    `Z`, turning a date into midnight) and leave a fractional one as text — a type that
    flips on data. The literal must survive.
    """
    frame = _read(_lines(_ROWS))
    assert frame["ts"].tolist() == ["2026-06-28T10:00:00Z", "2026-06-29T10:00:00Z", "2026-06-27"]


def test_text_override_keeps_the_file_column_order() -> None:
    """An explicitly-typed column is placed FIRST by pyarrow; the file's order must win."""
    frame = _read(_lines([{"a": 1, "b": 2, "when": "2026-01-01T00:00:00", "z": "x"}]))
    assert list(frame.columns) == ["a", "b", "when", "z"]


def test_a_temporal_field_first_seen_past_the_first_block_is_still_text() -> None:
    filler = [{"id": i, "pad": "x" * 200} for i in range(6000)]  # > 1 MiB before `late`
    late = [{"id": 6000, "pad": "x", "late": "2026-01-01T00:00:00"}]
    content = _lines(filler + late)
    assert len(content) > jsonfile.BLOCK_BYTES
    frame = _read(content)
    assert str(frame["late"].dtype) == "string[pyarrow]"
    assert frame["late"].iloc[-1] == "2026-01-01T00:00:00"
    assert list(frame.columns) == ["id", "pad", "late"]


def test_a_pretty_printed_array_larger_than_a_parse_block_reads_whole() -> None:
    """pyarrow cuts blocks at newlines; a pretty-printed element spans several, so an
    unfolded element straddling a block boundary would be split mid-object.
    """
    rows = [{"id": i, "name": f"row {i}", "amount": i / 4} for i in range(30_000)]
    content = _array(rows, indent=4)
    assert len(content) > 2 * jsonfile.BLOCK_BYTES
    frame = _read(content)
    assert len(frame) == 30_000 and frame["id"].iloc[-1] == 29_999


def test_a_json_string_with_an_escaped_newline_and_delimiters_survives_the_array_path() -> None:
    rows = [{"s": 'a,]\n"}', "n": 1}, {"s": "é中", "n": 2}]
    assert _read(_array(rows))["s"].tolist() == [rows[0]["s"], rows[1]["s"]]


def test_the_array_transcoder_survives_every_chunk_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Split elements, keys and multi-byte characters across the read boundary."""
    rows = [{"kéy": f"v中{i}", "n": i} for i in range(20)]
    content = _array(rows)
    for size in (1, 2, 3, 7, 64):
        monkeypatch.setattr(jsonfile, "_READ_BYTES", size)
        frame = _read(content)
        assert frame["n"].tolist() == list(range(20)), size
        assert frame["kéy"].iloc[19] == "v中19"


@pytest.mark.parametrize("content", [b"", b"  \n", b"[]", b" [ \n ] "])
def test_an_empty_file_or_array_is_zero_rows_not_an_error(content: bytes) -> None:
    assert len(_read(content)) == 0
    assert jsonfile.count_rows(lambda: io.BytesIO(content)) == 0


# ── refusals: classified, and never echoing a value from the file ──


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b'{"user": {"id": 1}, "id": 1}\n', "'user' hold nested"),
        (b'{"tags": ["SECRET"], "id": 1}\n', "'tags' hold nested"),
        (b'[{"a": 1}, "SECRET"]', "element 2 of the JSON array is not an object"),
        (b'[{"a": 1},]', "element 2 of the JSON array is not an object"),
        (b'[{"a": 1}', "not terminated"),
        (b'[{"a": 1}] {"b": 2}', "content after"),
        (b'[{"a": 1} {"b": 2}]', "expected ',' or ']'"),
        (b'{"a": 1}\n{"a": "SECRET"}\n', "column 'a' holds number values and later string"),
        (b'{"a": 1, "a": 2}\n', "repeats the key 'a'"),
        (b'{"a": 1}\n{"a": SECRET\n', "not valid JSON Lines"),
        (b'{"a": 1}\n5\n', "one object"),
        (b"id,name\n1,SECRET\n", "neither JSON Lines"),
        (b'[{"a": "\xff"}]', "not UTF-8"),
    ],
)
def test_unreadable_json_is_refused_with_a_dataq_message(content: bytes, expected: str) -> None:
    with pytest.raises(jsonfile.JsonFileError) as info:
        _read(content)
    assert expected in str(info.value)
    assert "SECRET" not in str(info.value)


def test_a_nested_column_past_the_first_block_is_refused_on_the_full_read() -> None:
    content = _lines(
        [{"id": i, "pad": "x" * 200} for i in range(6000)] + [{"id": 1, "m": {"a": 1}}]
    )
    with pytest.raises(jsonfile.JsonFileError, match="'m' hold nested"):
        _read(content)


def test_a_pyarrow_conversion_message_never_reaches_the_user() -> None:
    """`Failed to convert JSON to int64, couldn't parse:<value>` quotes the data."""
    content = _lines([{"a": 1}] * 20_000 + [{"a": "SECRET-VALUE"}])
    with pytest.raises(jsonfile.JsonFileError) as info:
        it, _schema, close = jsonfile.open_batches(lambda: io.BytesIO(content))
        try:
            list(it)
        finally:
            close()
    assert "SECRET-VALUE" not in str(info.value)


# ── counting ──


def test_count_rows_counts_objects_in_both_shapes_ignoring_blank_lines() -> None:
    assert jsonfile.count_rows(lambda: io.BytesIO(b'{"a":1}\n\n{"b":"x"}\n\r\n{"a":2}')) == 3
    assert jsonfile.count_rows(lambda: io.BytesIO(_array(_ROWS))) == 3


# ── the runner: full read, sampling, monitors ──

_CHECKS = [
    CheckSpec(expectation_type="expect_column_values_to_not_be_null", kwargs={"column": "id"}),
    CheckSpec(
        expectation_type="expect_column_values_to_be_between",
        kwargs={"column": "amount", "min_value": 0, "max_value": 10},
    ),
    CheckSpec(
        expectation_type="expect_column_values_to_match_regex",
        kwargs={"column": "ts", "regex": r"^2026-06"},
    ),
    CheckSpec(expectation_type="expect_column_values_to_be_unique", kwargs={"column": "id"}),
]


@pytest.mark.parametrize("path", ["raw/orders.jsonl", "raw/orders.json"])
def test_run_checks_over_a_real_json_file(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    content = _lines(_ROWS) if path.endswith("l") else _array(_ROWS)
    _patch_store(monkeypatch, content)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="s")
    outcome = runner.run_checks(table=path, schema=None, checks=_CHECKS)
    assert [c.success for c in outcome.checks] == [False, True, True, True]
    assert not any(c.errored for c in outcome.checks)
    sample = outcome.checks[0].sample_failures
    assert sample is not None and sample["unexpected_count"] == 1  # the one null id
    assert all(c.sampling is None for c in outcome.checks)


def _many(n: int) -> list[dict[str, Any]]:
    return [{"id": i, "ts": f"2026-06-{1 + i % 28:02d}", "amount": i % 7} for i in range(n)]


@pytest.mark.parametrize("strategy", ["head", "random"])
def test_sampled_json_run_reads_a_sample_and_says_so(
    monkeypatch: pytest.MonkeyPatch, strategy: str
) -> None:
    content = _lines(_many(5000))
    downloads: list[int] = []
    _patch_store(monkeypatch, content, downloads=downloads)
    seed = 7 if strategy == "random" else None
    runner = flatfile.FlatFileCheckRunner(
        conn_type="s3",
        config={},
        secret="s",
        sampling=SampleSpec(strategy=strategy, rows=100, seed=seed),
    )
    outcome = runner.run_checks(table="raw/orders.jsonl", schema=None, checks=_CHECKS[:2])
    record = outcome.checks[0].sampling
    assert record is not None and record["sampled"] is True and record["rows"] == 100
    # The random draw walks the whole file once, so it knows the population; head does not.
    assert record["total_rows"] == (5000 if strategy == "random" else None)
    assert downloads == []  # never the whole-object download


def test_random_sample_of_a_json_array_is_uniform_over_the_whole_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch, _array(_many(3000), indent=None))
    frame, record = flatfile.read_sampled_dataframe(
        conn_type="s3",
        config={},
        path="raw/orders.json",
        secret="s",
        sample=SampleSpec(strategy="random", rows=50, seed=3),
    )
    assert record["total_rows"] == 3000 and len(frame) == 50
    assert frame["id"].max() > 1500  # not just the head


def test_sampling_refuses_a_file_whose_types_change_past_the_first_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full read unifies int + decimal to double; a streamed read is typed from the
    first block and must refuse, not silently retype mid-stream.
    """
    rows = [{"id": i, "amount": 1} for i in range(120_000)] + [{"id": 0, "amount": 1.5}]
    content = _lines(rows)
    assert len(content) > jsonfile.BLOCK_BYTES
    _patch_store(monkeypatch, content)
    assert (
        str(
            flatfile.read_dataframe(conn_type="s3", config={}, path="a.jsonl", secret="s")[
                "amount"
            ].dtype
        )
        == "double[pyarrow]"
    )
    with pytest.raises(jsonfile.JsonFileError, match="turn sampling off"):
        flatfile.read_sampled_dataframe(
            conn_type="s3",
            config={},
            path="a.jsonl",
            secret="s",
            sample=SampleSpec(strategy="random", rows=10, seed=1),
        )


def _monitors(**config: Any) -> list[MonitorSpec]:
    return [
        MonitorSpec(kind="freshness", config={"column": "ts", **config}),
        MonitorSpec(kind="freshness", config=dict(config)),
        MonitorSpec(kind="volume", config={"min_rows": 1, "max_rows": 10}),
    ]


def test_freshness_parses_the_json_text_column_and_arrival_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_store(monkeypatch, _lines(_ROWS))
    runner = flatfile.FlatFileCheckRunner(conn_type="adls_gen2", config={}, secret="s")
    column, arrival, volume = runner.run_monitors(
        table="raw/orders.jsonl", schema=None, monitors=_monitors()
    )
    assert column.errored is False, column.error_message
    assert column.observed_value is not None and arrival.observed_value is not None
    assert column.observed_value["max_timestamp"].startswith("2026-06-29T10:00:00")
    assert arrival.observed_value["max_timestamp"].startswith("2026-06-29T00:00:00")
    assert volume.errored is False and volume.metric_value == 0.0


def test_volume_alone_counts_a_json_file_without_downloading_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads: list[int] = []
    _patch_store(monkeypatch, _array(_many(40)), downloads=downloads)
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="s")
    (volume,) = runner.run_monitors(
        table="raw/orders.json",
        schema=None,
        monitors=[MonitorSpec(kind="volume", config={"min_rows": 50, "max_rows": 60})],
    )
    assert volume.errored is False
    assert volume.metric_value == pytest.approx(20.0)  # 40 against a floor of 50
    assert downloads == []


def test_a_nested_json_run_fails_with_the_nested_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_store(monkeypatch, _lines([{"id": 1, "addr": {"city": "x"}}]))
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="s")
    with pytest.raises(jsonfile.JsonFileError, match="'addr' hold nested"):
        runner.run_checks(table="raw/orders.jsonl", schema=None, checks=_CHECKS[:1])


def test_json_schema_is_the_streamed_types_and_names(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_store(monkeypatch, _array(_ROWS))
    schema = flatfile.json_schema(conn_type="s3", config={}, path="x.json", secret="s")
    assert [(f.name, str(f.type)) for f in schema] == [
        ("id", "int64"),
        ("ts", "string"),
        ("amount", "double"),
        ("ok", "bool"),
        ("note", "string"),
    ]


def test_json_head_reads_only_the_first_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_store(monkeypatch, _lines(_many(1000)))
    frame = flatfile.read_json_head(conn_type="s3", config={}, path="x.jsonl", secret="s", rows=10)
    assert frame["id"].tolist() == list(range(10))


class _CountingStream(io.BytesIO):
    """Counts the bytes a reader pulls — the store cost of a schema peek."""

    pulled = 0

    def read(self, size: int | None = -1) -> bytes:
        data = super().read(size)
        self.pulled += len(data)
        return data


def test_a_schema_peek_reads_about_one_block_not_pyarrow_s_readahead() -> None:
    """pyarrow's streaming reader reads ~32 blocks ahead as it opens; the peek must not."""
    stream = _CountingStream(_lines([{"id": i, "pad": "x" * 100} for i in range(150_000)]))

    def rewind() -> Any:
        stream.seek(0)
        return stream

    schema = jsonfile.first_block_schema(rewind)
    assert schema is not None and schema.names == ["id", "pad"]
    # shape sniff (1 block) + the first block itself
    assert stream.pulled <= 3 * jsonfile.BLOCK_BYTES


def test_a_json_file_compares_clean_against_its_csv_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison kind across formats: an Arrow-backed JSON frame (int64 with a null,
    string[pyarrow]) against the numpy CSV frame of the same rows (float64 NaN, object)
    must diff as identical — both frames from the real readers.
    """
    from backend.app.datasources.comparison import compare_records

    rows = [
        {"id": 1, "qty": 3, "sku": "a", "ts": "2026-06-28T10:00:00", "price": 1.5},
        {"id": 2, "qty": None, "sku": None, "ts": "2026-06-29T10:00:00", "price": 2.0},
        {"id": 3, "qty": 7, "sku": "c", "ts": "2026-06-30T10:00:00", "price": None},
    ]
    csv = pd.DataFrame(rows).to_csv(index=False).encode()
    frames = {}
    for path, content in (("x.jsonl", _lines(rows)), ("x.csv", csv)):
        _patch_store(monkeypatch, content)
        frames[path] = flatfile.read_dataframe(conn_type="s3", config={}, path=path, secret="s")
    result = compare_records(frames["x.jsonl"], frames["x.csv"], keys=["id"])
    assert (result.matched, result.mismatched) == (3, 0), result
    assert (result.additional_in_source, result.additional_in_target) == (0, 0)


def test_of_type_on_json_columns_matches_python_value_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented `type_` guidance for JSON: GX compares Python value types on these
    Arrow-backed columns, so `int` passes on an integer column with a null and `int64` does not.
    """
    rows = [{"n": None if i == 3 else i, "f": i * 1.5, "b": i % 2 == 0, "s": "x"} for i in range(6)]
    _patch_store(monkeypatch, _lines(rows))
    cases = [
        ("n", "int", True),
        ("n", "int64", False),
        ("f", "float", True),
        ("b", "bool", True),
        ("s", "str", True),
        ("s", "object", False),
    ]
    checks = [
        CheckSpec(
            expectation_type="expect_column_values_to_be_of_type",
            kwargs={"column": column, "type_": type_},
        )
        for column, type_, _ in cases
    ]
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="s")
    outcome = runner.run_checks(table="raw/t.jsonl", schema=None, checks=checks)
    assert [c.success for c in outcome.checks] == [expected for _, _, expected in cases]
