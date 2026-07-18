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
_CONFIG: dict[str, Any] = {"account": _ACCOUNT, "database": "DATAQ_DB"}


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

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.params = params
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
                ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DATAQ_DB.ANALYTICS_STG.STG_ORDERS", None)
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
                ("DATAQ_DB.RETAIL.CUSTOMERS", "DATAQ_DB.ANALYTICS_STG.STG_CUSTOMERS", None)
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


# ── #902: authorization errors descend the ladder (found live, 2026-07-18) ────


def _not_authorized_error() -> Exception:
    """The live shape: role lacks the ACCOUNT_USAGE grant → 002003 compilation error
    (Snowflake deliberately blurs missing-object and missing-grant into one message)."""

    class _ProgrammingError(Exception):
        sqlstate = "02000"

    return _ProgrammingError(
        "002003 (02000): SQL compilation error:\n"
        "Table 'SNOWFLAKE.ACCOUNT_USAGE.TABLES' does not exist or not authorized."
    )


def test_not_authorized_tier_descends_instead_of_aborting() -> None:
    """A least-privilege role (e.g. SNOWFLAKE.GOVERNANCE_VIEWER) can read the lower
    tiers while the GET_LINEAGE probe's table is denied — the denied tier must skip
    with a reason, not abort the tiers the role CAN read (#902)."""
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={
            "GET_LINEAGE": _not_authorized_error(),
            "ACCESS_HISTORY": _not_authorized_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert len(result.edges) > 0
    assert any("not authorized" in s for s in result.skipped_tiers)
    # The degrade note carries the real (grant-shaped) reason — not a hardcoded
    # "need Enterprise" that would mislead the operator toward the wrong fix.
    assert result.degraded_reason is not None
    assert "not authorized" in result.degraded_reason
    # ...and it is a constructed, stable string — never the raw connector text.
    assert "002003" not in result.degraded_reason


def test_fully_denied_account_is_unavailable_not_empty() -> None:
    """Every tier denied (no ACCOUNT_USAGE grant at all — the live DATAQ_READER
    shape): the pull reports unavailable so the refresh freezes the cache; it must
    never read as a confident empty graph (#828)."""
    conn = _FakeConn(
        raises={
            "GET_LINEAGE": _not_authorized_error(),
            "ACCESS_HISTORY": _not_authorized_error(),
            "OBJECT_DEPENDENCIES": _not_authorized_error(),
        }
    )
    with pytest.raises(WarehouseLineageUnavailableError):
        SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)


# ── #908: scope + hygiene + column grain (Enterprise, live-tuned) ─────────────


def _access_history_conn(rows: list[Any]) -> _FakeConn:
    return _FakeConn(
        results={"ACCESS_HISTORY ah": rows, "QUERY_HISTORY": [(100,)]},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )


def test_access_history_query_is_scoped_and_bound() -> None:
    """The scope lives in the SQL (contractual, #908): both endpoints Table-domain,
    both in the connection's database via a BOUND param (no interpolation), and a
    bounded lookback — the unscoped account-wide sweep is what shipped Snowpark
    scratch and a dropped schema as browsable assets."""
    conn = _access_history_conn([])
    SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    sql = next(s for s in conn.executed if "ACCESS_HISTORY ah" in s)
    assert "bo.value:objectDomain = 'Table'" in sql
    assert "om.value:objectDomain = 'Table'" in sql
    assert "SPLIT_PART(bo.value:objectName::string, '.', 1) = :db" in sql
    assert "SPLIT_PART(om.value:objectName::string, '.', 1) = :db" in sql
    assert "DATEADD('day', -90" in sql
    assert conn.params == {"db": "DATAQ_DB"}


def test_object_dependencies_query_is_db_bound() -> None:
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": []},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    sql = next(s for s in conn.executed if "OBJECT_DEPENDENCIES" in s)
    assert "referenced_database = :db AND referencing_database = :db" in sql
    assert conn.params == {"db": "DATAQ_DB"}


def test_missing_database_is_unavailable() -> None:
    with pytest.raises(WarehouseLineageUnavailableError, match="database"):
        SnowflakeLineageProvider().fetch_edges(_FakeConn(), connection_config={"account": _ACCOUNT})


def test_snowpark_ephemera_rows_never_become_edges() -> None:
    # Real class from the live pull: SNOWPARK_TEMP_* tables are session scratch —
    # present in ACCESS_HISTORY, gone before anyone could browse the asset.
    rows = [
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_K0ADU7Z7AS", "DATAQ_DB.RETAIL.ORDERS", None),
        ("DATAQ_DB.RETAIL.ORDERS", "DATAQ_DB.PERF.SNOWPARK_TEMP_STAGE_5G3D7DHWSF", None),
        ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DATAQ_DB.ANALYTICS_STG.STG_ORDERS", None),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (
            format_snowflake_name("DATAQ_DB", "RETAIL", "ORDERS_HEADER"),
            format_snowflake_name("DATAQ_DB", "ANALYTICS_STG", "STG_ORDERS"),
        )
    ]


def test_column_pairs_extracted_from_direct_sources() -> None:
    """objects_modified[].columns[].directSources → column_pairs (#908). The pair
    attaches ONLY to the edge whose upstream is the direct source's table — a second
    source table in the same statement belongs to its own edge's row."""
    cols_json = json.dumps(
        [
            {
                "columnName": "ORDER_TOTAL",
                "directSources": [
                    {
                        "columnName": "SUBTOTAL",
                        "objectDomain": "Table",
                        "objectName": "DATAQ_DB.RETAIL.ORDERS_HEADER",
                    },
                    {  # a DIFFERENT source table — belongs to that edge's own row
                        "columnName": "TAX_RATE",
                        "objectDomain": "Table",
                        "objectName": "DATAQ_DB.REFERENCE.TAX",
                    },
                ],
            },
            {"columnName": "LOADED_AT", "directSources": []},  # real shape: COPY columns
        ]
    )
    rows = [("DATAQ_DB.RETAIL.ORDERS_HEADER", "DATAQ_DB.ANALYTICS_STG.STG_ORDERS", cols_json)]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    [edge] = result.edges
    assert edge.column_pairs == (("SUBTOTAL", "ORDER_TOTAL"),)


def test_real_capture_empty_direct_sources_yield_no_pairs() -> None:
    """The REAL captured Enterprise payload (2026-07-18): every historical write is
    COPY-from-stage, so every ``columns[].directSources`` is EMPTY — the extractor
    must yield zero pairs from it, never fabricate (the #823 discipline)."""
    raw = json.loads((_FIXTURES / "sf_access_history_columns_projected.json").read_text())
    provider = SnowflakeLineageProvider()
    ns = f"snowflake://{normalize_snowflake_account(_ACCOUNT)}"
    up = provider._identity_from_qualified(ns, "DATAQ_DB.RETAIL.ORDERS_HEADER")
    assert up is not None
    for entry in raw:
        assert (
            provider._column_pairs_from_json(
                json.dumps(entry["tgt_columns"]), namespace=ns, upstream=up
            )
            == []
        )


def test_malformed_columns_json_never_breaks_the_pull() -> None:
    rows = [
        ("DATAQ_DB.RETAIL.A", "DATAQ_DB.RETAIL.B", "{not json"),
        ("DATAQ_DB.RETAIL.A", "DATAQ_DB.RETAIL.C", json.dumps({"unexpected": "shape"})),
        ("DATAQ_DB.RETAIL.A", "DATAQ_DB.RETAIL.D", json.dumps([{"columnName": 7}])),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    assert len(result.edges) == 3
    assert all(e.column_pairs == () for e in result.edges)
