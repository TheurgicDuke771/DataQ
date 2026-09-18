"""Scale-aware execution: the sampling spec + the scan guardrail (#595, G-b).

Two layering decisions, both recorded as "leave it" (#1330). The
sample / probe-and-refuse / read policy stays written once per runner: what it
probes differs (flat-file bytes off a stat, UC rows off a COUNT) and so does what
a sample IS (a local stream walk vs a pushed-down TABLESAMPLE), so a shared
skeleton would be a three-line shape with two divergent bodies. And the runners
read `get_settings()` for the caps at the point of use rather than taking them as
constructor arguments — a cap then applies to the next run with nothing to thread
through the registry, and tests override it with the settings fixture.
"""

from __future__ import annotations

import math
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
    parse_whole_number,
)

#: Structural bound only (the memory guardrail is ``RUN_MAX_SCAN_ROWS``) —
#: rejects a nonsensical spec with a 422 at save time.
MAX_SAMPLE_ROWS = 10_000_000


class SamplingConfigError(ValueError):
    """A ``sampling`` block on a run target is missing or malformed (422 at
    suite-save time — a spec that saves is a spec that runs).
    """


class ScanTooLargeError(SafeMonitorError, RuntimeError):
    """The target is larger than the configured scan cap; DataQ refuses to load it."""


class SamplingDrawError(SafeMonitorError, RuntimeError):
    """A sampled read came back in a shape that cannot support a verdict (#595)."""


#: Expectations that measure the batch's row count — against a sampled frame they'd report the
#: SAMPLE as the dataset's size (#595), so they're refused.
ROW_COUNT_EXPECTATION_TYPES: frozenset[str] = frozenset(
    {
        "expect_table_row_count_to_be_between",
        "expect_table_row_count_to_equal",
        "expect_table_row_count_to_equal_other_table",
    }
)

#: Shared by the save-time gate and the run-time refusal, so both show one sentence.
SAMPLING_ROW_COUNT_CONFLICT = (
    "a table row-count expectation cannot run against a sampled dataset — it would "
    "measure the SAMPLE and report it as the dataset's size (a 100k sample of a 5M-row "
    "table reads as 100k rows). Use a volume monitor, which counts the whole dataset "
    "without loading it, or drop the sampling block from the suite's run target."
)


def is_row_count_expectation(expectation_type: str) -> bool:
    """Whether this expectation measures the batch's row count (see the set above)."""
    return expectation_type in ROW_COUNT_EXPECTATION_TYPES


def parse_sample_spec(raw: Any) -> SampleSpec:
    """Validate a target's ``sampling`` block. The absent-block case belongs to
    the caller: `registry._target_sampling` answers it before deciding whether the
    datasource may be sampled at all.
    """
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
        # Known key, inapplicable strategy — silently ignoring would mislead.
        raise SamplingConfigError(
            f"sampling 'seed' applies only to strategy {SAMPLE_RANDOM!r}; "
            f"{SAMPLE_HEAD!r} always reads the first rows in storage order"
        )
    return SampleSpec(strategy=str(strategy), rows=rows, seed=seed)


def _whole_number(value: Any, field: str) -> int | None:
    if value is None:
        return None
    return parse_whole_number(value, what=f"sampling {field}", error=SamplingConfigError)


def sample_row_indices(*, total: int, rows: int, seed: int | None) -> list[int] | None:
    """``rows`` positions drawn uniformly from ``range(total)``, sorted — or ``None``."""
    if total <= 0 or rows >= total:
        return None
    return sorted(random.Random(seed).sample(range(total), rows))  # noqa: S311  # nosec B311


def sampling_record(
    spec: SampleSpec, *, rows: int, total_rows: int | None, sampled: bool
) -> dict[str, Any]:
    """The payload persisted on ``results.sampling`` and surfaced by the read API."""
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


def _enforce_cap(amount: int, *, cap: int, message: str) -> None:
    """The one over-cap refusal (``cap <= 0`` disables) — the three public caps
    differ only in what they measure and what they tell the user to do (#1330).
    """
    if cap > 0 and amount > cap:
        raise ScanTooLargeError(message)


def enforce_row_cap(count: int, *, cap: int, target: str) -> None:
    """Refuse a read of ``count`` rows when it exceeds ``cap`` (``cap <= 0`` disables)."""
    _enforce_cap(
        count,
        cap=cap,
        message=(
            f"{target} has {count:,} rows, over the scan cap of {cap:,}. DataQ refuses to "
            "load it rather than risk an out-of-memory worker: set a sampling strategy on "
            "the suite's run target, narrow the target, or raise RUN_MAX_SCAN_ROWS "
            "deliberately."
        ),
    )


def enforce_byte_cap(size: int, *, cap: int, target: str) -> None:
    """Refuse a read of ``size`` bytes when it exceeds ``cap`` (``cap <= 0`` disables)."""
    _enforce_cap(
        size,
        cap=cap,
        message=(
            f"{target} is {size:,} bytes, over the scan cap of {cap:,}. DataQ refuses to "
            "load it rather than risk an out-of-memory worker: set a sampling strategy on "
            "the suite's run target, target a smaller file, or raise RUN_MAX_SCAN_BYTES "
            "deliberately."
        ),
    )


def enforce_sample_cap(spec: SampleSpec, *, cap: int) -> None:
    """Refuse a *sample* that is itself over the row cap (``cap <= 0`` disables)."""
    _enforce_cap(
        spec.rows,
        cap=cap,
        message=(
            f"the run target's sample of {spec.rows:,} rows is itself over the scan cap of "
            f"{cap:,}. Lower the sample size, or raise RUN_MAX_SCAN_ROWS deliberately."
        ),
    )


