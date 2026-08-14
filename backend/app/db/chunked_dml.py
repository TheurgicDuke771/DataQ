"""Shared bounded-batch DML loop for beat-driven retention sweeps (#323).

Both `run_service.purge_expired_sample_failures` and
`asset_service.sweep_orphan_assets` chunk a large candidate set into bounded,
individually-committed UPDATE/DELETE batches rather than one unbounded
statement — a catch-up run (first enable, long outage, bulk source removal)
never holds one long transaction against concurrent writers. This was two
independently hand-rolled copies that had already diverged (#323 review
finding F6 — the asset-sweep copy dropped a `rowcount or 0` guard a `-1`
rowcount driver would need) before either shipped; `chunked_dml` is the one
shared loop, and callers only supply the statement to run per batch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql import Executable

# Shared default across every chunked sweep — no evidence any sweep needs a
# different size, so one convention beats several arbitrary ones.
CHUNK_SIZE = 500


def chunked_dml(
    session: Session,
    build_statement: Callable[[], Executable],
    *,
    chunk_size: int = CHUNK_SIZE,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Execute `build_statement()` repeatedly, committing each batch, until a
    batch affects zero rows. Returns the total affected-row count.

    `build_statement` must build a FRESH statement each call (a new
    ``LIMIT chunk_size`` candidate-selection subquery) — reusing a cached
    statement object across calls would replay the exact same LIMIT-ed id set
    forever once those rows had already been updated/deleted out of the
    candidate predicate. The caller's own UPDATE/DELETE WHERE should repeat
    its full predicate — not just ``id IN (subquery)`` — so a concurrent
    transaction's EPQ (evaluate-plan-qual) recheck re-validates the whole
    guard, not only the id set (#323 review finding F5): the id-subquery is
    an optimizer hint to pick `chunk_size` candidate rows, not the sole
    correctness guard on what the statement is allowed to touch.

    Termination relies on the caller's predicate being monotonically
    self-excluding: each batch's UPDATE/DELETE must make its own matched rows
    stop matching that SAME predicate (the classic case is a `*_purged_at IS
    NULL` guard the batch itself sets, or a DELETE that removes the row
    outright) — otherwise this loops forever re-selecting the same backlog.

    Exits on ``affected == 0`` rather than ``affected < chunk_size`` (#323
    review finding F4): the latter has two real failure modes — a
    `chunk_size` that evenly divides the candidate count never sees a partial
    batch and needs the same trailing empty round anyway, and a concurrent
    deletion of already-selected rows (e.g. a cascading suite delete) can
    shrink one batch below `chunk_size` while a real backlog remains,
    silently stranding it. The one-batch-larger cost is cheap once the
    candidate predicate is index-supported (the whole point of the #323
    partial indexes).

    ``chunk_size >= 1`` is enforced here — a caller-supplied 0 would
    otherwise match nothing on every batch (``LIMIT 0``) and return 0 with no
    rows touched and no error: a silent no-op sweep rather than a loud
    misconfiguration.

    ``max(rowcount or 0, 0)``: some DB-API drivers return -1 for "unknown
    rowcount" on certain statement shapes; a bare ``rowcount or 0`` does NOT
    catch that (-1 is truthy), so it alone would leave a negative total that
    corrupts both the running sum and the ``affected == 0`` termination
    check — the ``max(..., 0)`` floor is required, not decorative.

    ``on_batch``, if given, is called with each batch's affected-row count
    immediately after that batch commits — the caller's way to keep a
    running total that stays accurate even if a LATER batch raises (#323
    review finding M1: without this, a mid-sweep failure would leave already
    -committed purges with no accounting anywhere, since the total this
    function would otherwise return is lost along with the exception).
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size!r}")
    total = 0
    while True:
        result = cast(CursorResult[Any], session.execute(build_statement()))
        # max(..., 0), not just `or 0`: -1 (a DB-API driver's "rowcount
        # unknown" sentinel) is truthy, so a bare `or 0` leaves it at -1 and
        # both corrupts the running total and defeats the `affected == 0`
        # termination check below (a caught-by-this-PR's-own-tests gap in
        # the guard shape #323 review F6 named — `or 0` alone stops None/0,
        # not a negative value).
        affected = max(result.rowcount or 0, 0)
        session.commit()
        total += affected
        if on_batch is not None:
            on_batch(affected)
        if affected == 0:
            break
    return total
