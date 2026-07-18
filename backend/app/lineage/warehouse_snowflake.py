"""Snowflake warehouse-native lineage provider (#858, ADR 0034).

The tier ladder, richest first — chosen and ordered from the 2026-07-17 live spike
(#858 comments):

1. **``SNOWFLAKE.CORE.GET_LINEAGE``** (Enterprise+) — first-class server-side lineage
   traversal, object-domain aware, no JSON parsing. **Its absence is a CLEAN, catchable
   ``0A000 Unsupported feature 'Data Lineage'``** — the best preflight signal, so it is
   tried first and its failure descends the ladder rather than erroring the pull.
2. **``ACCOUNT_USAGE.ACCESS_HISTORY``** (Enterprise+) — query-derived column-level
   lineage (CTAS / INSERT / MERGE / COPY). **On a Standard account the view is present
   but SILENTLY EMPTY** (live-verified: 45,630 ``QUERY_HISTORY`` rows in 90d vs
   ``COUNT(*)=0`` here) — so emptiness cannot be read as "no lineage"; it is corroborated
   against ``QUERY_HISTORY`` to distinguish edition-gating from a genuinely idle account.
   ~2-3h latency, surfaced as ``freshness_lag``.
3. **``ACCOUNT_USAGE.OBJECT_DEPENDENCIES``** (all editions) — the view-level floor,
   captured working on the demo account (real RETAIL→STG→ANALYTICS chain, UPPER identity
   byte-identical to ``asset_identity``). Views/matviews/dynamic-tables only; no
   column detail.

Identities are built with :func:`asset_identity.format_snowflake_name` +
:func:`normalize_snowflake_account`, the SAME functions the suite-target resolver and
the dbt canonicalizer use, so a pulled edge endpoint joins an existing `assets` row
byte-for-byte with no fold (`lineage.warehouse` docstring).

Every ``FUNCTION``-domain endpoint is dropped: a dependency on a UDF is not table
lineage and has no asset identity.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.app.core.logging import get_logger
from backend.app.lineage.warehouse import (
    LineageEdgePair,
    LineageTier,
    WarehouseLineageResult,
    WarehouseLineageUnavailableError,
    dedupe_edges,
)
from backend.app.services.asset_identity import (
    AssetIdentity,
    format_snowflake_name,
    normalize_snowflake_account,
)

log = get_logger(__name__)

# Object domains that ARE tables/table-like (have an asset identity). FUNCTION,
# PROCEDURE, etc. are dropped — a dependency on them is not table lineage.
_TABLE_DOMAINS = frozenset(
    {"TABLE", "VIEW", "MATERIALIZED VIEW", "DYNAMIC TABLE", "EXTERNAL TABLE"}
)

# The 0A000 SQLSTATE Snowflake returns when a feature (Data Lineage / ACCESS_HISTORY on
# a lower edition) is not licensed — a clean, catchable preflight signal (the spike's
# key finding vs ACCESS_HISTORY's silent-empty).
_FEATURE_UNSUPPORTED_SQLSTATE = "0A000"


class SnowflakeLineageProvider:
    """`WarehouseLineageProvider` for Snowflake. Descends the tier ladder above."""

    source = "snowflake"
    # SNAPSHOT source: OBJECT_DEPENDENCIES is a current-state view with no event time, so
    # the floor tier is re-read whole and pruned each refresh (the ACCESS_HISTORY log
    # tier's own event-time watermark is a deferred Enterprise follow-up).
    is_incremental = False

    def fetch_edges(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        since: datetime | None = None,
    ) -> WarehouseLineageResult:
        namespace = self._namespace(connection_config)
        database = self._database(connection_config)
        skipped: list[str] = []

        # Tier 1: GET_LINEAGE. Its absence is a clean 0A000 — descend, don't fail.
        # The reason is carried from the exception, NOT hard-coded: on Enterprise the
        # function IS supported but its per-seed traversal is deferred (#858 follow-up),
        # so an Enterprise operator must not see a false "unsupported on this edition".
        try:
            edges = self._from_get_lineage(conn, namespace)
            return WarehouseLineageResult(
                edges=edges, tier=LineageTier.SNOWFLAKE_GET_LINEAGE, skipped_tiers=tuple(skipped)
            )
        except _FeatureUnsupportedError as exc:
            skipped.append(f"get_lineage: {exc}")

        # Tier 2: ACCESS_HISTORY. Present-but-empty on Standard — corroborate.
        try:
            access = self._from_access_history(conn, namespace, database)
            if access is not None:
                return WarehouseLineageResult(
                    edges=access,
                    tier=LineageTier.SNOWFLAKE_ACCESS_HISTORY,
                    freshness_lag="~2-3h (ACCOUNT_USAGE latency)",
                    skipped_tiers=tuple(skipped),
                )
            skipped.append("access_history: empty (edition-gated or no write history)")
        except _FeatureUnsupportedError as exc:
            # Carry the REAL reason (edition gate vs missing grant, #902) — the same
            # honesty rule the tier-1 skip already follows.
            skipped.append(f"access_history: {exc}")

        # Tier 3: OBJECT_DEPENDENCIES — the all-editions floor.
        try:
            floor = self._from_object_dependencies(conn, namespace, database)
        except Exception as exc:  # the floor failing means we learned nothing
            raise WarehouseLineageUnavailableError(
                "snowflake lineage unavailable: could not read OBJECT_DEPENDENCIES "
                f"({type(exc).__name__})"
            ) from exc
        return WarehouseLineageResult(
            edges=floor,
            tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
            # The per-tier skip reasons are constructed, stable strings (edition gate /
            # missing grant / deferred traversal — never raw connector text), so they
            # can be surfaced verbatim; a blanket "need Enterprise" would mislabel a
            # grant-shaped skip (#902).
            degraded_reason=(
                ("view-level lineage only — " + "; ".join(skipped)) if skipped else None
            ),
            skipped_tiers=tuple(skipped),
        )

    # ── identity ──────────────────────────────────────────────────────────────
    def _namespace(self, config: dict[str, object]) -> str:
        account = config.get("account")
        if not isinstance(account, str) or not account.strip():
            raise WarehouseLineageUnavailableError(
                "snowflake lineage unavailable: connection config has no account"
            )
        return f"snowflake://{normalize_snowflake_account(account)}"

    def _identity(self, namespace: str, database: str, schema: str, table: str) -> AssetIdentity:
        return AssetIdentity(
            namespace=namespace, name=format_snowflake_name(database, schema, table)
        )

    def _database(self, config: dict[str, object]) -> str:
        """The connection's configured database, folded to Snowflake's unquoted-UPPER
        (the case ACCOUNT_USAGE stores) — the pull's scope boundary (#908): a
        datasource connection speaks for ONE database, and the first unscoped live
        pull proved why (Snowpark ephemera, a dropped PERF schema, system views all
        materialized as browsable assets)."""
        database = config.get("database")
        if not isinstance(database, str) or not database.strip():
            raise WarehouseLineageUnavailableError(
                "snowflake lineage unavailable: connection config has no database"
            )
        return database.strip().upper()

    # ── tier 3: OBJECT_DEPENDENCIES (live-verified) ─────────────────────────────
    def _from_object_dependencies(
        self, conn: Any, namespace: str, database: str
    ) -> tuple[LineageEdgePair, ...]:
        # Both endpoints bound to the connection's database (#908) — exact-match
        # bound params, so SNOWFLAKE.TRUST_CENTER.* and other system deps never
        # enter the graph.
        rows = conn.execute(
            text(
                "SELECT referenced_database, referenced_schema, referenced_object_name, "
                "referenced_object_domain, referencing_database, referencing_schema, "
                "referencing_object_name, referencing_object_domain "
                "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
                "WHERE referenced_database = :db AND referencing_database = :db"
            ),
            {"db": database},
        ).all()
        edges: list[LineageEdgePair] = []
        for (
            up_db,
            up_schema,
            up_name,
            up_domain,
            down_db,
            down_schema,
            down_name,
            down_domain,
        ) in rows:
            if up_domain not in _TABLE_DOMAINS or down_domain not in _TABLE_DOMAINS:
                continue  # a FUNCTION/PROCEDURE endpoint is not table lineage
            if up_db is None or down_db is None:
                continue
            edges.append(
                LineageEdgePair(
                    upstream=self._identity(namespace, up_db, up_schema, up_name),
                    downstream=self._identity(namespace, down_db, down_schema, down_name),
                )
            )
        return dedupe_edges(edges)

    # ── tier 2: ACCESS_HISTORY (Enterprise; empty-but-present on Standard) ───────
    def _from_access_history(
        self, conn: Any, namespace: str, database: str
    ) -> tuple[LineageEdgePair, ...] | None:
        """Query-derived lineage at BOTH grains (#908, live-tuned on the Enterprise
        account): table edges from the ``base_objects_accessed`` x ``objects_modified``
        pairs, column pairs from ``objects_modified[].columns[].directSources``.

        Scope + hygiene, each proven necessary by the first live pull:

        * **Both endpoints ``objectDomain = 'Table'``, in SQL** — the real history is
          dominated by ``Stage`` → Table (COPY) and ``Table function`` → Table
          (GENERATOR) rows, which are not table lineage and were what materialized
          stages as assets.
        * **Both endpoints in the connection's database** (``SPLIT_PART`` exact match
          on a bound param — no ``LIKE`` wildcard surprises from ``_`` in the name).
        * **Bounded lookback (90d, matching the write-activity probe)** — never the
          whole 365d retention; ACCESS_HISTORY is corroborating-tier evidence, not an
          archaeology dig, and the dropped-schema ghosts (PERF) live in the old rows.
        * **Snowpark ephemera dropped in Python** (``SNOWPARK_TEMP_*`` tables/stages
          are session-scoped scratch, gone by the time anyone browses the asset).

        Returns ``None`` when the SCOPED view is empty AND the account shows write
        activity — either edition-gating (Standard's silent-empty, the spike finding)
        or a database whose DML simply never read a table (this account: all COPYs);
        both descend to the floor rather than asserting an empty graph."""
        try:
            rows = conn.execute(
                text(
                    "SELECT bo.value:objectName::string AS source_name, "
                    "om.value:objectName::string AS target_name, "
                    "TO_JSON(om.value:columns) AS target_columns "
                    "FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah, "
                    "LATERAL FLATTEN(input => ah.base_objects_accessed) bo, "
                    "LATERAL FLATTEN(input => ah.objects_modified) om "
                    "WHERE ah.objects_modified IS NOT NULL "
                    "AND ARRAY_SIZE(ah.objects_modified) > 0 "
                    "AND ah.query_start_time > DATEADD('day', -90, CURRENT_TIMESTAMP()) "
                    "AND bo.value:objectDomain = 'Table' "
                    "AND om.value:objectDomain = 'Table' "
                    "AND bo.value:objectName IS NOT NULL "
                    "AND om.value:objectName IS NOT NULL "
                    "AND SPLIT_PART(bo.value:objectName::string, '.', 1) = :db "
                    "AND SPLIT_PART(om.value:objectName::string, '.', 1) = :db"
                ),
                {"db": database},
            ).all()
        except Exception as exc:
            _reraise_if_feature_unsupported(exc)
            raise
        if not rows:
            return None if self._account_has_write_activity(conn) else ()
        edges: dict[tuple[str, str], tuple[AssetIdentity, AssetIdentity]] = {}
        pairs: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for source_name, target_name, target_columns in rows:
            up = self._identity_from_qualified(namespace, source_name)
            down = self._identity_from_qualified(namespace, target_name)
            if up is None or down is None or up.name == down.name:
                continue
            if self._is_ephemeral(up.name) or self._is_ephemeral(down.name):
                continue  # Snowpark session scratch — real rows, never real assets
            key = (up.name, down.name)
            edges[key] = (up, down)
            for col_up, col_down in self._column_pairs_from_json(
                target_columns, namespace=namespace, upstream=up
            ):
                pairs.setdefault(key, set()).add((col_up, col_down))
        return dedupe_edges(
            [
                LineageEdgePair(
                    upstream=up,
                    downstream=down,
                    column_pairs=tuple(sorted(pairs.get(key, ()))),
                )
                for key, (up, down) in edges.items()
            ]
        )

    def _column_pairs_from_json(
        self, target_columns: str | None, *, namespace: str, upstream: AssetIdentity
    ) -> list[tuple[str, str]]:
        """Extract ``(source_column, written_column)`` pairs from one
        ``objects_modified[].columns`` JSON blob, for the edge whose upstream is
        ``upstream`` (#908).

        Each written column carries ``directSources`` — the exact source columns the
        engine derived it from (Enterprise). A statement can read several tables, so a
        direct source contributes to THIS edge only when its table identity IS the
        edge's upstream — sources in other tables belong to those edges' own rows.
        A malformed entry is skipped, never fatal (the table grain must survive a
        JSON surprise)."""
        if not target_columns:
            return []
        try:
            columns = json.loads(target_columns)
        except (TypeError, ValueError):
            return []
        out: list[tuple[str, str]] = []
        if not isinstance(columns, list):
            return out
        for col in columns:
            if not isinstance(col, dict):
                continue
            written = col.get("columnName")
            sources = col.get("directSources")
            if not isinstance(written, str) or not isinstance(sources, list):
                continue
            for src in sources:
                if not isinstance(src, dict) or src.get("objectDomain") != "Table":
                    continue
                src_table = src.get("objectName")
                src_col = src.get("columnName")
                if not isinstance(src_table, str) or not isinstance(src_col, str):
                    continue
                ident = self._identity_from_qualified(namespace, src_table)
                if ident is None or ident.name != upstream.name:
                    continue
                out.append((src_col, written))
        return out

    @staticmethod
    def _is_ephemeral(qualified_name: str) -> bool:
        """Snowpark session-scratch objects (``SNOWPARK_TEMP_TABLE_…``, stages) — real
        rows in ACCESS_HISTORY, gone before anyone could browse the asset (#908)."""
        last = qualified_name.rsplit(".", 1)[-1]
        return last.startswith("SNOWPARK_TEMP_")

    def _account_has_write_activity(self, conn: Any) -> bool:
        """Cheap corroboration: any query in the last 90d. If ACCESS_HISTORY is empty
        yet the account has run queries, the emptiness is edition-gating (return the
        empty-is-suspicious signal), not a truly idle account."""
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY "
                "WHERE start_time > DATEADD('day', -90, CURRENT_TIMESTAMP())"
            )
        ).scalar()
        return bool(count and int(count) > 0)

    def _identity_from_qualified(
        self, namespace: str, qualified: str | None
    ) -> AssetIdentity | None:
        """Build an identity from a ``DB.SCHEMA.TABLE`` string (ACCESS_HISTORY /
        GET_LINEAGE return the qualified name whole). Returns ``None`` for a
        non-3-part name (a stage, a column-qualified ref) — not a table."""
        if not qualified:
            return None
        parts = qualified.split(".")
        if len(parts) != 3:
            return None
        return self._identity(namespace, parts[0], parts[1], parts[2])

    # ── tier 1: GET_LINEAGE (Enterprise; clean 0A000 when absent) ───────────────
    def _from_get_lineage(self, conn: Any, _namespace: str) -> tuple[LineageEdgePair, ...]:
        """Probe GET_LINEAGE once with a trivial call. Its 0A000 on a lower edition is
        raised as :class:`_FeatureUnsupportedError` so the ladder descends. When supported,
        the per-object traversal is the build's next slice (the demo account is
        Standard, so there is no live payload to test the traversal against yet); this
        slice establishes the clean-preflight descent that the spike proved."""
        try:
            conn.execute(
                text(
                    "SELECT 1 FROM TABLE(SNOWFLAKE.CORE.GET_LINEAGE("
                    "'SNOWFLAKE.ACCOUNT_USAGE.TABLES', 'TABLE', 'UPSTREAM', 1)) LIMIT 1"
                )
            ).all()
        except Exception as exc:
            _reraise_if_feature_unsupported(exc)
            raise
        # Supported but the per-seed traversal is deferred (#858 follow-up): descend to a
        # tier we can populate today rather than claim a graph we don't yet build.
        raise _FeatureUnsupportedError("get_lineage supported but per-seed traversal not yet built")


class _FeatureUnsupportedError(Exception):
    """Internal: a tier is edition-gated (Snowflake 0A000). Drives the ladder descent."""


def _sqlstate(exc: BaseException) -> str | None:
    """Snowflake's SQLSTATE off a connector error, tolerating the SQLAlchemy wrapper."""
    for obj in (exc, getattr(exc, "orig", None)):
        code = getattr(obj, "sqlstate", None)
        if isinstance(code, str):
            return code
    return None


_UNSUPPORTED_EDITION_MSG = "unsupported on this edition"


_NOT_AUTHORIZED_MSG = "not authorized (role lacks the ACCOUNT_USAGE / GET_LINEAGE grant)"


def _reraise_if_feature_unsupported(exc: BaseException) -> None:
    """Raise :class:`_FeatureUnsupportedError` if ``exc`` is Snowflake's edition-gate 0A000,
    matched by SQLSTATE (structured) OR the documented message text (belt-and-braces —
    the connector surfaces both, and the SQLSTATE is the reliable one).

    Authorization failures descend the ladder too (#902, found live): Snowflake grants
    are per-object — the ``SNOWFLAKE.GOVERNANCE_VIEWER`` database role authorizes
    ACCESS_HISTORY + OBJECT_DEPENDENCIES *without* the GET_LINEAGE probe's table — so
    an un-authorized tier is exactly as skippable as an edition-gated one. Snowflake
    deliberately blurs missing-object and missing-grant into one message (002003
    "does not exist or not authorized"), so that text IS the structured signal here.
    If every tier is denied, the floor's failure already reports unavailable with a
    classified reason — this never converts total denial into a silent empty.
    """
    if _sqlstate(exc) == _FEATURE_UNSUPPORTED_SQLSTATE or "Unsupported feature" in str(exc):
        # The edition gate → a stable, operator-legible reason (NOT the raw connector
        # text, which can be noisy). The deferred-traversal path raises its own message.
        raise _FeatureUnsupportedError(_UNSUPPORTED_EDITION_MSG) from exc
    if "does not exist or not authorized" in str(exc):
        raise _FeatureUnsupportedError(_NOT_AUTHORIZED_MSG) from exc