# ─────────────────── batch-stream selection (pure, IO-free) ───────────────────
# Both helpers take an iterable of Arrow record batches; peak memory is one
# batch plus the retained rows.


def take_head(batches: Iterable[Any], *, limit: int) -> list[Any]:
    """The first ``limit`` rows across ``batches``; stops consuming at the limit."""
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
        # Checked AFTER taking — a top-of-loop test would pull one extra batch
        # (a wasted range request) before noticing it is done.
        if got >= limit:
            break
    return taken


def take_indices(batches: Iterable[Any], indices: list[int]) -> list[Any]:
    """The rows at the (sorted, ascending) global positions ``indices``."""
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


def _positive_uniform(rng: random.Random) -> float:
    """A draw in the open interval (0, 1) — `log` is undefined at both ends."""
    while True:
        value = rng.random()  # nosec B311
        if value > 0.0:
            return value


#: `exp(log(u) / rows)` rounds to exactly 1.0 for u within ~rows·eps of 1; log1p(-1.0) is -inf.
_MAX_WEIGHT = math.nextafter(1.0, 0.0)


def _skip(rng: random.Random, weight: float) -> int:
    """Rows to skip before the next replacement (Algorithm L's geometric jump)."""
    weight = min(weight, _MAX_WEIGHT)
    return int(math.log(_positive_uniform(rng)) / math.log1p(-weight)) + 1


def _write_slots(held: Any, batch: Any, local: list[int], slots: list[int]) -> Any:
    """Put ``batch``'s ``local`` rows into the reservoir's ``slots``."""
    import pyarrow as pa

    addition = pa.Table.from_batches([batch.take(pa.array(local, type=pa.int64()))])
    base = 0 if held is None else len(held)
    keep = list(range(base))
    for row, slot in enumerate(slots):
        if slot < len(keep):
            keep[slot] = base + row
        else:
            # Slots grow contiguously while the reservoir is still filling.
            keep.append(base + row)
    combined = addition if held is None else pa.concat_tables([held, addition])
    return combined.take(pa.array(keep, type=pa.int64())).combine_chunks()


def reservoir_sample(
    batches: Iterable[Any], *, rows: int, seed: int | None
) -> tuple[list[Any], int]:
    """``rows`` rows drawn uniformly in ONE pass, plus the number of rows walked.

    Vitter's Algorithm L: the reservoir holds the first ``rows`` rows, then each
    later row replaces a random slot with probability ``rows/i`` — reached by
    skipping ahead rather than drawing per row. The rows come back in FILE ORDER
    (callers and row locators read positionally), and the walked count is the
    population as of this very pass, measured by the read that produced the
    sample rather than by an earlier one.
    """
    import pyarrow as pa

    rng = random.Random(seed)  # noqa: S311  # nosec B311
    held: Any = None
    positions: list[int] = []
    walked = 0
    weight = 1.0
    next_take = -1

    for batch in batches:
        offset, count = walked, batch.num_rows
        walked += count
        if count == 0:
            continue
        local: list[int] = []
        slots: list[int] = []

        if rows > len(positions):
            fill = min(rows - len(positions), count)
            local.extend(range(fill))
            slots.extend(range(len(positions), len(positions) + fill))
            positions.extend(range(offset, offset + fill))
            if len(positions) == rows:
                weight = math.exp(math.log(_positive_uniform(rng)) / rows)
                next_take = rows - 1 + _skip(rng, weight)

        while offset <= next_take < offset + count:
            slot = rng.randrange(rows)  # nosec B311
            local.append(next_take - offset)
            slots.append(slot)
            positions[slot] = next_take
            weight *= math.exp(math.log(_positive_uniform(rng)) / rows)
            next_take += _skip(rng, weight)

        if local:
            held = _write_slots(held, batch, local, slots)

    if held is None:
        return [], walked
    order = sorted(range(len(positions)), key=positions.__getitem__)
    ordered = held.take(pa.array(order, type=pa.int64()))
    return list(ordered.combine_chunks().to_batches()), walked


def batches_to_frame(batches: list[Any], *, schema: Any, arrow_backed: bool) -> Any:
    """Concatenate selected Arrow batches into a pandas DataFrame."""
    import pandas as pd
    import pyarrow as pa

    table = pa.Table.from_batches(batches, schema=schema) if batches else schema.empty_table()
    if arrow_backed:
        return table.to_pandas(types_mapper=pd.ArrowDtype)
    return table.to_pandas()


def split_row_count_checks(
    checks: list[CheckSpec],
) -> tuple[list[int], dict[int, CheckOutcome]]:
    """Partition ``checks`` into (runnable positions, refusals keyed by position)."""
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
    """Re-key positionally-split outcome groups back into submission order
    (the 1:1 `run_service` zip; also the UC runner's split, #1179). A missing
    entry is a loud `KeyError`, never a silently short list.
    """
    by_position: dict[int, CheckOutcome] = {}
    for positions, outcomes in groups:
        by_position.update(zip(positions, outcomes, strict=True))
    return [by_position[i] for i in range(total)]


def stamp_sampling(outcome: SuiteOutcome, record: dict[str, Any] | None) -> SuiteOutcome:
    """Attach ``record`` to every check outcome in ``outcome`` (no-op if ``None``)."""
    if record is None:
        return outcome
    return SuiteOutcome(
        success=outcome.success,
        checks=[replace(check, sampling=record) for check in outcome.checks],
    )
