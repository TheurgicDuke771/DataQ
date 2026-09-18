"""Synthetic fixtures + a local-file stand-in for the object-store seams.

The flat-file runner reaches the store through four module-level seams
(`file_stat` / `object_size` / `download_bytes` / `read_range`). Pointing those
at a local file leaves the runner, the guardrail, the sampling readers and the
profiler executing for real and stands in only for the network — the same
substitution the v1.1 scale-aware-execution campaign used.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: 6-column order-lines, the shape every prior campaign in perf-baseline.md used.
COLUMNS = ("line_id", "order_id", "sku_id", "qty", "unit_price", "line_ts")


def data_dir() -> Path:
    root = Path(os.environ.get("PERF_DATA_DIR", Path.home() / ".cache" / "dataq-perf"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _frame(rows: int, *, seed: int = 7) -> Any:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    start = np.datetime64("2026-01-01T00:00:00")
    return pd.DataFrame(
        {
            "line_id": np.arange(rows, dtype="int64"),
            "order_id": rng.integers(0, max(rows // 4, 1), size=rows, dtype="int64"),
            "sku_id": rng.integers(0, 10_000, size=rows, dtype="int64"),
            "qty": rng.integers(1, 21, size=rows, dtype="int64"),
            "unit_price": rng.uniform(1.0, 500.0, size=rows).round(2),
            "line_ts": start + rng.integers(0, 86_400 * 90, size=rows) * np.timedelta64(1, "s"),
        }
    )


def _wide_frame(rows: int, columns: int, *, seed: int = 11) -> Any:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    data = {f"col_{i:03d}": rng.integers(0, 1000, size=rows, dtype="int64") for i in range(columns)}
    return pd.DataFrame(data)


def dataset(rows: int, fmt: str) -> Path:
    """Path to the order-lines fixture of `rows` rows, generating it on first use."""
    path = data_dir() / f"order_lines_{rows}.{fmt}"
    if path.exists():
        return path
    frame = _frame(rows)
    _write(frame, path, fmt)
    return path


def wide_dataset(rows: int, columns: int, fmt: str) -> Path:
    path = data_dir() / f"wide_{columns}c_{rows}.{fmt}"
    if path.exists():
        return path
    _write(_wide_frame(rows, columns), path, fmt)
    return path


def _write(frame: Any, path: Path, fmt: str) -> None:
    # Per-process temp name: concurrent cases generate the same fixture at once.
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.partial")
    if fmt == "csv":
        frame.to_csv(tmp, index=False)
    else:
        frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


# ────────────────────────── local-file store seams ──────────────────────────


@dataclass
class StoreCounters:
    """What the measured work asked the store for — deterministic, so a change
    in read shape (an extra round-trip, a full read where a range was enough)
    is gateable where wall clock is not.
    """

    calls: int = 0
    bytes_read: int = 0


def _local_file_stat(*, path: str, **_: Any) -> Any:
    from backend.app.datasources.flatfile import FileStat

    _COUNTERS.calls += 1
    stat = os.stat(path)
    return FileStat(last_modified=datetime.fromtimestamp(stat.st_mtime, UTC), size=stat.st_size)


def _local_object_size(*, path: str, **_: Any) -> int:
    _COUNTERS.calls += 1
    return os.stat(path).st_size


def _local_download_bytes(*, path: str, **_: Any) -> bytes:
    _COUNTERS.calls += 1
    with open(path, "rb") as handle:
        raw = handle.read()
    _COUNTERS.bytes_read += len(raw)
    return raw


def _local_read_range(*, path: str, start: int, length: int, **_: Any) -> bytes:
    if length <= 0:
        return b""
    _COUNTERS.calls += 1
    with open(path, "rb") as handle:
        handle.seek(start)
        raw = handle.read(length)
    _COUNTERS.bytes_read += len(raw)
    return raw


_COUNTERS = StoreCounters()

_SEAMS: dict[str, Any] = {
    "file_stat": _local_file_stat,
    "object_size": _local_object_size,
    "download_bytes": _local_download_bytes,
    "read_range": _local_read_range,
}


class SeamMissingError(RuntimeError):
    """A seam this harness stands in for is not where it used to be."""


@contextmanager
def local_store() -> Iterator[StoreCounters]:
    """Point the object-store seams at the local filesystem for the duration.

    The stubs take ``**_`` on purpose: they stand in for live functions whose
    signature is the app's to change (a `session` argument arrived that way),
    and a benchmark must not be the reason an unrelated change goes red.
    A seam that has *moved*, though, is fatal — silently skipping it would let
    the case reach the real boto3 client with a fake credential.
    """
    from backend.app.datasources import flatfile

    global _COUNTERS
    _COUNTERS = StoreCounters()
    missing = [name for name in _SEAMS if not hasattr(flatfile, name)]
    if missing:
        # Before importing anything downstream: a moved seam breaks its importers
        # too, and that ImportError would bury the reason.
        raise SeamMissingError(
            f"flatfile no longer exposes {missing}; the perf harness stands in for these seams "
            "and would otherwise open a real store connection"
        )

    from backend.app.services import profile_service

    saved: list[tuple[Any, str, Any]] = []
    for module in (flatfile, profile_service):
        for name, impl in _SEAMS.items():
            if hasattr(module, name):
                saved.append((module, name, getattr(module, name)))
                setattr(module, name, impl)
    try:
        yield _COUNTERS
    finally:
        for module, name, original in saved:
            setattr(module, name, original)
