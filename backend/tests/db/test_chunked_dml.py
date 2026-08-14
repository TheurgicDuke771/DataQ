"""Pure-unit tests for the shared bounded-batch DML loop (#323 review F4/F6).

No real DB needed: a fake `Session` stands in so the loop's own control flow —
termination, guard validation, the `rowcount or 0` defense, and the `on_batch`
progress callback — can be exercised deterministically, including the one
shape (a driver returning `rowcount=-1`) a real Postgres session can never
produce.
"""

from typing import Any

import pytest

from backend.app.db.chunked_dml import chunked_dml


class _FakeCursorResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    """Replays a scripted sequence of rowcounts, one per `execute()` call."""

    def __init__(self, rowcounts: list[int]) -> None:
        self._rowcounts = list(rowcounts)
        self.execute_count = 0
        self.commit_count = 0

    def execute(self, _statement: Any) -> _FakeCursorResult:
        self.execute_count += 1
        rowcount = self._rowcounts.pop(0)
        return _FakeCursorResult(rowcount)

    def commit(self) -> None:
        self.commit_count += 1


def test_chunk_size_zero_raises_before_any_query(monkeypatch: Any) -> None:
    """#323 review F4(a): the pre-fix `affected < chunk_size` exit condition
    infinite-looped on `chunk_size=0` (`0 < 0` is False, so it never breaks).
    The fix validates up front — no query is ever issued."""
    session = _FakeSession([])  # would raise IndexError if execute() were ever called

    with pytest.raises(ValueError, match="chunk_size must be >= 1"):
        chunked_dml(session, lambda: object(), chunk_size=0)  # type: ignore[arg-type, return-value]

    assert session.execute_count == 0


def test_negative_chunk_size_also_raises() -> None:
    session = _FakeSession([])

    with pytest.raises(ValueError, match="chunk_size must be >= 1"):
        chunked_dml(session, lambda: object(), chunk_size=-1)  # type: ignore[arg-type, return-value]


def test_exits_on_zero_not_on_partial_batch(monkeypatch: Any) -> None:
    """#323 review F4(b): a batch that returns exactly `chunk_size` (the
    candidate count is an exact multiple) or a batch shrunk by a concurrent
    delete must NOT be mistaken for "done" — only a truly empty batch ends
    the loop. Scripted as 3, 3, 1, 0: three non-empty batches (the middle one
    full-sized) followed by the confirming empty round."""
    session = _FakeSession([3, 3, 1, 0])

    total = chunked_dml(session, lambda: object(), chunk_size=3)  # type: ignore[arg-type, return-value]

    assert total == 7
    assert session.execute_count == 4  # the trailing empty round is required
    assert session.commit_count == 4


def test_negative_rowcount_is_treated_as_zero(monkeypatch: Any) -> None:
    """#323 review F6: `asset_service.sweep_orphan_assets`'s own loop guards
    against a driver returning `rowcount=-1` ("unknown"); the extracted
    helper must carry the same guard, not just the id-tracking `run_service`
    side happened to not need it for. A -1 must neither corrupt the running
    total nor be mistaken for a non-empty batch (which would spin forever)."""
    session = _FakeSession([5, -1])

    total = chunked_dml(session, lambda: object(), chunk_size=5)  # type: ignore[arg-type, return-value]

    assert total == 5  # the -1 batch contributed 0, not -1
    assert session.execute_count == 2


def test_on_batch_receives_each_batchs_affected_count() -> None:
    """#323 review M1: callers use this to keep a running total that stays
    accurate even if a LATER batch raises — proven here by checking the
    callback fires with the right value on every batch, not just the total
    at the end."""
    session = _FakeSession([2, 2, 1, 0])
    seen: list[int] = []

    total = chunked_dml(
        session, lambda: object(), chunk_size=2, on_batch=seen.append  # type: ignore[arg-type, return-value]
    )

    assert total == 5
    assert seen == [2, 2, 1, 0]


def test_on_batch_sees_partial_progress_if_a_later_batch_raises() -> None:
    """The concrete M1 scenario: batch 3 raises after batches 1-2 already
    committed. A caller accumulating via `on_batch` still has the correct
    partial total for logging, even though `chunked_dml` itself never
    returns."""
    session = _FakeSession([2, 2])  # only 2 scripted; the 3rd execute() raises IndexError
    seen: list[int] = []

    with pytest.raises(IndexError):
        chunked_dml(session, lambda: object(), chunk_size=2, on_batch=seen.append)  # type: ignore[arg-type, return-value]

    assert seen == [2, 2]  # the two committed batches were still reported
    assert session.commit_count == 2  # both prior batches really did commit
