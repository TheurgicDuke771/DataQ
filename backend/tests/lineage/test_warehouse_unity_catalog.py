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


class _FakeConn:
    """Routes the provider's query to the fixture, honoring its bound :since so the
    incremental watermark is exercised for real."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.since_used: datetime | None = None

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        if self._raises is not None:
            raise self._raises
        since = (params or {}).get("since")
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
    assert conn.since_used == future  # the watermark was bound into the query
    assert result.edges == ()
    assert result.new_watermark == future  # carried forward, never regressed


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
