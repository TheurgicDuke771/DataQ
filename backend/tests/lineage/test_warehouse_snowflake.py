"""Snowflake warehouse-native lineage provider tests (#858).

The OBJECT_DEPENDENCIES tier is exercised against the REAL captured payload
(`backend/tests/fixtures/lineage_native/snowflake_object_dependencies.json` — 200 rows
from the live demo account, 2026-07-17 spike), NOT hand-written rows: per #823, a fixture
we authored ourselves can pass while the real shape fails.

The GET_LINEAGE traversal (#892) and the Snowpark-scratch stitch (#912) ride their own
REAL captures, taken from live **prod Enterprise** on 2026-07-28
(`sf_get_lineage_projected.json`, `sf_access_history_snowpark_projected.json`) — the
edition that could not be observed when #858 shipped. Where a capture cannot express a
case (the surviving Snowpark rows are TEMP→TEMP only: the physical endpoints of those
pipelines fell out of ACCESS_HISTORY's retention), the REAL rows are augmented with
minimal same-shaped synthetic rows, and every such test says so at the point of use.
The 0A000 / not-authorized descents remain fake-driven — a live Enterprise account
cannot produce them.

Identity is pinned BYTE-FOR-BYTE against `services.asset_identity` — the whole premise of
warehouse-native lineage (no fold needed) is that these match, so if they ever diverge the
edges would 404 against `assets` exactly as the dbt path did (#823).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs
from structlog.typing import EventDict

from backend.app.core.config import get_settings
from backend.app.lineage import warehouse_snowflake
from backend.app.lineage.warehouse import LineageTier, WarehouseLineageUnavailableError
from backend.app.lineage.warehouse_snowflake import (
    _EPHEMERAL_STITCH_MAX_DEPTH,
    SnowflakeLineageProvider,
)
from backend.app.services.asset_identity import format_snowflake_name, normalize_snowflake_account

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "lineage_native"
_ACCOUNT = "PVQSOEQ-ZGB34383"  # the demo account the payload was captured from
_CONFIG: dict[str, Any] = {"account": _ACCOUNT, "database": "DATAQ_DB"}


@contextmanager
def _captured_lineage_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[EventDict]]:
    """`capture_logs`, with the provider's module logger REBOUND INSIDE the capture.

    `configure_logging()` sets `cache_logger_on_first_use=True`, so once any earlier
    test in the suite has run it, `warehouse_snowflake.log` holds a cached bound logger
    and bypasses the processors `capture_logs` installs — the capture then yields an
    EMPTY list and every assertion passes vacuously (the #1056 lesson: green alone, red
    never). Rebinding forces the capture to see the events.
    """
    with capture_logs() as logs:
        monkeypatch.setattr(
            warehouse_snowflake,
            "log",
            structlog.get_logger("backend.app.lineage.warehouse_snowflake"),
        )
        yield logs


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


#: The columns `_from_get_lineage` SELECTs, in order — the fixture keeps all 16 of the
#: capture's `SELECT *` columns, so the loader projects by NAME. (Named columns, not
#: `SELECT *`: a positional read silently mis-binds if Snowflake ever inserts a column,
#: which would build edges out of the wrong fields instead of failing loudly.)
_GET_LINEAGE_SELECT = (
    "source_object_database",
    "source_object_schema",
    "source_object_name",
    "source_object_domain",
    "source_status",
    "source_column_name",
    "target_object_database",
    "target_object_schema",
    "target_object_name",
    "target_object_domain",
    "target_status",
    "target_column_name",
)


def _get_lineage_rows(tag: str) -> list[tuple[Any, ...]]:
    """One captured GET_LINEAGE result, projected into the SELECT's column order.

    The capture harness stringified every value (`list(map(str, row))`), so a real NULL
    is the literal `"None"` in the fixture; the driver returns `None`. Mapping it back
    here keeps the fixture verbatim while handing the provider the shape the connector
    actually produces — a `"None"` string would have quietly become a column pair.
    """
    raw = json.loads((_FIXTURES / "sf_get_lineage_projected.json").read_text())[tag]
    index = {key: position for position, key in enumerate(raw["keys"])}
    return [
        tuple(None if row[index[col]] == "None" else row[index[col]] for col in _GET_LINEAGE_SELECT)
        for row in raw["rows"]
    ]


def _snowpark_rows() -> list[tuple[Any, ...]]:
    """The captured ACCESS_HISTORY rows touching Snowpark scratch (365d window)."""
    raw = json.loads((_FIXTURES / "sf_access_history_snowpark_projected.json").read_text())
    return [tuple(row) for row in raw]


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return self._rows[0][0] if self._rows else None


class _FakeConn:
    """A SQLAlchemy-connection double that routes each query to a canned result by a
    substring of the SQL text. `raises` maps a substring → an exception to throw.

    **One seed table is served by default** (#892): the GET_LINEAGE tier now enumerates
    seeds through the ADR 0040 `INFORMATION_SCHEMA.TABLES` read BEFORE it calls
    GET_LINEAGE, so a conn with no seeds would skip the tier without ever reaching the
    `raises={"GET_LINEAGE": …}` gate — every ladder-descent test below would then pass
    for the wrong reason (vacuously). Callers that want a different seed list (or none)
    override the `INFORMATION_SCHEMA.TABLES` key explicitly.
    """

    def __init__(
        self,
        *,
        results: dict[str, list[Any]] | None = None,
        raises: dict[str, Exception] | None = None,
    ) -> None:
        self._results = {"INFORMATION_SCHEMA.TABLES": _DEFAULT_SEED_ROWS, **(results or {})}
        self._raises = raises or {}
        self.executed: list[str] = []
        self.params_by_query: dict[str, dict[str, Any] | None] = {}

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.params = params
        for marker in (
            "ACCESS_HISTORY ah",
            "OBJECT_DEPENDENCIES",
            "GET_LINEAGE",
            "INFORMATION_SCHEMA.TABLES",
        ):
            if marker in sql:
                self.params_by_query[marker] = params
        self.executed.append(sql)
        for needle, exc in self._raises.items():
            if needle in sql:
                raise exc
        for needle, rows in self._results.items():
            if needle in sql:
                return _Result(rows)
        return _Result([])


#: `(table_schema, table_name)` — the shape `enumerate_tables` selects.
_DEFAULT_SEED_ROWS: list[Any] = [("RETAIL", "ORDERS_HEADER")]


class _GetLineageConn(_FakeConn):
    """A `_FakeConn` that answers GET_LINEAGE **per (object, direction)**, the way the
    real function does — a single canned result would make the traversal look like it
    works while actually replaying one seed's rows for every seed."""

    def __init__(self, lineage: dict[tuple[str, str], list[Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lineage = lineage

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        # `raises` still wins (checked by the base) — routing per-seed must not quietly
        # disarm a test that meant to gate the tier.
        if "GET_LINEAGE" in sql and params is not None and "GET_LINEAGE" not in self._raises:
            self.executed.append(sql)
            self.params_by_query["GET_LINEAGE"] = params
            return _Result(self._lineage.get((str(params["obj"]), str(params["dir"])), []))
        return super().execute(statement, params)


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


# ───────────────────── ACCESS_HISTORY + floor union (the #911 union) ────────────────


def test_access_history_empty_with_activity_falls_through_to_floor() -> None:
    # Scoped-empty ACCESS_HISTORY (Standard silent-empty, an all-COPY database, or
    # an idle window all look identical) → the union degrades to the floor, with an
    # honest skip reason instead of the old edition-gating guess (#911).
    conn = _FakeConn(
        results={
            "ACCESS_HISTORY ah": [],  # the FLATTEN join — empty
            "OBJECT_DEPENDENCIES": _object_dependencies_rows(),
        },
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert any("access_history" in s and "no table-to-table DML" in s for s in result.skipped_tiers)


def test_access_history_unions_with_the_floor_never_replaces_it() -> None:
    """#911 review: the two sources are COMPLEMENTARY — a winning DML tier must not
    erase the view-dependency graph (a view is never a DML write, so ACCESS_HISTORY
    can never re-observe it; under the snapshot-prune regime, replacement would have
    PRUNED the whole dbt view chain on the next refresh)."""
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
    pairs = {(e.upstream.name, e.downstream.name) for e in result.edges}
    # The DML edge is present…
    assert (
        format_snowflake_name("DATAQ_DB", "RETAIL", "ORDERS_HEADER"),
        format_snowflake_name("DATAQ_DB", "ANALYTICS_STG", "STG_ORDERS"),
    ) in pairs
    # …AND the floor's real view chain survives alongside it.
    assert (
        format_snowflake_name("DATAQ_DB", "ANALYTICS_STG", "STG_ORDERS"),
        format_snowflake_name("DATAQ_DB", "ANALYTICS", "MART_ORDER_REVENUE"),
    ) in pairs


def test_everything_empty_is_the_floors_true_empty() -> None:
    # Scoped DML log empty AND the current-state dependency view empty → a true,
    # prunable empty answered by the floor (the current-state authority) — never a
    # guess about edition gating (#911: the union removed the corroboration heuristic;
    # a confident empty now requires BOTH sources to have answered empty).
    conn = _FakeConn(
        results={"ACCESS_HISTORY ah": [], "OBJECT_DEPENDENCIES": []},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
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


def test_get_lineage_supported_but_empty_descends_with_an_honest_reason() -> None:
    # GET_LINEAGE IS supported (no gate raised) but observed nothing. The skip note must
    # say THAT — not the false "unsupported on this edition" an Enterprise operator
    # would otherwise see (a hard-coded label was the original bug) — and the tier must
    # descend rather than return a confident empty: this tier PRUNES, so an empty win
    # here would wipe the floor's real view graph on the next refresh (#911).
    conn = _FakeConn(results={"GET_LINEAGE": [], "ACCESS_HISTORY ah": []})
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    note = next(s for s in result.skipped_tiers if s.startswith("get_lineage"))
    assert "no lineage rows" in note
    assert "unsupported on this edition" not in note
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES


def test_get_lineage_without_seeds_descends_instead_of_claiming_empty() -> None:
    # No enumerable table → nothing to traverse. Same rule: skip the tier with a reason
    # the operator can act on, never a top-tier empty that prunes the graph.
    conn = _FakeConn(results={"INFORMATION_SCHEMA.TABLES": [], "ACCESS_HISTORY ah": []})
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert any("no seed tables" in s for s in result.skipped_tiers)
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES


def test_seed_enumeration_failure_skips_the_tier_not_the_pull() -> None:
    # A catalog read failure must not cost the whole graph — the floor can still answer
    # (#828). The reason is classified, never the raw connector text.
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows(), "ACCESS_HISTORY ah": []},
        raises={"INFORMATION_SCHEMA.TABLES": RuntimeError("connection reset by peer")},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert len(result.edges) > 0
    assert any("seed enumeration failed (RuntimeError)" in s for s in result.skipped_tiers)
    assert result.degraded_reason is not None
    assert "connection reset" not in result.degraded_reason


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
    # Descended cleanly: a result (not a hard failure), answered by the floor union.
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert any("get_lineage" in sk for sk in result.skipped_tiers)


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


# ── #1228: two remaining abort-instead-of-descend sites ────────────────────────


def test_an_unclassified_access_history_failure_skips_just_that_tier() -> None:
    """Before #1228, `_from_access_history`'s bare `raise` on an unclassified
    failure propagated a RAW exception type straight out of `fetch_edges`'s
    `except _FeatureUnsupportedError` handler — discarding the floor's already-
    successful OBJECT_DEPENDENCIES read and aborting the whole pull over a blip
    on the DML-only half. It must now descend: the floor's edges are returned,
    non-prunable, with a classified (exception-type-only, #902) skip reason."""
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),  # top tier out → floor path
            "ACCESS_HISTORY": RuntimeError("SELECT privilege missing on SNOWFLAKE db"),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.edges  # the floor's real edges, not discarded
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert result.prunable is False  # unclassified — must not license a prune
    access_history_skips = [s for s in result.skipped_tiers if s.startswith("access_history")]
    assert len(access_history_skips) == 1
    assert "RuntimeError" in access_history_skips[0]  # exception TYPE only
    assert "SELECT privilege missing" not in access_history_skips[0]  # never raw text (#902)
    assert "transient" in access_history_skips[0]


def test_an_access_history_confirmed_denial_stays_prunable() -> None:
    """The other half of the same descent: a CONFIRMED per-object denial (edition
    gate / missing grant) recurs identically every cycle, so it must stay
    prunable — only the UNCLASSIFIED case above suspends the prune."""
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.edges
    assert result.prunable is True


def test_a_floor_failure_after_a_successful_traversal_returns_the_traversal() -> None:
    """Before #1228, a floor failure AFTER a successful GET_LINEAGE traversal
    raised `WarehouseLineageUnavailableError`, discarding `top.edges` outright.
    `prunable=False` (#1220) exists precisely so this can descend instead: the
    traversal's own edges are real, observed lineage — losing them to an
    unrelated floor blip is worse than returning them degraded."""
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        },
        raises={"OBJECT_DEPENDENCIES": RuntimeError("SELECT privilege missing on SNOWFLAKE db")},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.edges  # the traversal's edges, not discarded
    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert result.prunable is False
    assert result.degraded_reason is not None
    assert "object_dependencies" in result.degraded_reason
    assert "RuntimeError" in result.degraded_reason
    assert "SELECT privilege missing" not in result.degraded_reason  # never raw text (#902)


def test_a_confirmed_floor_denial_after_a_successful_traversal_stays_prunable() -> None:
    """Review finding on #1263: the first #1228 fix for this site marked EVERY
    floor failure transient unconditionally. A CONFIRMED per-object denial on
    OBJECT_DEPENDENCIES (a role permanently missing that one grant) recurs
    identically every cycle, so treating it as transient would suspend the
    snapshot prune FOREVER — the same failure the per-seed loop already guards
    against. Only an UNCLASSIFIED failure (the sibling test above) may do that."""
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        },
        raises={"OBJECT_DEPENDENCIES": _not_authorized_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.edges
    assert result.prunable is True
    assert result.degraded_reason is not None
    assert "not authorized" in result.degraded_reason
    assert "transient" not in result.degraded_reason


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
    # Table-LIKE domain set (title case), mirroring _ACCESS_HISTORY_TABLE_DOMAINS —
    # a bare = 'Table' dropped view/dynamic-table endpoints (#911).
    from backend.app.lineage.warehouse_snowflake import _ACCESS_HISTORY_TABLE_DOMAINS

    for endpoint in ("bo", "om"):
        clause = next(
            part for part in sql.split(" AND ") if part.startswith(f"{endpoint}.value:objectDomain")
        )
        for domain in _ACCESS_HISTORY_TABLE_DOMAINS:
            assert f"'{domain}'" in clause
    # OR-scope: at least one endpoint in the connection database — dropping cross-db
    # edges would assert "nothing feeds this table" (#845-class omission).
    assert (
        "(SPLIT_PART(bo.value:objectName::string, '.', 1) = :db "
        "OR SPLIT_PART(om.value:objectName::string, '.', 1) = :db)" in sql
    )
    assert "DATEADD('day', -:lookback" in sql
    # THIS query's own binds (the fake records per query — asserting the last call's
    # params silently verified the floor query instead, #911 review).
    assert conn.params_by_query["ACCESS_HISTORY ah"] == {"db": "DATAQ_DB", "lookback": 90}


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
    assert "referenced_database = :db OR referencing_database = :db" in sql
    assert conn.params_by_query["OBJECT_DEPENDENCIES"] == {"db": "DATAQ_DB"}


def test_missing_database_is_unavailable() -> None:
    with pytest.raises(WarehouseLineageUnavailableError, match="database"):
        SnowflakeLineageProvider().fetch_edges(_FakeConn(), connection_config={"account": _ACCOUNT})


def test_snowpark_ephemera_rows_never_become_edges() -> None:
    """The #912 contract, rewritten: a SNOWPARK_TEMP_* identity may never appear as an
    edge endpoint — but a COMPLETE chain through one is stitched, not dropped.

    Before #912 both halves of this test were "no edge at all", which is why a real
    dependency could vanish: `ORDERS → TEMP → ORDERS_WIDE` rendered ORDERS_WIDE with
    zero upstreams, indistinguishable from genuinely unlineaged.
    """
    rows = [
        # a complete chain: physical → scratch → physical
        ("DATAQ_DB.RETAIL.ORDERS", "DATAQ_DB.PERF.SNOWPARK_TEMP_STAGE_5G3D7DHWSF", None),
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_STAGE_5G3D7DHWSF", "DATAQ_DB.PERF.ORDERS_WIDE", None),
        # a chain that DEAD-ENDS in scratch — nothing real to attach to, so it drops
        ("DATAQ_DB.RETAIL.CUSTOMERS", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_K0ADU7Z7AS", None),
        # an ordinary physical edge, untouched by any of it
        ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DATAQ_DB.ANALYTICS_STG.STG_ORDERS", None),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    names = [(e.upstream.name, e.downstream.name) for e in result.edges]
    assert names == [
        (
            format_snowflake_name("DATAQ_DB", "RETAIL", "ORDERS"),
            format_snowflake_name("DATAQ_DB", "PERF", "ORDERS_WIDE"),
        ),
        (
            format_snowflake_name("DATAQ_DB", "RETAIL", "ORDERS_HEADER"),
            format_snowflake_name("DATAQ_DB", "ANALYTICS_STG", "STG_ORDERS"),
        ),
    ]
    assert not any("SNOWPARK_TEMP" in up or "SNOWPARK_TEMP" in down for up, down in names)


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
    for entry in raw:
        assert provider._pairs_by_source_table(json.dumps(entry["tgt_columns"]), namespace=ns) == {}


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


def test_quoted_database_config_scopes_by_exact_inner_case() -> None:
    # A quoted database ("DataQ_Db") is stored by ACCOUNT_USAGE in its exact inner
    # case — blanket .upper() would exact-match nothing and turn a config nuance into
    # a silently empty (and prunable!) graph (#911 review).
    conn = _FakeConn(
        results={"ACCESS_HISTORY ah": [], "OBJECT_DEPENDENCIES": []},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    SnowflakeLineageProvider().fetch_edges(
        conn, connection_config={"account": _ACCOUNT, "database": '"DataQ_Db"'}
    )
    assert conn.params_by_query["OBJECT_DEPENDENCIES"] == {"db": "DataQ_Db"}


def test_cross_database_edges_survive_the_scope() -> None:
    # OR-scope: a view in OUR database over another database's table is real lineage;
    # dropping it asserts "nothing feeds this view" (#845-class omission, #911).
    other_db = ("SHARED_DB", "RETAIL", "ORDERS", "TABLE", "DATAQ_DB", "ANALYTICS", "V", "VIEW")
    conn = _FakeConn(
        results={"ACCESS_HISTORY ah": [], "OBJECT_DEPENDENCIES": [other_db]},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (
            format_snowflake_name("SHARED_DB", "RETAIL", "ORDERS"),
            format_snowflake_name("DATAQ_DB", "ANALYTICS", "V"),
        )
    ]


def test_column_pairs_capped_per_edge() -> None:
    # The shared #901 cap applies to the SF grain too (#911: the port shipped uncapped).
    from backend.app.lineage.warehouse import MAX_COLUMN_PAIRS_PER_EDGE

    cols = json.dumps(
        [
            {
                "columnName": f"C{i}",
                "directSources": [
                    {
                        "columnName": f"S{i}",
                        "objectDomain": "Table",
                        "objectName": "DATAQ_DB.RETAIL.WIDE",
                    }
                ],
            }
            for i in range(MAX_COLUMN_PAIRS_PER_EDGE + 50)
        ]
    )
    rows = [("DATAQ_DB.RETAIL.WIDE", "DATAQ_DB.ANALYTICS_STG.STG_WIDE", cols)]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    [edge] = result.edges
    assert len(edge.column_pairs) == MAX_COLUMN_PAIRS_PER_EDGE


# ── mutation-spike gaps (#898) ────────────────────────────────────────────────


def test_a_non_table_row_mid_stream_skips_only_itself() -> None:
    """A FUNCTION/PROCEDURE endpoint must skip THAT ROW, not stop the scan.

    The spike killed nothing when `continue` became `break` — because every
    fixture that exercised the filter had its non-table row at the END, where the
    two are indistinguishable. Put one in the MIDDLE and the mutant silently drops
    every remaining edge: not an error, not an empty graph, just a lineage graph
    quietly missing its tail. That is the #845-class omission — asserting "nothing
    feeds this view" when we simply stopped looking.
    """
    rows = [
        ("DB", "S", "UP_A", "TABLE", "DB", "S", "DOWN_A", "VIEW"),
        # the poison pill, deliberately BETWEEN two good rows
        ("DB", "S", "SOME_FN", "FUNCTION", "DB", "S", "DOWN_B", "TABLE"),
        ("DB", "S", "UP_C", "TABLE", "DB", "S", "DOWN_C", "VIEW"),
    ]
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": rows},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    names = {(e.upstream.name, e.downstream.name) for e in result.edges}

    def qualified(table: str) -> str:
        return format_snowflake_name("DB", "S", table)

    assert (qualified("UP_A"), qualified("DOWN_A")) in names
    # The row AFTER the filtered one must survive — this is the whole point.
    assert (qualified("UP_C"), qualified("DOWN_C")) in names
    assert not any("SOME_FN" in up for up, _ in names)


def test_both_endpoints_must_be_table_like_not_just_one() -> None:
    """The domain guard is `or` over the NEGATIVES — i.e. both endpoints must be
    table-like. Flipping it to `and` admits an edge with one FUNCTION endpoint,
    which materializes a stored procedure as a data asset (the class of bug that
    put Stages in the graph on the first live pull)."""
    rows = [("DB", "S", "SOME_FN", "FUNCTION", "DB", "S", "DOWN", "TABLE")]
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": rows},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.edges == ()


def test_a_half_parseable_access_history_row_is_dropped_whole() -> None:
    """`up is not None AND down is not None` — an `or` here emits a half-edge.

    A non-3-part object name yields `None` from `_identity_from_qualified`. With
    `or`, a row whose SOURCE parses but whose TARGET does not still passes the
    guard and builds an edge with a None endpoint — a malformed edge written to
    `lineage_edges`, which is worse than the dropped row it replaces.
    """
    rows = [
        # target is a 2-part name → unparseable; source is fine
        ("DB.S.GOOD_SOURCE", "NOT_THREE_PART", None),
        ("DB.S.REAL_UP", "DB.S.REAL_DOWN", None),
    ]
    conn = _FakeConn(
        results={"ACCESS_HISTORY ah": rows},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert all(e.upstream is not None and e.downstream is not None for e in result.edges)
    names = {(e.upstream.name, e.downstream.name) for e in result.edges}
    assert (
        format_snowflake_name("DB", "S", "REAL_UP"),
        format_snowflake_name("DB", "S", "REAL_DOWN"),
    ) in names
    assert not any("NOT_THREE_PART" in down for _, down in names)


def test_the_top_tier_success_reports_its_own_tier_and_no_skips() -> None:
    """The ladder tests assert DESCENT; this one asserts the shape of a top-tier win.

    It used to stand in for the deferred traversal via a monkeypatched
    `_from_get_lineage`, with a docstring saying the branch was unreachable in
    production. Since #892 the branch is REAL, so the test rides the real captured
    payload instead: `tier` is rendered on the asset graph ("view-level only",
    "current as of…"), and a top-tier win must name itself and apologise for nothing.
    """
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        }
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert result.skipped_tiers == ()  # nothing was skipped — the first tier answered
    assert result.degraded_reason is None  # and nothing to apologise for
    assert result.edges
    # The top tier no longer returns early (#1110 review): GET_LINEAGE is bounded
    # (500 seeds, distance 2) while OBJECT_DEPENDENCIES is not, so the floor IS always
    # consulted and unioned in underneath a top-tier win — the fake's floor answers
    # empty here, so the union changes nothing about the asserted edges above.
    assert any("OBJECT_DEPENDENCIES" in sql for sql in conn.executed)


def test_sqlstate_is_read_through_the_sqlalchemy_wrapper() -> None:
    """SQLAlchemy wraps the driver error; the SQLSTATE lives on `.orig`.

    Untested until now, and it is the STRUCTURED half of the edition-gate check —
    the message-text half would mask its loss on any error whose text happens to
    match, so a broken `.orig` walk could sit here silently.
    """

    class _DriverError(Exception):
        sqlstate = "0A000"

    class _WrappedError(Exception):
        def __init__(self) -> None:
            super().__init__("(snowflake.connector.errors.ProgrammingError) 002139")
            self.orig: Exception = _DriverError("002139")

    # No "Unsupported feature" text anywhere — only the wrapped SQLSTATE can classify it.
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={"GET_LINEAGE": _WrappedError(), "ACCESS_HISTORY": _WrappedError()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert "get_lineage" in " ".join(result.skipped_tiers).lower()


# ── #892: GET_LINEAGE per-seed traversal (real prod-Enterprise capture) ────────


def _sf(schema: str, table: str, database: str = "DATAQ_DB") -> str:
    return format_snowflake_name(database, schema, table)


def test_get_lineage_traversal_builds_the_real_captured_chain() -> None:
    """The real DOWNSTREAM capture of `RETAIL.ORDERS_HEADER` (distance 2), byte-for-byte.

    The load-bearing fact the capture settles: **each row is a DIRECT source→target
    edge**, and `distance` is hops-from-seed. Two of these three rows are distance-2 and
    neither has the seed as an endpoint — reading `distance` as "the seed depends on
    this" would fabricate ORDERS_HEADER→MART_* edges the warehouse never asserted, which
    is why the expected set is pinned exactly rather than by containment.
    """
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        }
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert {(e.upstream.name, e.downstream.name) for e in result.edges} == {
        (_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS")),
        (_sf("ANALYTICS_STG", "STG_ORDERS"), _sf("ANALYTICS", "MART_CUSTOMER_ORDERS")),
        (_sf("ANALYTICS_STG", "STG_ORDERS"), _sf("ANALYTICS", "MART_ORDER_REVENUE")),
    }
    # Identity is byte-identical to `asset_identity` — the premise of warehouse-native
    # lineage (no fold) — and DYNAMIC_TABLE endpoints survive the domain allowlist.
    ns = f"snowflake://{normalize_snowflake_account(_ACCOUNT)}"
    assert all(e.upstream.namespace == ns and e.downstream.namespace == ns for e in result.edges)


def test_get_lineage_skips_the_masked_row_in_the_real_capture() -> None:
    """The real UPSTREAM capture of `STG_ORDERS` carries a redacted endpoint: `***` name
    parts, `source_status='MASKED'`, domain STAGE. Neither a `***` asset nor a stage may
    reach the graph — only the one real edge does."""
    conn = _GetLineageConn(
        {("DATAQ_DB.ANALYTICS_STG.STG_ORDERS", "UPSTREAM"): _get_lineage_rows("gl_up_stg_orders")},
        results={"INFORMATION_SCHEMA.TABLES": [("ANALYTICS_STG", "STG_ORDERS")]},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS"))
    ]
    assert not any("*" in e.upstream.name or "*" in e.downstream.name for e in result.edges)


def test_a_masked_row_is_skipped_even_when_its_domain_is_table_like() -> None:
    """The mutation-killing half of the masked-row contract.

    The REAL masked row happens to be a STAGE, so the domain allowlist drops it anyway
    — removing the mask check outright leaves the fixture test above green, i.e. that
    test alone proves nothing about masking. GET_LINEAGE redacts objects of ANY domain
    the role cannot see, so this row is the real shape (`***` + MASKED) with a
    table-like domain: synthetic on purpose, and the only thing standing between a
    redacted object and a `***.***.***` asset row.
    """
    masked = (
        "***", "***", "***", "TABLE", "MASKED", None,
        "DATAQ_DB", "RETAIL", "T", "TABLE", "ACTIVE", None,
    )  # fmt: skip
    real = (
        "DATAQ_DB", "RETAIL", "SRC", "TABLE", "ACTIVE", None,
        "DATAQ_DB", "RETAIL", "T", "TABLE", "ACTIVE", None,
    )  # fmt: skip
    conn = _GetLineageConn({("DATAQ_DB.RETAIL.ORDERS_HEADER", "UPSTREAM"): [masked, real]})
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (_sf("RETAIL", "SRC"), _sf("RETAIL", "T"))
    ]


def test_a_non_table_domain_row_mid_stream_skips_only_itself() -> None:
    """The #898 shape, ported to this tier: a STAGE endpoint must skip THAT ROW, not
    stop the scan. Put the poison pill in the MIDDLE — at the end, `continue` and
    `break` are indistinguishable and a mutant that silently drops the tail survives.

    The REAL captured STAGE row is also MASKED, so the mask check retires it before the
    domain guard ever runs; this ACTIVE stage (Snowflake reports one whenever the role
    CAN see it) is what exercises the guard at all.
    """
    rows = [
        (
            "DATAQ_DB", "RETAIL", "UP_A", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "RETAIL", "DOWN_A", "VIEW", "ACTIVE", None,
        ),
        (
            "DATAQ_DB", "RETAIL", "MY_STAGE", "STAGE", "ACTIVE", None,
            "DATAQ_DB", "RETAIL", "DOWN_B", "TABLE", "ACTIVE", None,
        ),
        (
            "DATAQ_DB", "RETAIL", "UP_C", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "RETAIL", "DOWN_C", "VIEW", "ACTIVE", None,
        ),
    ]  # fmt: skip
    conn = _GetLineageConn({("DATAQ_DB.RETAIL.ORDERS_HEADER", "UPSTREAM"): rows})
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    names = {(e.upstream.name, e.downstream.name) for e in result.edges}
    assert (_sf("RETAIL", "UP_A"), _sf("RETAIL", "DOWN_A")) in names
    # The row AFTER the filtered one must survive — the whole point.
    assert (_sf("RETAIL", "UP_C"), _sf("RETAIL", "DOWN_C")) in names
    assert not any("MY_STAGE" in up for up, _ in names)


def test_a_row_with_a_null_name_part_and_a_self_edge_are_both_dropped() -> None:
    """Two malformed shapes that must never reach `assets`: a NULL name part (which
    would build a `DATAQ_DB.None.T` identity — `format_snowflake_name` stringifies
    whatever it is handed) and a self-edge (an in-place rebuild, not lineage)."""
    rows = [
        (
            "DATAQ_DB", None, "SRC", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "RETAIL", "T", "TABLE", "ACTIVE", None,
        ),
        (
            "DATAQ_DB", "RETAIL", "SAME", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "RETAIL", "SAME", "TABLE", "ACTIVE", None,
        ),
        (
            "DATAQ_DB", "RETAIL", "REAL_UP", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "RETAIL", "REAL_DOWN", "VIEW", "ACTIVE", None,
        ),
    ]  # fmt: skip
    conn = _GetLineageConn({("DATAQ_DB.RETAIL.ORDERS_HEADER", "UPSTREAM"): rows})
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (_sf("RETAIL", "REAL_UP"), _sf("RETAIL", "REAL_DOWN"))
    ]


def test_an_unclassified_failure_on_the_first_call_now_descends_the_ladder() -> None:
    """Flips the pin this test used to encode (#1109): the old body asserted that an
    unclassified error on the FIRST GET_LINEAGE call propagates and fails the WHOLE
    pull — a deliberate carry-over from the pre-#892 single-probe shape, where one
    call and "the whole tier" were the same thing. #892 made the tier a per-seed
    traversal of up to 2xN calls (N = seeds), so a transient blip on call 1 of a
    thousand now costs a graph the floor tier would have answered fine — sharper than
    the old contract intended, hence the flip: this tier now skips (a classified
    reason, exception TYPE only per #902) exactly like the seed-enumeration failure
    above, and the floor/ACCESS_HISTORY union still answers instead of the pull
    raising.
    """
    conn = _GetLineageConn(
        {},
        raises={"GET_LINEAGE": RuntimeError("connection reset by peer")},
        results={
            "INFORMATION_SCHEMA.TABLES": [
                ("RETAIL", "ORDERS_HEADER"),
                ("RETAIL", "CUSTOMERS"),
            ],
            "OBJECT_DEPENDENCIES": _object_dependencies_rows(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert len(result.edges) > 0  # the floor still answered
    reason = next(s for s in result.skipped_tiers if s.startswith("get_lineage"))
    assert "RuntimeError" in reason
    assert "connection reset" not in reason  # exception TYPE only, never raw text (#902)
    assert result.degraded_reason is not None
    assert "connection reset" not in result.degraded_reason
    # The tier aborted on the very first call — the second seed was never tried.
    assert len([sql for sql in conn.executed if "GET_LINEAGE" in sql]) == 1
    # …and descending is only HALF the fix (#1109 review). This result is a floor-only
    # view of a database whose GET_LINEAGE tier we simply could not read: absence of a
    # top-tier edge here is not evidence it is gone. Marked non-prunable so the snapshot
    # refresh accretes instead of wiping the previously-cached richer graph on a blip.
    assert result.prunable is False
    assert "transient" in reason  # and an operator can tell a blip from an edition gate


def test_a_confirmed_first_call_failure_stays_prunable() -> None:
    """The other half of #1109's review finding, and the reason `prunable` is not just
    "did we descend": an EDITION GATE (or a missing grant) is a confirmed, structural
    answer — this account will say the same thing next cycle, so the floor's graph IS
    the current truth and the refresh must still prune against it. Marking every descent
    non-prunable would freeze a genuinely-removed dependency in the cache forever.
    """
    conn = _GetLineageConn(
        {},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert result.prunable is True
    reason = next(s for s in result.skipped_tiers if s.startswith("get_lineage"))
    assert "unsupported on this edition" in reason
    assert "transient" not in reason


def test_a_transient_skip_and_a_dead_floor_report_both_halves() -> None:
    """A total denial must still raise (never read as an empty graph) — and must say
    what happened FIRST (#1109 review): with GET_LINEAGE blipped and the floor then
    unreadable, the GET_LINEAGE half is the more diagnostic one ("the warehouse is
    unhappy" vs "one view is unreadable"), and it was being dropped on the way out.
    Both halves are constructed strings, so #902 still holds for the joined message.
    """
    conn = _GetLineageConn(
        {},
        raises={
            "GET_LINEAGE": RuntimeError("connection reset by peer"),
            "OBJECT_DEPENDENCIES": OSError("connection reset by peer"),
        },
    )
    with pytest.raises(WarehouseLineageUnavailableError) as caught:
        SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    message = str(caught.value)
    assert "OBJECT_DEPENDENCIES" in message and "OSError" in message  # the floor's own half
    assert "get_lineage" in message and "RuntimeError" in message  # …and what preceded it
    assert "connection reset" not in message  # exception TYPE only, never raw text (#902)


def test_get_lineage_walks_both_directions_and_dedupes_across_seeds() -> None:
    """All three real captures at once. Every seed is walked UPSTREAM *and* DOWNSTREAM
    (an edge is observable from either end, and a role may see only one), and the same
    edge observed from two seeds collapses to one."""
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.ANALYTICS_STG.STG_ORDERS", "UPSTREAM"): _get_lineage_rows(
                "gl_up_stg_orders"
            ),
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            ),
            ("DATAQ_DB.ANALYTICS.MART_CUSTOMER_ORDERS", "UPSTREAM"): _get_lineage_rows(
                "gl_up_mart"
            ),
        },
        results={
            "INFORMATION_SCHEMA.TABLES": [
                ("ANALYTICS", "MART_CUSTOMER_ORDERS"),
                ("ANALYTICS_STG", "STG_ORDERS"),
                ("RETAIL", "ORDERS_HEADER"),
            ]
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    # ORDERS_HEADER→STG_ORDERS appears in all three captures, STG_ORDERS→MART_CUSTOMER
    # in two: the union is exactly five distinct edges.
    assert {(e.upstream.name, e.downstream.name) for e in result.edges} == {
        (_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS")),
        (_sf("RETAIL", "CUSTOMERS"), _sf("ANALYTICS_STG", "STG_CUSTOMERS")),
        (_sf("ANALYTICS_STG", "STG_ORDERS"), _sf("ANALYTICS", "MART_CUSTOMER_ORDERS")),
        (_sf("ANALYTICS_STG", "STG_ORDERS"), _sf("ANALYTICS", "MART_ORDER_REVENUE")),
        (_sf("ANALYTICS_STG", "STG_CUSTOMERS"), _sf("ANALYTICS", "MART_CUSTOMER_ORDERS")),
    }
    assert len(result.edges) == 5  # no duplicate survived the cross-seed union


def test_get_lineage_query_binds_the_object_and_direction() -> None:
    """The seed name and direction are BOUND params, never interpolated — a table name
    is warehouse-controlled text reaching a SQL string (the #428 rule)."""
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        }
    )
    SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    calls = [sql for sql in conn.executed if "GET_LINEAGE" in sql]
    assert len(calls) == 2  # one seed x both directions
    assert "GET_LINEAGE(:obj, 'TABLE', :dir, 2)" in calls[0]
    assert conn.params_by_query["GET_LINEAGE"] == {
        "obj": "DATAQ_DB.RETAIL.ORDERS_HEADER",
        "dir": "DOWNSTREAM",
    }


def test_get_lineage_seed_cap_truncates_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Overflow walks the first N in catalog order and SAYS SO — a silently-capped
    traversal reads as a complete graph (the no-silent-caps rule, ADR 0040 §5)."""
    monkeypatch.setenv("WAREHOUSE_LINEAGE_MAX_SEEDS", "1")
    get_settings.cache_clear()
    try:
        conn = _GetLineageConn(
            {
                ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                    "gl_down_orders_header"
                )
            },
            results={
                "INFORMATION_SCHEMA.TABLES": [
                    ("RETAIL", "ORDERS_HEADER"),
                    ("RETAIL", "CUSTOMERS"),
                ]
            },
        )
        with _captured_lineage_logs(monkeypatch) as logs:
            SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    finally:
        get_settings.cache_clear()

    # cap+1 requested so the overflow is DETECTABLE, then only `cap` seeds are walked.
    assert conn.params_by_query["INFORMATION_SCHEMA.TABLES"] == {
        "db": "DATAQ_DB",
        "lim": 2,
        "ephemeral": "SNOWPARK!_TEMP!_%",  # bound + bang-escaped (#1111/#1112)
    }
    assert len([sql for sql in conn.executed if "GET_LINEAGE" in sql]) == 2
    truncation = next(e for e in logs if e["event"] == "get_lineage_seeds_truncated")
    assert truncation["cap"] == 1
    assert truncation["enumerated"] == 2


def test_get_lineage_column_grain_rides_the_row_pair() -> None:
    """`source_column_name`/`target_column_name` carry the column grain when the row has
    it. The captured rows are all table-grain (both NULL), so the populated pair here is
    synthetic — but the NULL half is real, and a `"None"` string surviving the capture's
    stringification would have produced a `("None", "None")` pair on every real edge.
    """
    with_cols = (
        "DATAQ_DB", "RETAIL", "SRC", "TABLE", "ACTIVE", "SUBTOTAL",
        "DATAQ_DB", "ANALYTICS", "TGT", "VIEW", "ACTIVE", "ORDER_TOTAL",
    )  # fmt: skip
    table_grain = (
        "DATAQ_DB", "RETAIL", "SRC", "TABLE", "ACTIVE", None,
        "DATAQ_DB", "ANALYTICS", "OTHER", "VIEW", "ACTIVE", None,
    )  # fmt: skip
    conn = _GetLineageConn(
        {("DATAQ_DB.RETAIL.ORDERS_HEADER", "UPSTREAM"): [with_cols, table_grain]}
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    pairs = {e.downstream.name: e.column_pairs for e in result.edges}
    assert pairs[_sf("ANALYTICS", "TGT")] == (("SUBTOTAL", "ORDER_TOTAL"),)
    assert pairs[_sf("ANALYTICS", "OTHER")] == ()


def test_a_later_seed_failure_is_skipped_not_fatal() -> None:
    """The first call proved the feature exists, so a later seed failing is a per-object
    problem (dropped object, a revoked grant on one table): it skips and the rest of the
    traversal still lands. Aborting would lose a whole graph over one table."""

    class _OneSeedFails(_GetLineageConn):
        def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
            if params is not None and params.get("obj") == "DATAQ_DB.RETAIL.CUSTOMERS":
                raise RuntimeError("SQL compilation error: object does not exist")
            return super().execute(statement, params)

    conn = _OneSeedFails(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        },
        results={
            "INFORMATION_SCHEMA.TABLES": [("RETAIL", "ORDERS_HEADER"), ("RETAIL", "CUSTOMERS")]
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert len(result.edges) == 3
    # …but a partial SUCCESS is still partial (#1109 review). Before, the ONLY trace of
    # the lost calls was a `get_lineage_seed_failures` structlog line — a run that lost
    # a MAJORITY of its calls returned a normal top-tier result, and the snapshot
    # refresh then pruned every edge those calls would have re-observed. Now it says so
    # on the result, and declines to license the prune.
    partial = next(s for s in result.skipped_tiers if "traversal call(s) failed" in s)
    assert "2 of 4" in partial  # both directions of the one dead seed, out of 2 seeds x 2
    assert "transient" in partial
    assert result.prunable is False
    assert result.degraded_reason is not None and "partial" in result.degraded_reason


def test_a_confirmed_per_seed_denial_is_reported_but_still_prunes() -> None:
    """The seed-level counterpart of `test_a_confirmed_first_call_failure_stays_prunable`,
    and the sharpest edge of the whole `prunable` mechanism (#1109 review): a role that
    cannot resolve ONE object fails that object on EVERY refresh, forever. Counting a
    confirmed per-object denial as transient would therefore suspend the snapshot prune
    permanently — dropped views frozen in the cache for good, a failure that never
    self-heals, strictly worse than the blip this mechanism exists to survive.

    So the denial is REPORTED (an operator wants to see it) but stays prunable, and the
    reason says which kind it was rather than mislabelling it a blip.
    """

    class _OneSeedDenied(_GetLineageConn):
        def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
            if params is not None and params.get("obj") == "DATAQ_DB.RETAIL.CUSTOMERS":
                # Snowflake's real per-object blur (002003), not a generic error.
                raise RuntimeError(
                    "002003 (42S02): SQL compilation error:\n"
                    "Object 'DATAQ_DB.RETAIL.CUSTOMERS' does not exist or not authorized."
                )
            return super().execute(statement, params)

    conn = _OneSeedDenied(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        },
        results={
            "INFORMATION_SCHEMA.TABLES": [("RETAIL", "ORDERS_HEADER"), ("RETAIL", "CUSTOMERS")]
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert result.prunable is True  # permanent per-object state → the prune stays armed
    partial = next(s for s in result.skipped_tiers if "traversal call(s) failed" in s)
    assert "2 of 4" in partial
    assert "transient" not in partial  # not a blip, and must not read as one
    assert "per-object denial" in partial


def test_a_clean_full_traversal_is_prunable_and_undegraded() -> None:
    """The control for the two tests above — without it, `prunable is False` proves
    nothing (a field hard-wired to False would satisfy them both). A traversal where
    every call landed is a complete observation of the tier: no skip note, no degrade,
    and the snapshot prune stays armed."""
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        }
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert result.prunable is True
    assert result.skipped_tiers == ()
    assert result.degraded_reason is None


def test_get_lineage_only_scratch_traversal_descends_not_a_confident_empty() -> None:
    """#1110 review, item 1: the PRE-stitch raw GET_LINEAGE rows can be non-empty purely
    from a dead-end SNOWPARK_TEMP_* hop (a real row, gone before the stitch completes),
    while the POST-stitch edge set is empty. The emptiness check must run on the
    post-stitch set — checking `raw` passes the `not len(raw)` guard and returns a
    confident `()` as a top-tier win, which under this snapshot-regime provider prunes
    the floor's persisted graph on the next refresh (exactly what the guard exists to
    prevent).
    """
    rows = [
        (
            "DATAQ_DB", "RETAIL", "ORDERS", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "PERF", "SNOWPARK_TEMP_TABLE_DEAD_END", "TABLE", "ACTIVE", None,
        ),
    ]  # fmt: skip
    conn = _GetLineageConn(
        {("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): rows},
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    # Descended: the tier must report the floor's answer with an honest reason, not a
    # confident GET_LINEAGE empty win — and the floor's real chain must survive.
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert any("get_lineage" in s and "no lineage rows" in s for s in result.skipped_tiers)
    assert result.edges  # the floor's real graph, not wiped by a top-tier confident empty


def test_get_lineage_stitches_the_scratch_it_traverses_through() -> None:
    """GET_LINEAGE can walk straight through Snowpark scratch too, so the #912 stitch
    runs on BOTH tiers — a per-tier copy is how the two column-pair caps drifted apart
    in the first place (#911)."""
    rows = [
        (
            "DATAQ_DB", "RETAIL", "ORDERS", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "PERF", "SNOWPARK_TEMP_TABLE_KJ20JIM48W", "TABLE", "ACTIVE", None,
        ),
        (
            "DATAQ_DB", "PERF", "SNOWPARK_TEMP_TABLE_KJ20JIM48W", "TABLE", "ACTIVE", None,
            "DATAQ_DB", "PERF", "ORDERS_WIDE", "TABLE", "ACTIVE", None,
        ),
    ]  # fmt: skip
    conn = _GetLineageConn({("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): rows})
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (_sf("RETAIL", "ORDERS"), _sf("PERF", "ORDERS_WIDE"))
    ]


# ── #912: Snowpark-scratch stitching (real captured payload) ──────────────────


def test_the_real_snowpark_capture_materializes_no_scratch_assets() -> None:
    """The captured 365d Snowpark rows, unaugmented: three NULL-source stage uploads and
    three STAGE→TABLE scratch hops, with no physical endpoint anywhere (the PERF writes
    aged out of ACCESS_HISTORY). Nothing real can be attached to, so nothing is emitted
    — and critically, no `SNOWPARK_TEMP_*` identity is either."""
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(_snowpark_rows()), connection_config=_CONFIG
    )
    assert result.edges == ()


def test_the_real_snowpark_chain_stitches_once_its_endpoints_are_present() -> None:
    """The #912 acceptance criterion, on the real payload.

    The capture's own chain is `SNOWPARK_TEMP_STAGE_TV3UJWGFTG →
    SNOWPARK_TEMP_TABLE_KJ20JIM48W` — a genuine Snowpark materialization carrying its
    real `columns` blob. Its physical endpoints fell out of the retention window, so
    they are added here as two MINIMAL SYNTHETIC rows of exactly the captured shape
    (3-part name, 3-part name, columns blob). Everything between them is real, and the
    collapse is what the ticket asks for: `A → TEMP → TEMP → B` ⇒ `A → B`.
    """
    real = [
        row for row in _snowpark_rows() if row[0] == "DATAQ_DB.PERF.SNOWPARK_TEMP_STAGE_TV3UJWGFTG"
    ]
    assert len(real) == 1  # the real hop the synthetic endpoints bracket
    rows = [
        # SYNTHETIC endpoint: the table the Snowpark job read
        ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DATAQ_DB.PERF.SNOWPARK_TEMP_STAGE_TV3UJWGFTG", None),
        *real,
        # SYNTHETIC endpoint: the physical table the job finally wrote
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_KJ20JIM48W", "DATAQ_DB.PERF.ORDERS_WIDE", None),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (_sf("RETAIL", "ORDERS_HEADER"), _sf("PERF", "ORDERS_WIDE"))
    ]


def _direct_sources(written: str, source_column: str, source_table: str) -> str:
    """One `objects_modified[].columns` blob of the captured shape, with a populated
    `directSources` (every REAL capture's is empty — see
    `test_real_capture_empty_direct_sources_yield_no_pairs`)."""
    return json.dumps(
        [
            {
                "columnName": written,
                "directSources": [
                    {
                        "columnName": source_column,
                        "objectDomain": "Table",
                        "objectName": source_table,
                    }
                ],
            }
        ]
    )


def test_column_pairs_compose_across_a_scratch_hop() -> None:
    """Both hops carry directSources → the composed pair joins over the bridging scratch
    column, so a stitched edge keeps the column grain the two hops jointly evidence."""
    rows = [
        (
            "DATAQ_DB.RETAIL.ORDERS_HEADER",
            "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_KJ20JIM48W",
            _direct_sources("TMP_TOTAL", "SUBTOTAL", "DATAQ_DB.RETAIL.ORDERS_HEADER"),
        ),
        (
            "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_KJ20JIM48W",
            "DATAQ_DB.PERF.ORDERS_WIDE",
            _direct_sources(
                "ORDER_TOTAL", "TMP_TOTAL", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_KJ20JIM48W"
            ),
        ),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    [edge] = result.edges
    assert edge.column_pairs == (("SUBTOTAL", "ORDER_TOTAL"),)


def test_a_table_grain_hop_makes_the_stitched_edge_table_grain() -> None:
    """The second hop reports no column sources — the REAL captured shape, where every
    `directSources` is empty. Composition must degrade to table grain, never carry the
    first hop's pairs through as if they described the whole chain: that would claim
    `SUBTOTAL → TMP_TOTAL` is a mapping into ORDERS_WIDE, about which the composed edge
    has no evidence at all."""
    rows = [
        (
            "DATAQ_DB.RETAIL.ORDERS_HEADER",
            "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_KJ20JIM48W",
            _direct_sources("TMP_TOTAL", "SUBTOTAL", "DATAQ_DB.RETAIL.ORDERS_HEADER"),
        ),
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_KJ20JIM48W", "DATAQ_DB.PERF.ORDERS_WIDE", None),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    [edge] = result.edges
    assert edge.column_pairs == ()


def _chain(scratch_hops: int) -> list[tuple[Any, ...]]:
    """`A → TEMP1 → … → TEMP{n} → B` as ACCESS_HISTORY rows."""
    nodes = ["DATAQ_DB.RETAIL.A"]
    nodes += [f"DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_T{i}" for i in range(scratch_hops)]
    nodes.append("DATAQ_DB.PERF.B")
    return [(nodes[i], nodes[i + 1], None) for i in range(len(nodes) - 1)]


def test_a_chain_at_the_depth_cap_still_stitches() -> None:
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(_chain(_EPHEMERAL_STITCH_MAX_DEPTH)), connection_config=_CONFIG
    )
    assert [(e.upstream.name, e.downstream.name) for e in result.edges] == [
        (_sf("RETAIL", "A"), _sf("PERF", "B"))
    ]


def test_a_chain_past_the_depth_cap_is_dropped_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded against scratch chains (#912 AC-2). Over-depth drops the edge — but the
    COUNTER is the point: the pre-#912 drop was silent, and establishing whether it was
    biting took manual archaeology against prod.

    The count is pinned EXACTLY at 1 (#1110 review): this is a single linear chain with
    one over-depth path, so it must be counted once — `_resolve` used to count it when
    the depth cap was exceeded, and `stitched_edges` counted it AGAIN when that same
    chain then resolved to nothing, logging 2 for one dropped chain. A `>= 1` assertion
    cannot see that regression; only the exact count can.
    """
    with _captured_lineage_logs(monkeypatch) as logs:
        result = SnowflakeLineageProvider().fetch_edges(
            _access_history_conn(_chain(_EPHEMERAL_STITCH_MAX_DEPTH + 1)),
            connection_config=_CONFIG,
        )
    assert result.edges == ()
    counters = next(e for e in logs if e["event"] == "warehouse_lineage_ephemeral_stitch")
    assert counters["ephemeral_chains_dropped"] == 1
    assert counters["stitched_edges"] == 0


def test_the_stitch_counters_are_logged_once_per_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ephemeral_rows_seen` / `stitched_edges` / `ephemeral_chains_dropped` — the
    explicit ask on #912, following the `dropped_names` precedent."""
    rows = [
        ("DATAQ_DB.RETAIL.ORDERS", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_X", None),
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_X", "DATAQ_DB.PERF.ORDERS_WIDE", None),
        ("DATAQ_DB.RETAIL.CUSTOMERS", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_DEAD_END", None),
    ]
    with _captured_lineage_logs(monkeypatch) as logs:
        SnowflakeLineageProvider().fetch_edges(
            _access_history_conn(rows), connection_config=_CONFIG
        )
    [counters] = [e for e in logs if e["event"] == "warehouse_lineage_ephemeral_stitch"]
    assert counters["path"] == "access_history"
    assert counters["ephemeral_rows_seen"] == 3
    assert counters["stitched_edges"] == 1
    assert counters["ephemeral_chains_dropped"] == 1


def test_a_scratch_cycle_terminates_without_emitting_anything() -> None:
    """A malformed/looping payload must not hang the worker: the memo is seeded BEFORE
    the recursion (the dbt port's cycle guard), so a back-edge resolves to nothing."""
    rows = [
        ("DATAQ_DB.RETAIL.A", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_1", None),
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_1", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_2", None),
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_2", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_1", None),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    assert result.edges == ()


def test_a_scratch_fan_out_reattaches_every_physical_descendant() -> None:
    """One scratch object feeding two real tables yields two stitched edges — the union
    of `up(TEMP) x down(TEMP)` the ticket describes, not just the first."""
    rows = [
        ("DATAQ_DB.RETAIL.ORDERS", "DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_X", None),
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_X", "DATAQ_DB.PERF.WIDE_A", None),
        ("DATAQ_DB.PERF.SNOWPARK_TEMP_TABLE_X", "DATAQ_DB.PERF.WIDE_B", None),
    ]
    result = SnowflakeLineageProvider().fetch_edges(
        _access_history_conn(rows), connection_config=_CONFIG
    )
    assert {(e.upstream.name, e.downstream.name) for e in result.edges} == {
        (_sf("RETAIL", "ORDERS"), _sf("PERF", "WIDE_A")),
        (_sf("RETAIL", "ORDERS"), _sf("PERF", "WIDE_B")),
    }
