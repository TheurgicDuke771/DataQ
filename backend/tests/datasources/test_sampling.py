"""Scale-aware execution core: the sampling spec, the scan guardrail (#595).

Everything here is pure — the readers in `flatfile.py` / `unity_catalog.py` supply
the real streams, and their tests exercise those. What this file pins is the part
that decides *what* gets read and *what gets claimed about it afterwards*, which
is where a quiet wrong answer would live: an off-by-one that turns a complete read
into a "sample", a guardrail that reads a disabled cap as zero-allowed, or a
selection pass that silently drops rows.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from backend.app.datasources.base import CheckOutcome, SampleSpec, SuiteOutcome
from backend.app.datasources.sampling import (
    MAX_SAMPLE_ROWS,
    SamplingConfigError,
    ScanTooLargeError,
    batches_to_frame,
    enforce_byte_cap,
    enforce_row_cap,
    enforce_sample_cap,
    parse_sample_spec,
    sample_row_indices,
    sampling_record,
    stamp_sampling,
    take_head,
    take_indices,
)

# ── parse_sample_spec ──


def test_absent_sampling_is_none_not_a_default_spec() -> None:
    """No block means "read everything", the historical behaviour — never a
    silently-applied default cap, which would change what every existing suite
    validates."""
    assert parse_sample_spec(None) is None


def test_a_valid_head_spec_parses() -> None:
    assert parse_sample_spec({"strategy": "head", "rows": 1000}) == SampleSpec(
        strategy="head", rows=1000, seed=None
    )


def test_a_valid_random_spec_keeps_its_seed() -> None:
    assert parse_sample_spec({"strategy": "random", "rows": 50, "seed": 7}) == SampleSpec(
        strategy="random", rows=50, seed=7
    )


@pytest.mark.parametrize(
    "raw",
    [
        "head",  # not an object
        ["head", 10],
        {"rows": 10},  # no strategy
        {"strategy": "tail", "rows": 10},  # unknown strategy
        {"strategy": "HEAD", "rows": 10},  # case matters — the vocabulary is closed
        {"strategy": "head"},  # no rows
        {"strategy": "head", "rows": 0},
        {"strategy": "head", "rows": -1},
        {"strategy": "head", "rows": MAX_SAMPLE_ROWS + 1},
        {"strategy": "head", "rows": "1000"},
        {"strategy": "head", "rows": 10.5},
        {"strategy": "head", "rows": None},
    ],
)
def test_a_malformed_spec_is_refused_not_coerced(raw: Any) -> None:
    """A spec that saves must be a spec that runs, so every shape problem is a
    422 at author time rather than a surprise at 02:00."""
    with pytest.raises(SamplingConfigError):
        parse_sample_spec(raw)


def test_a_boolean_rows_is_refused_because_true_would_read_as_one_row() -> None:
    """`bool` is an `int` subclass: without the explicit guard, `rows: true`
    parses as a ONE-row sample and every check silently validates a single row.
    The `monitors._anomaly_int` lesson, in a new place."""
    with pytest.raises(SamplingConfigError, match="boolean"):
        parse_sample_spec({"strategy": "head", "rows": True})


def test_an_integral_float_rows_is_accepted() -> None:
    """A JSON client sends `1000.0` for a whole number; refusing it would be a
    wire-format complaint dressed as a validation error."""
    spec = parse_sample_spec({"strategy": "head", "rows": 1000.0})
    assert spec == SampleSpec(strategy="head", rows=1000, seed=None)


def test_a_seed_on_a_head_spec_is_refused_not_ignored() -> None:
    """Ignoring it would leave the author believing their head sample is
    reproducible-random when it always reads the same first rows regardless."""
    with pytest.raises(SamplingConfigError, match="seed"):
        parse_sample_spec({"strategy": "head", "rows": 10, "seed": 1})


# ── sample_row_indices ──


def test_indices_are_sorted_distinct_and_in_range() -> None:
    indices = sample_row_indices(total=1000, rows=50, seed=1)
    assert len(indices) == 50
    assert len(set(indices)) == 50
    assert indices == sorted(indices)
    assert all(0 <= i < 1000 for i in indices)


def test_indices_are_sorted_for_a_seed_that_draws_them_unsorted() -> None:
    """The sort is load-bearing, not cosmetic: `take_indices` walks batches
    forwards and cannot revisit a position it has passed, so an unsorted list
    would silently DROP rows. Asserting `== sorted(...)` on one seed can pass by
    luck, so this checks a seed whose raw draw is demonstrably out of order."""
    raw = __import__("random").Random(3).sample(range(1000), 50)
    assert raw != sorted(raw), "pick a seed whose unsorted draw differs, or this proves nothing"
    assert sample_row_indices(total=1000, rows=50, seed=3) == sorted(raw)


def test_a_seed_makes_the_draw_reproducible_and_no_seed_does_not_pin_it() -> None:
    assert sample_row_indices(total=10_000, rows=20, seed=42) == sample_row_indices(
        total=10_000, rows=20, seed=42
    )
    # Unseeded draws over a large space colliding would be astronomically unlikely.
    unseeded = {tuple(sample_row_indices(total=10_000, rows=20, seed=None)) for _ in range(5)}
    assert len(unseeded) > 1


def test_a_sample_at_least_as_big_as_the_population_takes_every_row() -> None:
    """The full-coverage case: the caller reads everything and reports
    `sampled=False` rather than labelling a complete read a sample."""
    assert sample_row_indices(total=5, rows=5, seed=1) == [0, 1, 2, 3, 4]
    assert sample_row_indices(total=5, rows=99, seed=1) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("total", [0, -1])
def test_an_empty_population_yields_no_indices(total: int) -> None:
    assert sample_row_indices(total=total, rows=10, seed=1) == []


# ── sampling_record ──


def test_the_record_states_what_was_read_not_what_was_asked_for() -> None:
    spec = SampleSpec(strategy="random", rows=100, seed=3)
    record = sampling_record(spec, rows=97, total_rows=10_000, sampled=True)
    assert record == {
        "strategy": "random",
        "requested_rows": 100,
        "rows": 97,
        "total_rows": 10_000,
        "sampled": True,
        "seed": 3,
    }


def test_the_record_omits_a_seed_that_was_never_set() -> None:
    record = sampling_record(
        SampleSpec(strategy="head", rows=10), rows=10, total_rows=None, sampled=True
    )
    assert "seed" not in record
    assert record["total_rows"] is None


def test_a_sample_covering_everything_is_reported_as_not_sampled() -> None:
    """A "sample" of 1000 rows from a 12-row file saw the whole file. Claiming it
    was sampled would put a caveat on every small target, which trains users to
    ignore the caveat that matters (#424/#1115)."""
    record = sampling_record(
        SampleSpec(strategy="head", rows=1000), rows=12, total_rows=12, sampled=False
    )
    assert record["sampled"] is False


# ── the guardrail ──


def test_a_row_count_over_the_cap_is_refused_with_the_knob_named() -> None:
    with pytest.raises(ScanTooLargeError) as exc:
        enforce_row_cap(2_000_000, cap=1_500_000, target="table 'ORDERS'")
    message = str(exc.value)
    assert "2,000,000" in message and "1,500,000" in message
    assert "ORDERS" in message
    assert "RUN_MAX_SCAN_ROWS" in message
    assert "sampling" in message


def test_a_row_count_exactly_at_the_cap_is_allowed() -> None:
    """`>` not `>=`: a cap of N means N rows are permitted, which is what an
    operator setting it to their measured ceiling expects."""
    enforce_row_cap(1_500_000, cap=1_500_000, target="table 'ORDERS'")


@pytest.mark.parametrize("cap", [0, -1])
def test_a_disabled_row_cap_permits_anything(cap: int) -> None:
    enforce_row_cap(10**12, cap=cap, target="table 'ORDERS'")


def test_a_byte_size_over_the_cap_is_refused_with_the_knob_named() -> None:
    with pytest.raises(ScanTooLargeError) as exc:
        enforce_byte_cap(300_000_000, cap=268_435_456, target="file 'raw/big.csv'")
    message = str(exc.value)
    assert "300,000,000" in message and "268,435,456" in message
    assert "RUN_MAX_SCAN_BYTES" in message


@pytest.mark.parametrize("cap", [0, -1])
def test_a_disabled_byte_cap_permits_anything(cap: int) -> None:
    enforce_byte_cap(10**12, cap=cap, target="file 'raw/big.csv'")


def test_a_sample_bigger_than_the_row_cap_is_itself_refused() -> None:
    """Sampling is the way past the size probe *because* the read is bounded by
    the sample. That argument only holds while the sample fits, so the cap still
    applies to it — and the message names the sample, not the source, because the
    sample is what the author set."""
    with pytest.raises(ScanTooLargeError, match="sample of 5,000,000"):
        enforce_sample_cap(SampleSpec(strategy="head", rows=5_000_000), cap=1_500_000)


def test_a_sample_within_the_row_cap_is_allowed() -> None:
    enforce_sample_cap(SampleSpec(strategy="head", rows=100_000), cap=1_500_000)


def test_the_refusal_carries_no_driver_text_so_it_can_be_persisted_verbatim() -> None:
    """`ScanTooLargeError` is `SafeMonitorError`-marked, which is what lets
    `run_service._failure_reason` surface it instead of classifying it. That is
    only sound because every word of it is DataQ-authored — this pins the marker
    so a future subclass change can't quietly widen the redaction contract."""
    from backend.app.datasources.monitors import SafeMonitorError

    assert issubclass(ScanTooLargeError, SafeMonitorError)


# ── batch selection ──


def _batches(*sizes: int) -> list[Any]:
    """Record batches of `n` sequential ids, so a selection is checkable by value."""
    out: list[Any] = []
    start = 0
    for size in sizes:
        out.append(
            pa.record_batch(
                {"id": pa.array(range(start, start + size), type=pa.int64())},
            )
        )
        start += size
    return out


def _ids(batches: list[Any]) -> list[int]:
    return [v for batch in batches for v in batch.column("id").to_pylist()]


def test_take_head_slices_across_batch_boundaries() -> None:
    assert _ids(take_head(_batches(3, 3, 3), limit=4)) == [0, 1, 2, 3]


def test_take_head_stops_consuming_once_the_limit_is_reached() -> None:
    """The reader is a live stream: "stops early" is what makes a sampled read
    cheaper than a full one, so it is asserted on the generator, not just on the
    returned rows."""
    consumed: list[int] = []

    def _stream() -> Any:
        for batch in _batches(2, 2, 2, 2):
            consumed.append(batch.num_rows)
            yield batch

    assert _ids(take_head(_stream(), limit=3)) == [0, 1, 2]
    assert consumed == [2, 2], "the third batch must never have been pulled"


def test_take_head_returns_everything_when_the_limit_exceeds_the_stream() -> None:
    assert _ids(take_head(_batches(2, 2), limit=99)) == [0, 1, 2, 3]


def test_take_head_of_zero_takes_nothing() -> None:
    assert take_head(_batches(2, 2), limit=0) == []


def test_take_indices_picks_exactly_the_requested_global_positions() -> None:
    assert _ids(take_indices(_batches(4, 4, 4), [0, 5, 6, 11])) == [0, 5, 6, 11]


def test_take_indices_stops_once_the_last_index_has_been_passed() -> None:
    consumed: list[int] = []

    def _stream() -> Any:
        for batch in _batches(4, 4, 4):
            consumed.append(batch.num_rows)
            yield batch

    assert _ids(take_indices(_stream(), [1, 2])) == [1, 2]
    assert consumed == [4, 4], "only one batch past the last index should be pulled"


def test_take_indices_of_nothing_returns_nothing() -> None:
    assert take_indices(_batches(4), []) == []


# ── batches_to_frame ──


def test_an_empty_selection_still_produces_a_correctly_typed_empty_frame() -> None:
    """A frame with no COLUMNS would make every check error with "column not
    found" instead of failing honestly against an empty dataset — the schema is
    what keeps the verdict truthful."""
    schema = pa.schema([("id", pa.int64()), ("name", pa.string())])
    frame = batches_to_frame([], schema=schema, arrow_backed=False)
    assert list(frame.columns) == ["id", "name"]
    assert len(frame) == 0


def test_arrow_backed_mirrors_the_unsampled_parquet_reader_dtypes() -> None:
    """`flatfile.read_dataframe` reads Parquet with `dtype_backend="pyarrow"`;
    if the sampled path did not, switching a suite to sampling could change a
    check's verdict through a dtype change rather than through the data."""
    batches = _batches(2)
    arrow = batches_to_frame(batches, schema=batches[0].schema, arrow_backed=True)
    numpy = batches_to_frame(batches, schema=batches[0].schema, arrow_backed=False)
    assert str(arrow["id"].dtype) == "int64[pyarrow]"
    assert str(numpy["id"].dtype) == "int64"


# ── stamp_sampling ──


def _outcome(*names: str) -> SuiteOutcome:
    return SuiteOutcome(
        success=True,
        checks=[CheckOutcome(expectation_type=n, success=True) for n in names],
    )


def test_stamping_none_leaves_the_outcome_untouched() -> None:
    outcome = _outcome("a", "b")
    assert stamp_sampling(outcome, None) is outcome


def test_stamping_marks_every_check_in_the_group() -> None:
    record = {"strategy": "head", "rows": 10, "sampled": True}
    stamped = stamp_sampling(_outcome("a", "b"), record)
    assert [c.sampling for c in stamped.checks] == [record, record]
    assert stamped.success is True


def test_stamping_preserves_the_rest_of_each_outcome() -> None:
    outcome = SuiteOutcome(
        success=False,
        checks=[
            CheckOutcome(
                expectation_type="expect_column_values_to_not_be_null",
                success=False,
                metric_value=12.5,
                errored=True,
                error_message="boom",
            )
        ],
    )
    stamped = stamp_sampling(outcome, {"sampled": True})
    check = stamped.checks[0]
    assert check.metric_value == 12.5
    assert check.errored is True
    assert check.error_message == "boom"
    assert check.sampling == {"sampled": True}
