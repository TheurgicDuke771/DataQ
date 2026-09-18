"""Case registry, subprocess measurement and summary statistics.

Every case is measured in a FRESH child process so its peak RSS is attributable
to that case rather than to whatever ran before it in the same interpreter.
"""

from __future__ import annotations

import json
import math
import os
import platform
import resource
import statistics
import subprocess  # nosec B404 - benchmark children + a read-only sha lookup
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: How `--check` treats a metric.
#:   ``strict``  — deterministic counter; any increase fails.
#:   ``band``    — bounded resource (peak RSS); fails past a tolerance.
#:   ``observe`` — recorded, never gated (wall clock on a shared machine).
GATES = ("strict", "band", "observe")


@dataclass(frozen=True)
class Metric:
    name: str
    value: float
    unit: str
    gate: str = "observe"

    def __post_init__(self) -> None:
        if self.gate not in GATES:
            raise ValueError(f"unknown gate {self.gate!r}")


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    datasource: str
    tier: str
    fn: Callable[[], list[Metric]]
    #: Cases sharing a tag can be selected together (``ci`` = the CI subset).
    tags: tuple[str, ...] = ()
    #: Set when the case cannot run here — emitted as a "not measured" row.
    skip_reason: str | None = None
    #: Extra environment for the child process.
    env: dict[str, str] = field(default_factory=dict)
    #: Setup run in the child before the clock starts (seeding, fixture build).
    prepare: Callable[[], None] | None = None


_REGISTRY: dict[str, Case] = {}


def register(case: Case) -> Case:
    if case.id in _REGISTRY:
        raise ValueError(f"duplicate case id {case.id!r}")
    _REGISTRY[case.id] = case
    return case


def load_cases() -> None:
    """Import the case modules for their registration side effects."""
    from backend.scripts.perf import cases_db, cases_flatfile, cases_warehouse  # noqa: F401


def registry() -> dict[str, Case]:
    # Always: importing ONE case module (a test, a direct import) leaves the
    # registry non-empty but incomplete, and an emptiness check would accept it.
    load_cases()
    return _REGISTRY


def select(
    *,
    families: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    ids: Iterable[str] | None = None,
) -> list[Case]:
    cases = list(registry().values())
    if ids:
        wanted = set(ids)
        cases = [c for c in cases if c.id in wanted]
    if families:
        fams = set(families)
        cases = [c for c in cases if c.family in fams]
    if tags:
        tagset = set(tags)
        cases = [c for c in cases if tagset & set(c.tags)]
    return cases


# ────────────────────────────── measurement ──────────────────────────────


def peak_rss_mib() -> float:
    """This process's peak resident set size.

    ``ru_maxrss`` is bytes on macOS and kibibytes on Linux — the one platform
    difference that would otherwise report a 1024x wrong number in silence.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def _spin() -> float:
    total = 0.0
    for i in range(1, 2_000_001):
        total += math.sqrt(i) / (i + 1.0)
    return total


def calibration_seconds(rounds: int = 3) -> float:
    """A fixed CPU-bound micro-benchmark, best-of-N.

    Wall clock alone is not comparable across machines; wall / calibration is,
    to the extent the measured work is CPU-bound in the same way.
    """
    best = float("inf")
    for _ in range(rounds):
        started = time.perf_counter()
        _spin()
        best = min(best, time.perf_counter() - started)
    return best


def execute(case: Case) -> dict[str, Any]:
    """Run one case IN THIS PROCESS and return its raw measurement payload."""
    if case.prepare is not None:
        case.prepare()
    calib = calibration_seconds()
    started = time.perf_counter()
    metrics = list(case.fn())
    wall = time.perf_counter() - started
    metrics.append(Metric("wall_s", wall, "s", "observe"))
    metrics.append(Metric("wall_calibrated", wall / calib if calib else 0.0, "ratio", "observe"))
    metrics.append(Metric("peak_rss_mib", peak_rss_mib(), "MiB", "band"))
    metrics.append(Metric("calibration_s", calib, "s", "observe"))
    return {
        "case": case.id,
        "metrics": [
            {"name": m.name, "value": m.value, "unit": m.unit, "gate": m.gate} for m in metrics
        ],
    }


def repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def child_command(case_id: str) -> list[str]:
    return [sys.executable, "-m", "backend.scripts.perf_baseline", "exec", "--case", case_id]


def child_env(case: Case) -> dict[str, str]:
    env = dict(os.environ)
    env.update(case.env)
    env.setdefault("PYTHONPATH", repo_root())
    return env


def run_in_subprocess(case: Case, *, timeout: float = 3600.0) -> dict[str, Any]:
    """Run `case` in a fresh interpreter and return its measurement payload."""
    proc = subprocess.run(  # noqa: S603  # nosec B603 - this interpreter, a registered case id
        child_command(case.id),
        capture_output=True,
        text=True,
        env=child_env(case),
        cwd=repo_root(),
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"case {case.id} failed (rc={proc.returncode}):\n{proc.stderr[-4000:]}")
    return last_json_line(proc.stdout, case.id)


def last_json_line(stdout: str, case_id: str) -> dict[str, Any]:
    for line in reversed(stdout.strip().splitlines()):
        if line.startswith("{"):
            payload: dict[str, Any] = json.loads(line)
            return payload
    raise RuntimeError(f"case {case_id} produced no measurement line:\n{stdout[-2000:]}")


# ────────────────────────────── aggregation ──────────────────────────────


def summarise(values: list[float]) -> dict[str, float]:
    """Median, coefficient of variation and range over repeated measurements."""
    median = statistics.median(values)
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "value": median,
        "mean": mean,
        "cov": (stdev / mean) if mean else 0.0,
        "min": min(values),
        "max": max(values),
        "n": float(len(values)),
    }


def _total_memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):  # pragma: no cover — platform-dependent
        return None


def rig() -> dict[str, Any]:
    """The machine these numbers were taken on — a dev box, NOT the prod rig."""
    total = _total_memory_bytes()
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_gib": round(total / (1024**3), 1) if total else None,
        "is_prod_parity": False,
        "prod_rig": "1 CPU / 2 GiB container, celery prefork concurrency 4",
    }


def git_sha() -> str:
    out = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=repo_root(),
        check=False,
    )
    return out.stdout.strip() or "unknown"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
