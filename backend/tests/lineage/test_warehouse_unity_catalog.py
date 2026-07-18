"""Unity Catalog warehouse-native lineage provider tests (#858).

The table_lineage tier runs against the REAL captured payload
(`uc_table_lineage_projected.json` — 200 rows / 8 unique edges from the live demo workspace,
2026-07-17 spike), NOT hand-written rows (#823). Identity is pinned byte-for-byte
against `services.asset_identity`; the incremental `event_time` watermark and the
edge-requires-both-endpoints rule (most rows are pure read-access, target NULL) are
exercised against the real distribution.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from backend.app.lineage.warehouse import LineageTier, WarehouseLineageUnavailableError
from backend.app.lineage.warehouse_unity_catalog import UnityCatalogLineageProvider
from backend.app.services.asset_identity import format_unity_catalog_name

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "lineage_native"
_WORKSPACE = "https://adb-7474653982915344.4.azuredatabricks.net"
_HOST = "adb-7474653982915344.4.azuredatabricks.net"
_CONFIG: dict[str, Any] = {"workspace_url": _WORKSPACE}


def _table_lineage_rows(*, since: datetime | None = None) -> list[tuple[Any, ...]]:
    """The captured table_lineage payload as SELECT-order tuples — filtered to the
    columns (and the >since / both-endpoints WHERE) the provider's query applies, so
    the fake reproduces what the warehouse would return, not the raw file."""
    raw = json.loads((_FIXTURES / "uc_table_lineage_projected.json").read_text())
    out = []
    for r in raw:
        if not (r["source_table_full_name"] and r["target_table_full_name"]):
            continue  # the WHERE ... IS NOT NULL both-endpoints filter
        et = datetime.fromisoformat(r["event_time"])
        if since is not None and not et > since:
            continue  # the WHERE event_time > :since filter
        out.append(
            (
                r["source_table_catalog"],
                r["source_table_schema"],
                r["source_table_name"],
                r["target_table_catalog"],
                r["target_table_schema"],
                r["target_table_name"],
                r["source_type"],
                r["target_type"],
                et,
            )
        )
    out.sort(key=lambda t: t[-1])  # ORDER BY event_time
    return out


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


def _column_lineage_rows(*, since: datetime | None = None) -> list[tuple[Any, ...]]:
    """The captured column_lineage payload (#901 — 200 real rows, 2026-07-18 live
    session) as SELECT-order tuples, filtered like the provider's WHERE clause."""
    raw = json.loads((_FIXTURES / "uc_column_lineage_projected.json").read_text())
    out = []
    for r in raw:
        if not (r["source_column_name"] and r["target_column_name"]):
            continue
        # The capture stored CAST(event_time AS STRING) — naive UTC in Databricks'
        # string form; re-attach UTC so the fake's filter can compare with the floor.
        et = datetime.fromisoformat(r["event_time"])
        if et.tzinfo is None:
            et = et.replace(tzinfo=UTC)
        if since is not None and not et > since:
            continue
        out.append(
            (
                r["source_table_catalog"],
                r["source_table_schema"],
                r["source_table_name"],
                r["source_column_name"],
                r["target_table_catalog"],
                r["target_table_schema"],
                r["target_table_name"],
                r["target_column_name"],
            )
        )
    return out


class _FakeConn:
    """Routes the provider's queries to the captured fixtures — table_lineage and
    column_lineage discriminated off the statement text — honoring the bound :since
    so the incremental watermark is exercised for real."""

    def __init__(
        self, *, raises: Exception | None = None, column_raises: Exception | None = None
    ) -> None:
        self._raises = raises
        self._column_raises = column_raises
        self.since_used: datetime | None = None

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        since = (params or {}).get("since")
        if "column_lineage" in str(statement):
            if self._column_raises is not None:
                raise self._column_raises
            return _Result(_column_lineage_rows(since=since))
        if self._raises is not None:
            raise self._raises
        self.since_used = since
        return _Result(_table_lineage_rows(since=since))


def test_table_lineage_builds_real_edges() -> None:
    result = UnityCatalogLineageProvider().fetch_edges(_FakeConn(), connection_config=_CONFIG)
    assert result.tier == LineageTier.UNITY_CATALOG_SYSTEM_ACCESS
    assert result.freshness_lag is not None

    pairs = {(e.upstream.name, e.downstream.name) for e in result.edges}
    # the real captured chain: raw.sales_events -> silver.sales -> gold.daily_revenue
    assert (
        format_unity_catalog_name("dataq_retail", "raw", "sales_events"),
        format_unity_catalog_name("dataq_retail", "silver", "sales"),
    ) in pairs
    assert (
        format_unity_catalog_name("dataq_retail", "silver", "sales"),
        format_unity_catalog_name("dataq_retail", "gold", "daily_revenue"),
    ) in pairs
    # every endpoint under the workspace namespace, byte-for-byte with asset_identity
    ns = f"unitycatalog://{_HOST}"
    assert all(e.upstream.namespace == ns and e.downstream.namespace == ns for e in result.edges)


