"""Shared bounded-batch DML loop for beat-driven retention sweeps (#323)."""

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
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size!r}")
    total = 0
    while True:
        result = cast(CursorResult[Any], session.execute(build_statement()))
        # max(..., 0), not just `or 0`: -1 (a DB-API driver's "rowcount unknown" sentinel) is
        # truthy.
        affected = max(result.rowcount or 0, 0)
        session.commit()
        total += affected
        if on_batch is not None:
            on_batch(affected)
        if affected == 0:
            break
    return total
