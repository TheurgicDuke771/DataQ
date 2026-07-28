"""The ADR 0040 table-enumeration seam — `enumerate_tables` on both providers.

Identity rows ride the REAL captured catalog payloads
(`snowflake_tables_casing.json` / `uc_tables_casing.json`, #823 discipline): the
Snowflake capture is account-wide and includes NULL-catalog rows and the
SNOWFLAKE system database; the UC capture includes `samples` and
`information_schema` rows — the exact noise the enumerator's scope exists to
exclude. SQL-level scope (bound catalog param, table_type allowlist, system-
catalog exclusions) is pinned by predicate assertions on the captured SQL text,
because a fake connection cannot execute a WHERE clause.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.lineage.warehouse_snowflake import SnowflakeLineageProvider
from backend.app.lineage.warehouse_unity_catalog import UnityCatalogLineageProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "lineage_native"

_SF_CONFIG: dict[str, object] = {"account": "PVQSOEQ-ZGB34383", "database": "DATAQ_DB"}
_UC_CONFIG: dict[str, object] = {"workspace_url": "https://dbc-4492dde4-090c.cloud.databricks.com"}


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConn:
    """Captures the SQL + params and returns canned rows — the same double shape
    the lineage-tier tests use (a fake cannot execute a WHERE clause, so the
    rows are what the real query WOULD return)."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.sql: str = ""
        self.params: dict[str, Any] = {}

    def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.sql = str(clause)
        self.params = params or {}
        return _FakeResult(self.rows)


def _sf_fixture_rows() -> list[tuple[Any, ...]]:
    """The real capture, narrowed to what the bound-catalog predicate would pass
    (the account-wide rows prove the OTHER catalogs exist to be excluded)."""
    raw = json.loads((_FIXTURES / "snowflake_tables_casing.json").read_text())
    return [(r["TABLE_SCHEMA"], r["TABLE_NAME"]) for r in raw if r["TABLE_CATALOG"] == "DATAQ_DB"]


class TestSnowflakeEnumeration:
    def test_real_capture_rows_become_engine_case_identities(self) -> None:
        conn = _FakeConn(_sf_fixture_rows())
        idents = SnowflakeLineageProvider().enumerate_tables(conn, connection_config=_SF_CONFIG)
        assert {i.name for i in idents} == {
            "DATAQ_DB.ANALYTICS.MART_CUSTOMER_ORDERS",
            "DATAQ_DB.RETAIL.SETTLEMENTS",
            "DATAQ_DB.ANALYTICS_STG.STG_ORDERS",
        }
        # Byte-for-byte the namespace a suite target on the same account produces.
        assert all(i.namespace == "snowflake://PVQSOEQ-ZGB34383" for i in idents)

    def test_null_rows_from_the_real_capture_are_skipped_not_materialized(self) -> None:
        """The account-wide capture contains NULL-catalog rows — a NULLed row must
        never become a 'DATAQ_DB.None.None' asset."""
        conn = _FakeConn([(None, None), ("RETAIL", "SETTLEMENTS"), ("RETAIL", None)])
        idents = SnowflakeLineageProvider().enumerate_tables(conn, connection_config=_SF_CONFIG)
        assert [i.name for i in idents] == ["DATAQ_DB.RETAIL.SETTLEMENTS"]

    def test_snowpark_scratch_never_enumerates(self) -> None:
        conn = _FakeConn([("PERF", "SNOWPARK_TEMP_TABLE_K0ADU7Z7AS"), ("RETAIL", "ORDERS_HEADER")])
        idents = SnowflakeLineageProvider().enumerate_tables(conn, connection_config=_SF_CONFIG)
        assert [i.name for i in idents] == ["DATAQ_DB.RETAIL.ORDERS_HEADER"]

    def test_sql_scope_predicates_are_present(self) -> None:
        """A fake cannot run the WHERE clause, so the scope is pinned textually:
        bound catalog param (#911 one-database rule), INFORMATION_SCHEMA excluded,
        the temporary-excluding TABLE_TYPE allowlist, deterministic order."""
        conn = _FakeConn([])
        SnowflakeLineageProvider().enumerate_tables(conn, connection_config=_SF_CONFIG)
        assert "table_catalog = :db" in conn.sql
        assert conn.params["db"] == "DATAQ_DB"
        assert "table_schema != 'INFORMATION_SCHEMA'" in conn.sql
        # Positive allowlist present; TEMPORARY TABLE (Snowflake's actual temp
        # vocabulary — review fix, the earlier 'LOCAL TEMPORARY' string tested
        # nothing real) must not be an allowed type.
        assert "'BASE TABLE'" in conn.sql and "'TEMPORARY TABLE'" not in conn.sql
        assert "ORDER BY table_schema, table_name" in conn.sql
        # Budget-correctness (review finding): every exclusion must precede the
        # LIMIT, or excluded rows consume the cap+1 budget and truncation
        # detection silently never fires. ESCAPE makes the underscores literal.
        assert "table_name NOT LIKE 'SNOWPARK\\_TEMP\\_%' ESCAPE '\\'" in conn.sql
        assert "table_schema IS NOT NULL AND table_name IS NOT NULL" in conn.sql

    def test_limit_is_pushed_into_the_query(self) -> None:
        conn = _FakeConn([])
        SnowflakeLineageProvider().enumerate_tables(conn, connection_config=_SF_CONFIG, limit=51)
        assert "LIMIT :lim" in conn.sql
        assert conn.params["lim"] == 51


class TestUnityCatalogEnumeration:
    def test_real_capture_rows_become_lower_case_identities(self) -> None:
        raw = json.loads((_FIXTURES / "uc_tables_casing.json").read_text())
        rows = [
            (r["table_catalog"], r["table_schema"], r["table_name"])
            for r in raw
            if r["table_catalog"] not in ("system", "samples", "__databricks_internal")
            and r["table_schema"] != "information_schema"
        ]
        conn = _FakeConn(rows)
        idents = UnityCatalogLineageProvider().enumerate_tables(conn, connection_config=_UC_CONFIG)
        assert idents, "the capture holds real workspace tables"
        assert all(
            i.namespace == "unitycatalog://dbc-4492dde4-090c.cloud.databricks.com" for i in idents
        )
        # UC's information_schema returns lower-case; identities keep it verbatim.
        assert all(i.name == i.name.lower() for i in idents)

    def test_sql_scope_excludes_system_catalogs_and_information_schema(self) -> None:
        conn = _FakeConn([])
        UnityCatalogLineageProvider().enumerate_tables(conn, connection_config=_UC_CONFIG)
        assert "system.information_schema.tables" in conn.sql
        assert "table_catalog NOT IN ('system', 'samples', '__databricks_internal')" in conn.sql
        assert "table_schema != 'information_schema'" in conn.sql
        assert "table_catalog IS NOT NULL" in conn.sql  # budget-correct NULL exclusion
        assert "'STREAMING_TABLE'" in conn.sql

    def test_null_rows_are_skipped(self) -> None:
        conn = _FakeConn([("workspace", None, "t"), ("workspace", "s", "t")])
        idents = UnityCatalogLineageProvider().enumerate_tables(conn, connection_config=_UC_CONFIG)
        assert [i.name for i in idents] == ["workspace.s.t"]
