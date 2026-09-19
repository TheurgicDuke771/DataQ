"""The worker memory admission budget (#1998).

Against REAL Redis, not a double: the whole guarantee is that the reserve is one atomic
server-side script, and a Python fake with a lock would prove its own lock instead.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest

from backend.app.core import memory_budget
from backend.app.core.config import get_settings
from backend.app.core.memory_budget import MemoryBudget

MiB = 1024 * 1024


def _client() -> Any:
    import redis

    return redis.from_url(get_settings().redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)


def _redis_available() -> bool:
    try:
        _client().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(), reason="redis not reachable (docker compose up -d redis)"
)


@pytest.fixture
def budget() -> MemoryBudget:
    return MemoryBudget(
        _client(),
        budget_bytes=1000 * MiB,
        lease_seconds=60,
        scope=f"test-{uuid.uuid4().hex}",
    )


def test_a_run_that_fits_is_admitted_and_its_bytes_are_counted(budget: MemoryBudget) -> None:
    outcome = budget.reserve("run-a", 400 * MiB)

    assert outcome.admitted is True
    assert outcome.used_bytes == 400 * MiB
    assert outcome.degraded is False


def test_a_second_run_over_the_budget_is_refused_until_the_first_releases(
    budget: MemoryBudget,
) -> None:
    budget.reserve("run-a", 700 * MiB)

    refused = budget.reserve("run-b", 700 * MiB)
    assert refused.admitted is False
    assert refused.used_bytes == 700 * MiB

    budget.release("run-a")
    assert budget.reserve("run-b", 700 * MiB).admitted is True


def test_a_run_larger_than_the_whole_budget_still_runs_alone(budget: MemoryBudget) -> None:
    """Otherwise it can never be admitted and waits out its budget on every attempt."""
    assert budget.reserve("giant", 5000 * MiB).admitted is True
    assert budget.reserve("run-b", 1).admitted is False


def test_concurrent_reservations_cannot_both_exceed_the_budget(budget: MemoryBudget) -> None:
    """Two prefork children reserving at the same instant. Mutation check: replace the Lua
    script with a read-then-write pair and this goes red.
    """
    results: list[bool] = []
    gate = threading.Barrier(8)

    def _attempt(n: int) -> None:
        gate.wait()
        results.append(budget.reserve(f"run-{n}", 600 * MiB).admitted)

    threads = [threading.Thread(target=_attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 600 MiB each into a 1000 MiB budget: exactly one can hold it at a time.
    assert sum(results) == 1, results


def test_an_expired_lease_frees_the_budget_without_a_release(budget: MemoryBudget) -> None:
    """The OOM case: a SIGKILLed child never runs its own release, so the LEASE is what
    frees the budget. Modelled by a zero-length lease rather than by sleeping.
    """
    dead = MemoryBudget(
        _client(), budget_bytes=budget.budget_bytes, lease_seconds=0, scope="shared-scope-1998"
    )
    live = MemoryBudget(
        _client(), budget_bytes=budget.budget_bytes, lease_seconds=60, scope="shared-scope-1998"
    )
    dead.release("victim")
    live.release("survivor")

    assert dead.reserve("victim", 900 * MiB).admitted is True
    # Its lease is already in the past, so the next attempt evicts it and fits.
    admitted = live.reserve("survivor", 900 * MiB)
    live.release("survivor")

    assert admitted.admitted is True
    assert admitted.used_bytes == 900 * MiB


def test_an_unreachable_store_admits_and_says_it_was_degraded() -> None:
    """Fail-open, mirroring the rate limiter (ADR 0035): a budget outage must not stop DataQ
    running checks. `used_bytes` is None — unknown, never 0.
    """
    import redis

    unreachable = MemoryBudget(
        redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.05, socket_timeout=0.05),
        budget_bytes=1000 * MiB,
        lease_seconds=60,
        scope="unreachable",
    )

    outcome = unreachable.reserve("run-a", 10_000 * MiB)

    assert outcome.admitted is True
    assert outcome.degraded is True
    assert outcome.used_bytes is None


def test_a_zero_budget_switches_admission_off_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_ADMISSION_BUDGET_BYTES", "0")
    get_settings.cache_clear()
    memory_budget.reset_memory_budget_state()
    try:
        assert memory_budget.get_memory_budget() is None
    finally:
        get_settings.cache_clear()
        memory_budget.reset_memory_budget_state()
