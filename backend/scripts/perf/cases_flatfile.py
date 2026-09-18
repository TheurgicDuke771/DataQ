"""Flat-file cases: run scaling, concurrent peak, batch resolution, profiler.

Everything here drives the REAL code paths — `FlatFileCheckRunner.run_checks`,
`flatfile.resolve_batch_file`, `profile_service.profile_file` — with only the
object-store seams pointed at a local file (see `datagen.local_store`).
"""

from __future__ import annotations

import concurrent.futures
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from backend.scripts.perf import datagen
from backend.scripts.perf.harness import Case, Metric, register, spawn

ROW_TIERS = {"100k": 100_000, "1m": 1_000_000, "5m": 5_000_000}
#: Sampled tiers draw this many rows, the shipped default sample size.
SAMPLE_ROWS = 100_000
#: The shipped scan caps exist to REFUSE the reads these cases measure.
_UNCAPPED = {"RUN_MAX_SCAN_BYTES": "0", "RUN_MAX_SCAN_ROWS": "0"}
#: The seams read local files, so no credential is involved anywhere in this module.
_NO_CREDENTIAL = "local-file-seam"  # nosec B105


def _check_specs(count: int) -> list[Any]:
    from backend.app.datasources.base import CheckSpec

    shapes: list[tuple[str, dict[str, Any]]] = [
        ("expect_column_values_to_not_be_null", {"column": "order_id"}),
        ("expect_column_values_to_not_be_null", {"column": "sku_id"}),
        ("expect_column_values_to_be_between", {"column": "qty", "min_value": 1, "max_value": 20}),
        (
            "expect_column_values_to_be_between",
            {"column": "unit_price", "min_value": 0, "max_value": 1000},
        ),
        ("expect_column_values_to_be_unique", {"column": "line_id"}),
    ]
    columns = ["order_id", "sku_id", "qty", "unit_price", "line_id"]
    specs = []
    for i in range(count):
        expectation, kwargs = shapes[i % len(shapes)]
        kwargs = dict(kwargs)
        if i >= len(shapes) and "column" in kwargs:
            kwargs["column"] = columns[(i // len(shapes)) % len(columns)]
        specs.append(CheckSpec(expectation_type=expectation, kwargs=kwargs))
    return specs


def _sample_spec(mode: str) -> Any:
    from backend.app.datasources.base import SampleSpec

    if mode == "full":
        return None
    return SampleSpec(strategy=mode, rows=SAMPLE_ROWS, seed=42)


@contextmanager
def _measured_frame() -> Iterator[list[int]]:
    """Record the row count of whatever frame the runner actually loaded.

    Without this the `full`-mode row count would be the tier constant handed in
    by the case — a gate on a number that cannot move, which is worse than no
    gate because it reads like coverage.
    """
    from backend.app.datasources import flatfile

    seen: list[int] = []
    originals = {
        name: getattr(flatfile, name) for name in ("read_dataframe", "read_sampled_dataframe")
    }

    def wrap(name: str) -> Any:
        original = originals[name]

        def measured(**kwargs: Any) -> Any:
            result = original(**kwargs)
            frame = result[0] if isinstance(result, tuple) else result
            seen.append(len(frame))
            return result

        return measured

    for name in originals:
        setattr(flatfile, name, wrap(name))
    try:
        yield seen
    finally:
        for name, original in originals.items():
            setattr(flatfile, name, original)


def _run_flatfile(*, fmt: str, rows: int, checks: int, mode: str) -> list[Metric]:
    import time

    from backend.app.datasources.flatfile import FlatFileCheckRunner

    path = str(datagen.dataset(rows, fmt))
    specs = _check_specs(checks)
    with datagen.local_store() as counters, _measured_frame() as frame_rows:
        runner = FlatFileCheckRunner(
            conn_type="s3", config={}, secret=_NO_CREDENTIAL, sampling=_sample_spec(mode)
        )
        started = time.perf_counter()
        outcome = runner.run_checks(table=path, schema=None, checks=specs)
        elapsed = time.perf_counter() - started

    sampling = next((c.sampling for c in outcome.checks if c.sampling), None)
    rows_seen = sum(frame_rows)
    return [
        Metric("run_wall_s", elapsed, "s", "observe"),
        Metric("rows_per_s", rows_seen / elapsed if elapsed else 0.0, "rows/s", "observe"),
        Metric("rows_read", float(rows_seen), "rows", "exact"),
        Metric("frames_loaded", float(len(frame_rows)), "frames", "exact"),
        Metric("checks_evaluated", float(len(outcome.checks)), "checks", "exact"),
        Metric("store_calls", float(counters.calls), "calls", "strict"),
        Metric("store_bytes_read", float(counters.bytes_read), "bytes", "band"),
        Metric("sampled", 1.0 if sampling and sampling.get("sampled") else 0.0, "bool", "exact"),
    ]


def _register_run_scaling() -> None:
    for fmt in ("csv", "parquet"):
        for tier, rows in ROW_TIERS.items():
            for checks in (5, 25):
                for mode in ("full", "head", "random"):
                    case_id = f"flatfile.{fmt}.{tier}.{checks}checks.{mode}"
                    tags: tuple[str, ...] = ("full",)
                    if tier == "100k" and checks == 5 and mode in ("full", "head"):
                        tags = ("full", "ci")
                    register(
                        Case(
                            id=case_id,
                            family="flatfile_run",
                            datasource=f"flatfile_{fmt}",
                            tier=f"{tier}x{checks}checks/{mode}",
                            fn=_bind_run(fmt=fmt, rows=rows, checks=checks, mode=mode),
                            tags=tags,
                            env=dict(_UNCAPPED),
                        )
                    )


def _bind_run(*, fmt: str, rows: int, checks: int, mode: str) -> Any:
    def run() -> list[Metric]:
        return _run_flatfile(fmt=fmt, rows=rows, checks=checks, mode=mode)

    return run


# ───────────────────────────── concurrent peak ─────────────────────────────


def _concurrent(children: int, case_id: str) -> list[Metric]:
    """N overlapping runs in separate processes — the concurrent-peak question.

    Each child reports its own peak RSS; the sum is what a 4-way prefork worker
    would have to hold at once. Children are spawned BY ID, so this module never
    needs to read the registry it is itself registering into.
    """
    import time

    # Materialise the fixture BEFORE the children start: generating it N times
    # in parallel would measure fixture generation, not the run.
    datagen.dataset(ROW_TIERS["1m"], "csv")
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=children) as pool:
        payloads = list(pool.map(lambda _: spawn(case_id, env=_UNCAPPED), range(children)))
    elapsed = time.perf_counter() - started

    peaks = [_metric_value(p, "peak_rss_mib") for p in payloads]
    walls = [_metric_value(p, "run_wall_s") for p in payloads]
    return [
        Metric("children", float(children), "processes", "exact"),
        Metric("child_peak_rss_sum_mib", sum(peaks), "MiB", "band"),
        Metric("child_peak_rss_max_mib", max(peaks), "MiB", "band"),
        Metric("concurrent_wall_s", elapsed, "s", "observe"),
        Metric("slowest_child_wall_s", max(walls), "s", "observe"),
    ]


def _metric_value(payload: dict[str, Any], name: str) -> float:
    for metric in payload["metrics"]:
        if metric["name"] == name:
            return float(metric["value"])
    raise KeyError(f"{name} missing from {payload['case']}")


def _register_concurrent() -> None:
    base = "flatfile.csv.1m.5checks.full"
    for children in (2, 4):
        register(
            Case(
                id=f"concurrent.csv.1m.x{children}",
                family="flatfile_concurrent",
                datasource="flatfile_csv",
                tier=f"{children} overlapping 1M-row runs",
                fn=_bind_concurrent(children, base),
                tags=("full",),
                env=dict(_UNCAPPED),
            )
        )


def _bind_concurrent(children: int, case_id: str) -> Any:
    def run() -> list[Metric]:
        return _concurrent(children, case_id)

    return run


# ──────────────────────────── batch resolution ────────────────────────────


def _batch_resolution(objects: int) -> list[Metric]:
    import time

    from backend.app.datasources import flatfile

    listing = [
        flatfile.FileRef(
            path=f"landing/orders/orders_2026{(i % 12) + 1:02d}{(i % 28) + 1:02d}_{i:07d}.csv",
            last_modified=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for i in range(objects)
    ]
    scanned = {"n": 0}

    def fake_iter_files(*, conn_type: str, config: Any, prefix: str, secret: str) -> Any:
        for ref in listing:
            scanned["n"] += 1
            yield ref

    original = flatfile.iter_files
    flatfile.iter_files = fake_iter_files
    try:
        started = time.perf_counter()
        path = flatfile.resolve_batch_file(
            conn_type="s3",
            config={},
            secret=_NO_CREDENTIAL,
            prefix="landing/orders/",
            pattern=r"orders_(?P<batch>\d{8})_\d+\.csv",
            strategy="latest",
        )
        elapsed = time.perf_counter() - started
    finally:
        flatfile.iter_files = original

    return [
        Metric("objects_listed", float(scanned["n"]), "objects", "exact"),
        Metric("resolve_wall_s", elapsed, "s", "observe"),
        Metric("objects_per_s", objects / elapsed if elapsed else 0.0, "objects/s", "observe"),
        Metric("resolved", 1.0 if path else 0.0, "bool", "exact"),
    ]


def _register_batch() -> None:
    for objects in (1_000, 10_000, 100_000):
        tier = f"{objects // 1000}k" if objects >= 1000 else str(objects)
        register(
            Case(
                id=f"batch_resolve.{tier}",
                family="batch_resolution",
                datasource="flatfile_csv",
                tier=f"{objects} object keys",
                fn=_bind_batch(objects),
                tags=("full", "ci") if objects == 1_000 else ("full",),
            )
        )


def _bind_batch(objects: int) -> Any:
    def run() -> list[Metric]:
        return _batch_resolution(objects)

    return run


# ──────────────────────────────── profiler ────────────────────────────────


class _StaticSecretStore:
    def get(self, name: str) -> str:
        return _NO_CREDENTIAL

    def set(self, name: str, value: str) -> None:  # pragma: no cover — never called
        raise NotImplementedError

    def delete(self, name: str) -> None:  # pragma: no cover — never called
        raise NotImplementedError


def _profile_wide(columns: int, fmt: str, rows: int = 200_000) -> list[Metric]:
    import time

    from backend.app.db.models import Connection
    from backend.app.services import profile_service

    path = str(datagen.wide_dataset(rows, columns, fmt))
    names = [f"col_{i:03d}" for i in range(columns)]
    connection = Connection(
        id=uuid.uuid4(), name="perf", type="s3", env="dev", config={}, secret_ref=_NO_CREDENTIAL
    )
    with datagen.local_store() as counters:
        started = time.perf_counter()
        result = profile_service.profile_file(
            connection,
            path=path,
            file_format=fmt,
            columns=names,
            top_n=10,
            secret_store=_StaticSecretStore(),
        )
        elapsed = time.perf_counter() - started

    return [
        Metric("profile_wall_s", elapsed, "s", "observe"),
        Metric("columns_profiled", float(len(result.columns)), "columns", "exact"),
        Metric("store_calls", float(counters.calls), "calls", "strict"),
        Metric("store_bytes_read", float(counters.bytes_read), "bytes", "band"),
    ]


def _register_profiler() -> None:
    for fmt in ("csv", "parquet"):
        for columns in (50, 200):
            register(
                Case(
                    id=f"profiler.{fmt}.{columns}col",
                    family="profiler",
                    datasource=f"flatfile_{fmt}",
                    tier=f"{columns} columns",
                    fn=_bind_profile(columns, fmt),
                    tags=("full", "ci") if (fmt == "parquet" and columns == 50) else ("full",),
                )
            )


def _bind_profile(columns: int, fmt: str) -> Any:
    def run() -> list[Metric]:
        return _profile_wide(columns, fmt)

    return run


_register_run_scaling()
_register_concurrent()
_register_batch()
_register_profiler()
