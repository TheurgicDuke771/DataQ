"""Snowflake warehouse-native lineage provider tests (#858).

The OBJECT_DEPENDENCIES tier is exercised against the REAL captured payload
(`backend/tests/fixtures/lineage_native/snowflake_object_dependencies.json` — 200 rows
from the live demo account, 2026-07-17 spike), NOT hand-written rows: per #823, a fixture
we authored ourselves can pass while the real shape fails. The edition-gated tiers
(GET_LINEAGE 0A000, ACCESS_HISTORY silent-empty) are driven by fakes that reproduce the
connector's observed behaviour, since the Standard demo account cannot emit their payloads.

Identity is pinned BYTE-FOR-BYTE against `services.asset_identity` — the whole premise of
warehouse-native lineage (no fold needed) is that these match, so if they ever diverge the
edges would 404 against `assets` exactly as the dbt path did (#823).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.lineage.warehouse import LineageTier, WarehouseLineageUnavailableError
from backend.app.lineage.warehouse_snowflake import SnowflakeLineageProvider
from backend.app.services.asset_identity import format_snowflake_name, normalize_snowflake_account

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "lineage_native"
_ACCOUNT = "PVQSOEQ-ZGB34383"  # the demo account the payload was captured from
_CONFIG: dict[str, Any] = {"account": _ACCOUNT}


def _object_dependencies_rows() -> list[tuple[Any, ...]]:
    """The captured OBJECT_DEPENDENCIES payload as (col, …) tuples in the query's
    SELECT order — what a SQLAlchemy `.all()` returns."""
    raw = json.loads((_FIXTURES / "snowflake_object_dependencies.json").read_text())
    return [
        (
            r["REFERENCED_DATABASE"],
            r["REFERENCED_SCHEMA"],
            r["REFERENCED_OBJECT_NAME"],
            r["REFERENCED_OBJECT_DOMAIN"],
            r["REFERENCING_DATABASE"],
            r["REFERENCING_SCHEMA"],
            r["REFERENCING_OBJECT_NAME"],
            r["REFERENCING_OBJECT_DOMAIN"],
        )
        for r in raw
    ]


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return self._rows[0][0] if self._rows else None


class _FakeConn:
    """A SQLAlchemy-connection double that routes each query to a canned result by a
    substring of the SQL text. `raises` maps a substring → an exception to throw."""

    def __init__(
        self,
        *,
        results: dict[str, list[Any]] | None = None,
        raises: dict[str, Exception] | None = None,
    ) -> None:
        self._results = results or {}
        self._raises = raises or {}
        self.executed: list[str] = []

    def execute(self, statement: Any) -> _Result:
        sql = str(statement)
        self.executed.append(sql)
        for needle, exc in self._raises.items():
            if needle in sql:
                raise exc
        for needle, rows in self._results.items():
            if needle in sql:
                return _Result(rows)
        return _Result([])


def _feature_unsupported_error() -> Exception:
    """A stand-in for the connector's ProgrammingError with Snowflake's edition-gate
    SQLSTATE 0A000 (`Unsupported feature 'Data Lineage'`)."""

    class _ProgrammingError(Exception):
        sqlstate = "0A000"

    return _ProgrammingError("002139 (0A000): Unsupported feature 'Data Lineage'.")


# ───────────────────── tier 3: OBJECT_DEPENDENCIES (real payload) ─────────────


def test_object_dependencies_builds_real_dbt_chain() -> None:
    # GET_LINEAGE + ACCESS_HISTORY both edition-gated → descend to the floor, which is
    # the only tier live on the demo account.
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert result.degraded_reason is not None  # richer tiers were gated → say so
    assert "get_lineage" in " ".join(result.skipped_tiers).lower()

    # The real captured chain: RETAIL.ORDERS_HEADER → STG_ORDERS → MART_ORDER_REVENUE.
    ns = f"snowflake://{normalize_snowflake_account(_ACCOUNT)}"
    pairs = {(e.upstream.name, e.downstream.name) for e in result.edges}
    assert (
        format_snowflake_name("DATAQ_DB", "ANALYTICS_STG", "STG_ORDERS"),
        format_snowflake_name("DATAQ_DB", "ANALYTICS", "MART_ORDER_REVENUE"),
    ) in pairs
    # every endpoint carries the account namespace, byte-for-byte with asset_identity
    assert all(e.upstream.namespace == ns and e.downstream.namespace == ns for e in result.edges)


def test_object_dependencies_drops_function_domain_endpoints() -> None:
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    # The payload contains FUNCTION-domain rows; none may become an edge (a UDF has no
    # asset identity). Every surviving endpoint is a 3-part DB.SCHEMA.TABLE name.
    for edge in result.edges:
        assert edge.upstream.name.count(".") == 2
        assert edge.downstream.name.count(".") == 2


def test_object_dependencies_dedupes_and_drops_self_edges() -> None:
    ok = ("DB", "S", "A", "TABLE", "DB", "S", "B", "VIEW")
    dup = ("DB", "S", "A", "TABLE", "DB", "S", "B", "VIEW")  # identical pair
    self_edge = ("DB", "S", "C", "TABLE", "DB", "S", "C", "DYNAMIC TABLE")  # A→A
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": [ok, dup, self_edge]},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert len(result.edges) == 1  # dup collapsed, self-edge dropped


# ───────────────────── tier 2: ACCESS_HISTORY corroboration ───────────────────


def test_access_history_empty_with_activity_falls_through_to_floor() -> None:
    # The Standard-edition signature: ACCESS_HISTORY empty, QUERY_HISTORY non-zero →
    # emptiness is edition-gating, NOT "no lineage" → descend to OBJECT_DEPENDENCIES.
    conn = _FakeConn(
        results={
            "ACCESS_HISTORY ah": [],  # the FLATTEN join — empty
            "QUERY_HISTORY": [(45630,)],  # account is active
            "OBJECT_DEPENDENCIES": _object_dependencies_rows(),
        },
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert any("access_history" in s and "empty" in s for s in result.skipped_tiers)


def test_access_history_populated_wins_over_floor() -> None:
    conn = _FakeConn(
        results={
            "ACCESS_HISTORY ah": [
                ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DATAQ_DB.ANALYTICS_STG.STG_ORDERS")
            ],
            "OBJECT_DEPENDENCIES": _object_dependencies_rows(),
        },
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_ACCESS_HISTORY
    assert result.freshness_lag is not None  # the 2-3h latency is surfaced
    assert result.tier.is_column_level
    [edge] = result.edges
    assert edge.upstream.name == format_snowflake_name("DATAQ_DB", "RETAIL", "ORDERS_HEADER")


def test_access_history_empty_idle_account_is_a_true_empty() -> None:
    # ACCESS_HISTORY empty AND no query activity → genuinely idle, not edition-gated:
    # ACCESS_HISTORY is the answering tier with zero edges (a true, prunable empty),
    # not a fall-through.
    conn = _FakeConn(
        results={"ACCESS_HISTORY ah": [], "QUERY_HISTORY": [(0,)], "OBJECT_DEPENDENCIES": []},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_ACCESS_HISTORY
    assert result.edges == ()


# ───────────────────── tier 1: GET_LINEAGE preflight ──────────────────────────


def test_get_lineage_0a000_descends_the_ladder() -> None:
    # The spike's key finding: the edition gate is a CLEAN, catchable 0A000, so a
    # missing GET_LINEAGE must degrade gracefully, never error the pull.
    conn = _FakeConn(
        results={
            "ACCESS_HISTORY ah": [
                ("DATAQ_DB.RETAIL.CUSTOMERS", "DATAQ_DB.ANALYTICS_STG.STG_CUSTOMERS")
            ]
        },
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_ACCESS_HISTORY
    assert any("get_lineage" in s for s in result.skipped_tiers)


def test_get_lineage_supported_but_deferred_reports_honest_reason() -> None:
    # On Enterprise, GET_LINEAGE IS supported but its per-seed traversal is deferred.
    # The skipped_tiers note must say so — NOT the false "unsupported on this edition"
    # an Enterprise operator would otherwise see (a hard-coded label was the bug).
    conn = _FakeConn(
        results={
            "GET_LINEAGE": [(1,)],  # the probe succeeds → feature IS available
            "ACCESS_HISTORY ah": [],
            "QUERY_HISTORY": [(0,)],
        }
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    note = next(s for s in result.skipped_tiers if s.startswith("get_lineage"))
    assert "traversal not yet built" in note
    assert "unsupported on this edition" not in note


def test_get_lineage_message_only_gate_also_descends() -> None:
    # Belt-and-braces: even if a connector surfaced the gate without the SQLSTATE, the
    # documented message text still routes it to the descent (not a hard failure).
    class _NoStateError(Exception):
        pass

    conn = _FakeConn(
        results={"ACCESS_HISTORY ah": [], "QUERY_HISTORY": [(0,)]},
        raises={"GET_LINEAGE": _NoStateError("Unsupported feature 'Data Lineage'.")},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_ACCESS_HISTORY  # descended cleanly


# ───────────────────── failure + config guards ────────────────────────────────


def test_floor_failure_is_unavailable_not_empty() -> None:
    # If even OBJECT_DEPENDENCIES fails, we learned NOTHING — the refresh must not
    # prune, so this is Unavailable, never an empty result.
    conn = _FakeConn(
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
            "OBJECT_DEPENDENCIES": RuntimeError("SELECT privilege missing on SNOWFLAKE db"),
        }
    )
    with pytest.raises(WarehouseLineageUnavailableError, match="OBJECT_DEPENDENCIES"):
        SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)


def test_missing_account_is_unavailable() -> None:
    with pytest.raises(WarehouseLineageUnavailableError, match="no account"):
        SnowflakeLineageProvider().fetch_edges(_FakeConn(), connection_config={})


def test_source_tag_is_snowflake() -> None:
    assert SnowflakeLineageProvider().source == "snowflake"