def test_self_edge_from_real_payload_is_dropped() -> None:
    # The real payload contains feedback_sentiment -> feedback_sentiment (a self-rewrite).
    result = UnityCatalogLineageProvider().fetch_edges(_FakeConn(), connection_config=_CONFIG)
    for edge in result.edges:
        assert edge.upstream.name != edge.downstream.name


def test_watermark_advances_to_max_event_time() -> None:
    result = UnityCatalogLineageProvider().fetch_edges(_FakeConn(), connection_config=_CONFIG)
    assert result.new_watermark is not None
    # the returned watermark is the max event_time across the read rows
    all_times = [row[-1] for row in _table_lineage_rows()]
    assert result.new_watermark == max(all_times)


def test_incremental_since_reads_only_newer_events() -> None:
    # A watermark AFTER every event → the pull reads nothing new, and the watermark must
    # NOT regress (it is carried forward unchanged).
    future = datetime(2027, 1, 1, tzinfo=UTC)
    conn = _FakeConn()
    result = UnityCatalogLineageProvider().fetch_edges(
        conn, connection_config=_CONFIG, since=future
    )
    assert result.edges == ()
    assert result.new_watermark == future  # carried forward, never regressed


def test_incremental_bound_floor_is_watermark_minus_safety_window() -> None:
    # The query re-scans a safety window BEFORE the watermark so a late-ingested row
    # (event_time <= watermark, ingested after the last pull) is not lost to a strict
    # `>`. The returned watermark still never regresses below `since`.
    from backend.app.lineage.warehouse_unity_catalog import _WATERMARK_SAFETY

    # A mark AFTER every event: the safety window (6h) still doesn't reach the July
    # payload, so nothing is re-read and the watermark stays put — proving both the
    # floor offset and the never-regress guarantee in one case.
    mark = datetime(2027, 1, 1, tzinfo=UTC)
    conn = _FakeConn()
    result = UnityCatalogLineageProvider().fetch_edges(conn, connection_config=_CONFIG, since=mark)
    assert conn.since_used == mark - _WATERMARK_SAFETY  # re-scan window applied to the query
    assert result.new_watermark == mark  # but the watermark itself never regressed
    assert result.edges == ()


def test_is_incremental_flag_is_true() -> None:
    # The refresh reads this to pick the log regime (watermark + no prune).
    assert UnityCatalogLineageProvider().is_incremental is True


def test_missing_grant_is_unavailable_not_empty() -> None:
    conn = _FakeConn(raises=RuntimeError("PERMISSION_DENIED: SELECT on system.access"))
    with pytest.raises(WarehouseLineageUnavailableError, match=r"system\.access"):
        UnityCatalogLineageProvider().fetch_edges(conn, connection_config=_CONFIG)


def test_missing_workspace_url_is_unavailable() -> None:
    with pytest.raises(WarehouseLineageUnavailableError, match="workspace_url"):
        UnityCatalogLineageProvider().fetch_edges(_FakeConn(), connection_config={})


def test_source_tag_is_unity_catalog() -> None:
    assert UnityCatalogLineageProvider().source == "unity_catalog"


# ── column grain (#901) ────────────────────────────────────────────────────────


def test_column_pairs_attach_to_their_table_edge() -> None:
    """The captured column_lineage rows refine the edges from the captured
    table_lineage payload — the join is on full names, byte-for-byte (#823)."""
    result = UnityCatalogLineageProvider().fetch_edges(_FakeConn(), connection_config=_CONFIG)
    by_name = {(e.upstream.name, e.downstream.name): e for e in result.edges}
    key = (
        format_unity_catalog_name("dataq_retail", "silver", "feedback"),
        format_unity_catalog_name("dataq_retail", "gold", "feedback_sentiment"),
    )
    assert key in by_name
    pairs = by_name[key].column_pairs
    # A real derived-column mapping from the live capture: comment -> sentiment.
    assert ("comment", "sentiment") in pairs
    # Pass-through columns from the same real payload.
    assert ("customer_id", "customer_id") in pairs
    assert pairs == tuple(sorted(set(pairs)))  # deduped + deterministic order


def test_column_grain_failure_degrades_never_fails_table_edges() -> None:
    """column_lineage gated separately from table_lineage (a real UC grant shape):
    the table edges still land, with an honest degrade note instead of a silent
    absence — and never an Unavailable that would freeze the cache (#828)."""
    conn = _FakeConn(column_raises=RuntimeError("PERMISSION_DENIED: column_lineage"))
    result = UnityCatalogLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert len(result.edges) > 0
    assert all(e.column_pairs == () for e in result.edges)
    assert result.degraded_reason is not None
    assert "column-level lineage unavailable" in result.degraded_reason
    # The degrade note is CLASSIFIED (exception type only) — never raw text, which
    # for a storage-layer failure can carry a signed URL (#828).
    assert "PERMISSION_DENIED" not in result.degraded_reason
    assert "RuntimeError" in result.degraded_reason


def test_healthy_column_grain_sets_no_degrade_note() -> None:
    result = UnityCatalogLineageProvider().fetch_edges(_FakeConn(), connection_config=_CONFIG)
    assert result.degraded_reason is None
