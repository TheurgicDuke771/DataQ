"""Scale-aware execution: the sampling spec + the scan guardrail (#595, G-b).

Two orthogonal ideas live here, both datasource-agnostic and both pure:

* **Sampling** — a suite may declare, on its run target, that checks run against a
  bounded *sample* of the dataset rather than all of it (``head`` = the first N
  rows, ``random`` = N rows drawn uniformly without replacement). Only the
  full-load runners need it: Snowflake pushes every expectation down as SQL and
  never materialises rows in the worker (perf-baseline: 200M rows, worker flat),
  so a sampling spec there would be a lie. The capable set is declared in
  `datasources.registry`, which refuses the spec on any other datasource at save
  time rather than silently ignoring it.

* **The guardrail** — a *size probe* (an object's byte length, a table's
  ``COUNT(*)``) checked against a configurable hard cap **before** anything is
  materialised, so an oversized target ends the run with a clear, DataQ-authored
  reason instead of OOM-killing the Celery child. That failure mode is not
  hypothetical: today the kernel SIGKILLs the child, nothing maps
  ``WorkerLostError`` back to the run row, and the run sits ``running`` for up to
  60 minutes until the stuck-run reaper fails it with no memory-attributed reason
  (#755). Refusing is strictly better than dying.

**Sampled-ness is recorded, never assumed.** `sampling_record` builds the payload
that lands on ``results.sampling`` and reaches the read API: a check that passed
on a sample must say so. It carries an explicit ``sampled`` boolean, because a
sample larger than the dataset is *not* a sample — claiming otherwise would cry
wolf on every small table, which is the confidently-wrong-label class this
codebase keeps closing (#424/#1115).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from backend.app.core.errors import SafeMonitorError
from backend.app.datasources.base import (
    SAMPLE_HEAD,
    SAMPLE_RANDOM,
    SAMPLING_STRATEGIES,
    CheckOutcome,
    CheckSpec,
    SampleSpec,
    SuiteOutcome,
)

#: Structural bound on a sample size. Not a memory guardrail (that is
#: ``RUN_MAX_SCAN_ROWS``, applied per datasource with its own probe) — this only
#: keeps an obviously-nonsensical spec (``rows: 10**12``) out of the target
#: document at save time, where the error is a 422 the author sees immediately
#: rather than an OOM three schedules later.
MAX_SAMPLE_ROWS = 10_000_000


class SamplingConfigError(ValueError):
    """A ``sampling`` block on a run target is missing or malformed.

    Surfaced as a 422 at suite-save time (via `TargetShapeError`), so a spec that
    saves is a spec that runs — the same contract `registry._batch_spec` keeps for
    batch targets.
    """


class ScanTooLargeError(SafeMonitorError, RuntimeError):
    """The target is larger than the configured scan cap; DataQ refuses to load it.

    `SafeMonitorError`-marked deliberately: every message this can carry is built
    here from the user's own configuration (a path or table name they typed, two
    integers we chose) and never interpolates a driver message, a URL or a
    connection string — so it is safe to persist verbatim into
    ``results.observed_value`` and to surface as a run's ``failure_reason``.
    Classifying it instead would replace the one actionable sentence ("this file
    is 1.2 GB, over the 256 MB cap — enable sampling or narrow the target") with
    "the run failed to execute; see the server logs", which is precisely the
    outcome #755 already delivers.
    """


class SamplingDrawError(SafeMonitorError, RuntimeError):
    """A sampled read came back in a shape that cannot support a verdict (#595).

    Today one case: a random draw that returned **zero** rows from a non-empty
    dataset. Every column expectation passes vacuously on an empty frame, so the
    run would print a full green board while asserting nothing — the fabricated
    -pass outcome this codebase refuses everywhere else (an anomaly monitor with
    no baseline `skip`s rather than inventing a verdict).

    `SafeMonitorError`-marked for the same reason as `ScanTooLargeError`: the
    message is built from the target's own name and a row count DataQ measured,
    so it is safe to persist verbatim and useless once classified.
    """


#: GX expectations whose observed value IS the row count of the batch they run
#: on — so against a sampled frame they deterministically measure the SAMPLE and
#: report it as the dataset's size (#595). A healthy 5M-row file with
#: ``min_value=4_000_000`` fails critically forever under a 100k sample, and the
#: inverse (``max_value``) passes wrongly. Freshness monitors were exempted from
#: sampling for exactly this smaller-aggregate reason; these are the expectation
#: -side instances of it, and they are refused rather than silently mismeasured.
#:
#: Declared as data — and pinned by a canary test — so widening it is a
#: deliberate edit. `expect_table_row_count_to_equal_other_table` is included:
#: its own side is still the sampled batch, so the comparison is against a
#: number that is not the dataset's.
ROW_COUNT_EXPECTATION_TYPES: frozenset[str] = frozenset(
    {
        "expect_table_row_count_to_be_between",
        "expect_table_row_count_to_equal",
        "expect_table_row_count_to_equal_other_table",
    }
)

#: The one message both the save-time gate and the run-time refusal use, so an
#: author who somehow reaches the run (a suite that predates the gate, an
#: imported suite) reads the same sentence the editor would have shown.
SAMPLING_ROW_COUNT_CONFLICT = (
    "a table row-count expectation cannot run against a sampled dataset — it would "
    "measure the SAMPLE and report it as the dataset's size (a 100k sample of a 5M-row "
    "table reads as 100k rows). Use a volume monitor, which counts the whole dataset "
    "without loading it, or drop the sampling block from the suite's run target."
)


def is_row_count_expectation(expectation_type: str) -> bool:
    """Whether this expectation measures the batch's row count (see the set above)."""
    return expectation_type in ROW_COUNT_EXPECTATION_TYPES


def parse_sample_spec(raw: Any) -> SampleSpec | None:
    """Validate a target's ``sampling`` block, or ``None`` when it has none.

    Raises `SamplingConfigError` on anything malformed. ``bool`` is rejected for
    the integer fields explicitly — it is an ``int`` subclass, so ``True`` would
    otherwise sail through as a one-row sample (the `monitors._anomaly_int`
    lesson). An integral ``float`` (``1000.0``, what a JSON client sends for a
    whole number) is accepted; a fractional one is not.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SamplingConfigError(f"target 'sampling' must be an object: {raw!r}")
    strategy = raw.get("strategy")
    if strategy not in SAMPLING_STRATEGIES:
        raise SamplingConfigError(
            f"sampling strategy must be one of {', '.join(SAMPLING_STRATEGIES)}: {strategy!r}"
        )
    rows = _whole_number(raw.get("rows"), "rows")
    if rows is None:
        raise SamplingConfigError("sampling needs a 'rows' count")
    if not 1 <= rows <= MAX_SAMPLE_ROWS:
        raise SamplingConfigError(f"sampling rows must be between 1 and {MAX_SAMPLE_ROWS}: {rows}")
    seed = _whole_number(raw.get("seed"), "seed")
    if seed is not None and strategy != SAMPLE_RANDOM:
        # Known key, inapplicable strategy. Ignoring it silently would leave the
        # author believing their head sample is reproducible-random — the same
        # quiet-wrong shape `monitors.anomaly_params` refuses for `column`.
        raise SamplingConfigError(
            f"sampling 'seed' applies only to strategy {SAMPLE_RANDOM!r}; "
            f"{SAMPLE_HEAD!r} always reads the first rows in storage order"
        )
    return SampleSpec(strategy=str(strategy), rows=rows, seed=seed)


def _whole_number(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SamplingConfigError(f"sampling {field} must be an integer, not a boolean")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise SamplingConfigError(f"sampling {field} must be an integer: {value!r}")
    return value


def sample_row_indices(*, total: int, rows: int, seed: int | None) -> list[int] | None:
    """``rows`` positions drawn uniformly from ``range(total)``, sorted — or ``None``.

    Sorted because every reader consuming this walks the dataset **forwards** in
    batches (a Parquet row-group iterator, a streamed CSV, a warehouse cursor) and
    can only take positions it has not passed. Sorting here rather than at each
    call site means the ordering contract is stated once.

    ``random.Random.sample`` over a ``range`` uses the selection-set algorithm, so
    drawing 100k positions out of 50M costs the 100k, not the 50M — the sample
    itself never becomes the memory problem it exists to solve.

    ``rows >= total`` returns ``None``, not ``list(range(total))`` — the sample
    covers everything, so there is no selection to make. Materialising the identity
    list is not free: at 1.4M rows it is ~40 MB of Python ints, and `take_indices`
    would then gather-copy every batch through it, all to reproduce the batches it
    was handed. The caller reads the dataset straight through and reports
    ``sampled=False``, rather than pretending a full read was a sample.
    """
    if total <= 0 or rows >= total:
        return None
    return sorted(random.Random(seed).sample(range(total), rows))  # noqa: S311  # nosec B311


def sampling_record(
    spec: SampleSpec, *, rows: int, total_rows: int | None, sampled: bool
) -> dict[str, Any]:
    """The payload persisted on ``results.sampling`` and surfaced by the read API.

    ``rows`` is what the check engine actually saw; ``total_rows`` is the
    population it was drawn from, or ``None`` when learning it would have cost a
    scan the sample exists to avoid (a ``head`` sample of a CSV stops reading at
    the cap and never learns how many rows follow). ``sampled`` is the honest
    headline: ``False`` means the "sample" covered the whole dataset, so the
    check's verdict is complete and no caveat should be shown.
    """
    record: dict[str, Any] = {
        "strategy": spec.strategy,
        "requested_rows": spec.rows,
        "rows": rows,
        "total_rows": total_rows,
        "sampled": sampled,
    }
    if spec.seed is not None:
        record["seed"] = spec.seed
    return record


def enforce_row_cap(count: int, *, cap: int, target: str) -> None:
    """Refuse a read of ``count`` rows when it exceeds ``cap`` (``cap <= 0`` disables).

    The warehouse half of the guardrail: a ``COUNT(*)`` is cheap and exact, so the
    refusal happens before a single row transfers.
    """
    if cap > 0 and count > cap:
        raise ScanTooLargeError(
            f"{target} has {count:,} rows, over the scan cap of {cap:,}. DataQ refuses to "
            "load it rather than risk an out-of-memory worker: set a sampling strategy on "
            "the suite's run target, narrow the target, or raise RUN_MAX_SCAN_ROWS "
            "deliberately."
        )


def enforce_byte_cap(size: int, *, cap: int, target: str) -> None:
    """Refuse a read of ``size`` bytes when it exceeds ``cap`` (``cap <= 0`` disables).

    The flat-file half. Bytes rather than rows because a file's row count is not
    knowable cheaply for CSV (it needs a full scan, which is the cost being
    avoided) while its byte length is one metadata call — and because the measured
    ceiling tracks bytes far better than rows: on a 2 GiB worker a 131 MB / 5M-row
    Parquet passes where a 304 MB / 5M-row CSV dies (docs/perf-baseline.md).
    """
    if cap > 0 and size > cap:
        raise ScanTooLargeError(
            f"{target} is {size:,} bytes, over the scan cap of {cap:,}. DataQ refuses to "
            "load it rather than risk an out-of-memory worker: set a sampling strategy on "
            "the suite's run target, target a smaller file, or raise RUN_MAX_SCAN_BYTES "
            "deliberately."
        )


def enforce_sample_cap(spec: SampleSpec, *, cap: int) -> None:
    """Refuse a *sample* that is itself over the row cap (``cap <= 0`` disables).

    Sampling is the sanctioned way past the size probe — the read is bounded by
    ``spec.rows`` by construction, so the source's own size stops mattering. That
    only holds while the sample is smaller than what the worker can hold, so the
    cap still applies to the sample itself. Checked separately (and stated
    separately) so the error names the knob the author actually set.
    """
    if cap > 0 and spec.rows > cap:
        raise ScanTooLargeError(
            f"the run target's sample of {spec.rows:,} rows is itself over the scan cap of "
            f"{cap:,}. Lower the sample size, or raise RUN_MAX_SCAN_ROWS deliberately."
        )


# ─────────────────── batch-stream selection (pure, IO-free) ───────────────────
#
# Both helpers take an *iterable of Arrow record batches* and return the selected
# rows, so the selection logic is unit-testable against hand-built batches while
# the readers in `flatfile.py` supply the real streams. Peak memory is one batch
# plus the retained rows — never the whole object, which is the entire point.


def take_head(batches: Iterable[Any], *, limit: int) -> list[Any]:
    """The first ``limit`` rows across ``batches``, as a list of Arrow batches.

    Stops consuming as soon as the limit is reached, so the underlying reader
    never fetches the rest of the object. Callers pass ``limit = rows + 1`` and
    trim: reading one row past the sample is what distinguishes "the dataset has
    exactly N rows" (a complete read) from "the dataset has more" (a real sample),
    without a second pass to count.
    """
    taken: list[Any] = []
    if limit <= 0:
        return taken
    got = 0
    for batch in batches:
        remaining = limit - got
        slice_ = batch.slice(0, remaining) if batch.num_rows > remaining else batch
        if slice_.num_rows:
            taken.append(slice_)
            got += slice_.num_rows
        # Checked AFTER taking, not before: a top-of-loop test pulls one further
        # batch from the live reader before noticing it is already done, which on
        # a range-backed stream is a wasted request of exactly the size this
        # function exists to avoid fetching.
        if got >= limit:
            break
    return taken


def take_indices(batches: Iterable[Any], indices: list[int]) -> list[Any]:
    """The rows at the (sorted, ascending) global positions ``indices``.

    One forward pass: each batch takes the positions that fall inside it, then the
    walk stops as soon as the last requested index has been passed — so a sample
    of early rows does not stream the tail of the object. ``indices`` **must** be
    sorted (`sample_row_indices` guarantees it); an unsorted list would silently
    drop positions already walked past, which is why the ordering contract lives
    with the generator rather than here.
    """
    import pyarrow as pa

    taken: list[Any] = []
    offset = 0
    cursor = 0
    for batch in batches:
        if cursor >= len(indices):
            break
        end = offset + batch.num_rows
        selected: list[int] = []
        while cursor < len(indices) and indices[cursor] < end:
            selected.append(indices[cursor] - offset)
            cursor += 1
        if selected:
            taken.append(batch.take(pa.array(selected, type=pa.int64())))
        offset = end
    return taken


def batches_to_frame(batches: list[Any], *, schema: Any, arrow_backed: bool) -> Any:
    """Concatenate selected Arrow batches into a pandas DataFrame.

    ``schema`` is the reader's own schema, needed so a selection that matched
    **zero** rows still produces a correctly-typed empty frame rather than a
    shapeless one — an empty frame with no columns would make every check error
    with "column not found" instead of failing honestly on an empty dataset.

    ``arrow_backed`` mirrors what the unsampled reader for that format does, so
    switching a suite to sampling cannot change a check's verdict through a dtype
    change: `flatfile.read_dataframe` reads Parquet with
    ``dtype_backend="pyarrow"`` and CSV through ``pd.read_csv`` (NumPy-backed).
    """
    import pandas as pd
    import pyarrow as pa

    table = pa.Table.from_batches(batches, schema=schema) if batches else schema.empty_table()
    if arrow_backed:
        return table.to_pandas(types_mapper=pd.ArrowDtype)
    return table.to_pandas()


def split_row_count_checks(
    checks: list[CheckSpec],
) -> tuple[list[int], dict[int, CheckOutcome]]:
    """Partition ``checks`` into (runnable positions, refusals keyed by position).

    The run-time half of the row-count/sampling refusal (#595 C6). The author-time
    gate in `check_service`/`suite_service` catches the combination when it is
    created, but a suite that predates the gate — or one that arrived through
    import — still reaches a runner, and there the expectation would quietly
    measure the sample and report it as the dataset's size.

    A per-check ``error`` rather than a failed run, matching #122: the other
    expectations on the same sampled frame are perfectly valid and must still be
    evaluated and persisted. The refusals come back keyed by submission position
    so a runner can merge them into its outcome list without disturbing the 1:1
    order `run_service` zips onto its `checks`.
    """
    runnable: list[int] = []
    refused: dict[int, CheckOutcome] = {}
    for index, spec in enumerate(checks):
        if is_row_count_expectation(spec.expectation_type):
            refused[index] = CheckOutcome(
                expectation_type=spec.expectation_type,
                success=False,
                errored=True,
                error_message=SAMPLING_ROW_COUNT_CONFLICT,
                expected_value=dict(spec.kwargs) or None,
            )
        else:
            runnable.append(index)
    return runnable, refused


def merge_by_position(
    total: int, *groups: tuple[list[int], list[CheckOutcome]]
) -> list[CheckOutcome]:
    """Re-key positionally-split outcome groups back into submission order.

    `run_service` zips outcomes onto its own `checks` list, so a runner that
    splits its work (sampled vs refused here; DataFrame vs SQL batch in the UC
    runner, #1179) must return submission order regardless of which group
    evaluated what. Keyed by position and read back by index, so a missing entry
    is a loud `KeyError` rather than a silently short list that would map results
    onto the wrong checks.
    """
    by_position: dict[int, CheckOutcome] = {}
    for positions, outcomes in groups:
        by_position.update(zip(positions, outcomes, strict=True))
    return [by_position[i] for i in range(total)]


def stamp_sampling(outcome: SuiteOutcome, record: dict[str, Any] | None) -> SuiteOutcome:
    """Attach ``record`` to every check outcome in ``outcome`` (no-op if ``None``).

    Runners call this on the group they bounded, so the claim lands on exactly the
    checks it is true for. Unity Catalog is the case that makes this per-group
    rather than per-run: its custom-SQL checks evaluate against a SQL batch over
    the **whole** table while the expectations beside them ran on the sampled
    DataFrame, so only the latter group carries a sampling record (#1179).
    """
    if record is None:
        return outcome
    return SuiteOutcome(
        success=outcome.success,
        checks=[replace(check, sampling=record) for check in outcome.checks],
    )
