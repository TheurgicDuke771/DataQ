"""The catalog's expectations executed for real through the shared `gx_runner` (#1509).

The contract test beside this one proves a catalog entry CONSTRUCTS under the pinned GX. That is
not the same as running: GX registers metric providers per execution engine, so an expectation can
construct fine and then find no provider on the batch its datasource builds. These tests therefore
run every entry twice — once on a pandas batch (the flat-file / Iceberg / Unity-Catalog shape) and
once on a SQLAlchemy batch (the Snowflake shape, stood up on sqlite: providers are registered
against the engine CLASS, so dialect is irrelevant to whether one exists).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import great_expectations as gx
import pandas as pd
import pytest
import sqlalchemy as sa

from backend.app.datasources import flatfile
from backend.app.datasources.base import CheckOutcome, CheckSpec
from backend.app.datasources.gx_runner import run_expectations

_FRAME = pd.DataFrame(
    {
        "id": [1, 2, 3, 4, 5],
        "status": ["new", "new", "shipped", "shipped", "cancelled"],
        "started_on": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"],
        "amount": [10, 20, 30, 40, 50],
        # One null in five rows → unexpected_percent 20.0 on a not-null check.
        "note": ["a", None, "c", "d", "e"],
    }
)


def _pandas_outcomes(specs: list[CheckSpec], monkeypatch: pytest.MonkeyPatch) -> list[CheckOutcome]:
    monkeypatch.setattr(flatfile, "read_dataframe", lambda **k: _FRAME)
    monkeypatch.setattr(flatfile, "file_stat", lambda **k: flatfile.FileStat(None, 4096))
    runner = flatfile.FlatFileCheckRunner(conn_type="s3", config={}, secret="x")
    return runner.run_checks(table="data/orders.csv", schema=None, checks=specs).checks


def _sql_outcomes(specs: list[CheckSpec], tmp_path: Path) -> list[CheckOutcome]:
    db = tmp_path / "orders.db"
    url = f"sqlite:///{db}"
    _FRAME.to_sql("orders", sa.create_engine(url), index=False)
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_sqlite(name="sq", connection_string=url)
    asset = source.add_table_asset(name="orders", table_name="orders")
    batch_definition = asset.add_batch_definition_whole_table("bd")
    return run_expectations(
        context, batch_definition=batch_definition, checks=specs, name="catalog"
    ).checks


def _unexpected_percent(outcome: CheckOutcome) -> Any:
    return (outcome.sample_failures or {}).get("unexpected_percent")


# ───────────────────────────── `mostly` tolerance ─────────────────────────────

_NOT_NULL = "expect_column_values_to_not_be_null"


def test_mostly_moves_gx_success_without_moving_the_banded_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADR-0016 claim, executed rather than asserted: `mostly` changes whether GX calls the
    check successful and leaves `unexpected_percent` — the number the severity bands read
    (`services/severity.extract_metric`) — untouched.
    """
    strict, tolerant, tight = _pandas_outcomes(
        [
            CheckSpec(_NOT_NULL, {"column": "note"}),
            CheckSpec(_NOT_NULL, {"column": "note", "mostly": 0.5}),
            CheckSpec(_NOT_NULL, {"column": "note", "mostly": 0.95}),
        ],
        monkeypatch,
    )
    assert (strict.success, tolerant.success, tight.success) == (False, True, False)
    assert _unexpected_percent(strict) == 20.0
    assert _unexpected_percent(tolerant) == 20.0
    assert _unexpected_percent(tight) == 20.0


def test_mostly_runs_on_a_sql_batch_too(tmp_path: Path) -> None:
    (tolerant,) = _sql_outcomes([CheckSpec(_NOT_NULL, {"column": "note", "mostly": 0.5})], tmp_path)
    assert tolerant.success is True
    assert tolerant.errored is False
    assert _unexpected_percent(tolerant) == 20.0
