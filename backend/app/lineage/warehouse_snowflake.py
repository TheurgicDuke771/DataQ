"""Snowflake warehouse-native lineage provider (#858, ADR 0034)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn

from sqlalchemy import text

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.lineage.warehouse import (
    MAX_COLUMN_PAIRS_PER_EDGE,
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

# The 0A000 SQLSTATE Snowflake returns when a feature (Data Lineage / ACCESS_HISTORY on a lower
# edition) is not licensed — a clean.
_FEATURE_UNSUPPORTED_SQLSTATE = "0A000"

# Bounded ACCESS_HISTORY lookback (#908): the DML log is read this many days back, bound as a query
# param.
_ACCESS_HISTORY_LOOKBACK_DAYS = 90

# The shared per-edge column-pair cap (#901/#908) — see `warehouse`.
_MAX_COLUMN_PAIRS_PER_EDGE = MAX_COLUMN_PAIRS_PER_EDGE

# ACCESS_HISTORY objectDomain values that are table-like (per-kind, title case — distinct from
# OBJECT_DEPENDENCIES' UPPER domain vocabulary in `_TABLE_DOMAINS`).
_ACCESS_HISTORY_TABLE_DOMAINS = frozenset(
    {"Table", "View", "Materialized view", "Dynamic table", "External table"}
)

# `SNOWFLAKE.CORE.GET_LINEAGE` object domains that are table-like — a THIRD spelling of the same
# vocabulary (UPPER with UNDERSCORES, live-captured 2026-07-28: TABLE / VIEW / DYNAMIC_TABLE /
# STAGE).
_GET_LINEAGE_TABLE_DOMAINS = frozenset(
    {"TABLE", "VIEW", "DYNAMIC_TABLE", "MATERIALIZED_VIEW", "EXTERNAL_TABLE"}
)

# GET_LINEAGE redacts objects the calling role cannot see: the name parts come back as `***` with
# `*_status = 'MASKED'` (live-captured — a masked STAGE upstream of ORDERS_HEADER).
_MASKED_STATUS = "MASKED"
_REDACTED_NAME_PART = "***"

# How many chained ephemeral hops the #912 stitch will collapse through (A → TEMP1 → … → TEMPn → B).
_EPHEMERAL_STITCH_MAX_DEPTH = 5


def _is_masked(status: Any, *parts: Any) -> bool:
    """True when GET_LINEAGE redacted this endpoint (the role cannot see the object)."""
    return status == _MASKED_STATUS or any(part == _REDACTED_NAME_PART for part in parts)


def _is_ephemeral(qualified_name: str) -> bool:
    """Snowpark session-scratch objects (``SNOWPARK_TEMP_TABLE_…``, stages) — real rows
    in ACCESS_HISTORY / GET_LINEAGE, gone before anyone could browse the asset (#908).
    """
    last = qualified_name.rsplit(".", 1)[-1]
    return last.startswith("SNOWPARK_TEMP_")


class _EdgeSet:
    """An insertion-ordered ``(upstream, downstream) → identities + column pairs`` bag."""

    def __init__(self) -> None:
        self._edges: dict[
            tuple[str, str], tuple[AssetIdentity, AssetIdentity, set[tuple[str, str]]]
        ] = {}

    def add(
        self,
        upstream: AssetIdentity,
        downstream: AssetIdentity,
        pairs: Iterable[tuple[str, str]] = (),
    ) -> None:
        key = (upstream.name, downstream.name)
        entry = self._edges.get(key)
        if entry is None:
            entry = (upstream, downstream, set())
            self._edges[key] = entry
        bucket = entry[2]
        for pair in pairs:
            if len(bucket) >= _MAX_COLUMN_PAIRS_PER_EDGE:
                break
            bucket.add(pair)

    def __len__(self) -> int:
        return len(self._edges)

    def rows(self) -> list[tuple[AssetIdentity, AssetIdentity, frozenset[tuple[str, str]]]]:
        return [(up, down, frozenset(pairs)) for up, down, pairs in self._edges.values()]

    def to_edges(self) -> tuple[LineageEdgePair, ...]:
        return dedupe_edges(
            [
                LineageEdgePair(upstream=up, downstream=down, column_pairs=tuple(sorted(pairs)))
                for up, down, pairs in self._edges.values()
            ]
        )


def _compose_pairs(
    first: frozenset[tuple[str, str]], second: frozenset[tuple[str, str]]
) -> frozenset[tuple[str, str]]:
    """Compose two hops' column pairs over the bridging (scratch) column."""
    if not first or not second:
        return frozenset()
    by_bridge: dict[str, list[str]] = {}
    for bridge, out_col in second:
        by_bridge.setdefault(bridge, []).append(out_col)
    composed: set[tuple[str, str]] = set()
    for in_col, bridge in first:
        for out_col in by_bridge.get(bridge, ()):
            if len(composed) >= _MAX_COLUMN_PAIRS_PER_EDGE:
                return frozenset(composed)
            composed.add((in_col, out_col))
    return frozenset(composed)


class _EphemeralStitch:
    """Collapse ``A → TEMP(→TEMP)* → B`` chains into ``A → B`` (#912)."""

    def __init__(self, raw: _EdgeSet) -> None:
        self._rows = raw.rows()
        # downstream adjacency FROM each ephemeral node — the only walk direction the
        # stitch needs (a physical row's own upstream is already an endpoint).
        self._out: dict[str, list[tuple[AssetIdentity, frozenset[tuple[str, str]]]]] = {}
        for up, down, pairs in self._rows:
            if _is_ephemeral(up.name):
                self._out.setdefault(up.name, []).append((down, pairs))
        self._memo: dict[str, list[tuple[AssetIdentity, frozenset[tuple[str, str]], int]]] = {}
        # Whether resolving `node` encountered (directly or transitively through a child) at least
        # one over-depth cut that already accounted for `node` coming back empty.
        self._had_depth_drop: dict[str, bool] = {}
        self.rows_seen = 0
        self.stitched = 0
        self.dropped = 0

    def _resolve(self, node: str) -> list[tuple[AssetIdentity, frozenset[tuple[str, str]], int]]:
        """The PHYSICAL nodes reachable through ephemeral ``node``, with the composed
        column pairs and the ephemeral-hop count of each path (``node`` itself = 1).
        """
        cached = self._memo.get(node)
        if cached is not None:
            return cached
        self._memo[node] = []  # cycle guard: a back-edge to `node` sees this empty seed
        out: list[tuple[AssetIdentity, frozenset[tuple[str, str]], int]] = []
        had_drop = False
        for child, pairs in self._out.get(node, ()):
            if not _is_ephemeral(child.name):
                out.append((child, pairs, 1))
                continue
            for ident, onward, hops in self._resolve(child.name):
                if hops + 1 > _EPHEMERAL_STITCH_MAX_DEPTH:
                    self.dropped += 1  # over-depth: dropped, but never silently
                    had_drop = True
                    continue
                out.append((ident, _compose_pairs(pairs, onward), hops + 1))
            if self._had_depth_drop.get(child.name):
                had_drop = True  # propagate: the cut happened deeper in this branch
        self._memo[node] = out
        self._had_depth_drop[node] = had_drop
        return out

    def stitched_edges(self) -> _EdgeSet:
        stitched = _EdgeSet()
        for up, down, pairs in self._rows:
            up_ephemeral = _is_ephemeral(up.name)
            down_ephemeral = _is_ephemeral(down.name)
            if up_ephemeral or down_ephemeral:
                self.rows_seen += 1
            if up_ephemeral:
                # An interior hop — reached (and composed) from whatever physical row feeds it.
                continue
            if not down_ephemeral:
                stitched.add(up, down, pairs)
                continue
            resolved = self._resolve(down.name)
            if not resolved:
                # Only count here when `_resolve` did NOT already count this — a chain cut for depth
                # is counted once, at the point `_resolve` observed the cut.
                if not self._had_depth_drop.get(down.name, False):
                    self.dropped += 1  # dead-ends in scratch — nothing real to attach to
                continue
            for ident, onward, _hops in resolved:
                stitched.add(up, ident, _compose_pairs(pairs, onward))
                self.stitched += 1
        return stitched


def _stitch_ephemera(raw: _EdgeSet, *, path: str) -> tuple[LineageEdgePair, ...]:
    """Run the #912 stitch over ``raw`` and log its counters once per pull."""
    stitch = _EphemeralStitch(raw)
    edges = stitch.stitched_edges()
    if stitch.rows_seen or stitch.dropped:
        log.info(
            "warehouse_lineage_ephemeral_stitch",
            source="snowflake",
            path=path,
            ephemeral_rows_seen=stitch.rows_seen,
            stitched_edges=stitch.stitched,
            ephemeral_chains_dropped=stitch.dropped,
        )
    return edges.to_edges()


@dataclass(frozen=True)
class _GetLineageTraversal:
    """One GET_LINEAGE tier pass: the edges it produced, and how much of the traversal
    actually ran (#1109 review).
    """

    edges: tuple[LineageEdgePair, ...]
    calls: int
    failed_calls: int
    unclassified_failures: int = 0


class SnowflakeLineageProvider:
    """`WarehouseLineageProvider` for Snowflake. Descends the tier ladder above."""

    source = "snowflake"
    # SNAPSHOT source: OBJECT_DEPENDENCIES is a current-state view with no event time.
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
        # Set by ANY tier that was skipped or half-traversed for a transient reason.
        partial = False

        # Tier 1: GET_LINEAGE.
        try:
            top = self._from_get_lineage(conn, namespace, connection_config=connection_config)
        except _FeatureUnsupportedError as exc:
            skipped.append(f"get_lineage: {_skip_reason(exc)}")
            partial = partial or exc.transient
            top = None

        if top is not None:
            if top.failed_calls:
                # A partial SUCCESS is still partial (#1109 review): before this, a run that lost
                # calls 2..2N — up to a majority of them.
                partial = partial or bool(top.unclassified_failures)
                skipped.append(
                    f"get_lineage: {top.failed_calls} of {top.calls} traversal call(s) "
                    "failed — partial"
                    + (
                        _TRANSIENT_SKIP_SUFFIX
                        if top.unclassified_failures
                        else " (per-object denial — this role cannot read those objects)"
                    )
                )
            try:
                floor_for_top = self._from_object_dependencies(conn, namespace, database)
            except Exception as exc:
                # #1228: this used to raise WarehouseLineageUnavailableError here, discarding a
                # SUCCESSFUL GET_LINEAGE traversal — `top.edges`.
                # #1264/#1307: name OBJECT_DEPENDENCIES's own grant directly at classification
                # time (not by `==`-matching the classifier's generic reason afterwards, which
                # silently stops substituting the moment that generic text is ever enriched).
                reason = _feature_unsupported_reason(
                    exc, not_authorized_label="OBJECT_DEPENDENCIES"
                )
                if reason is not None:
                    skipped.append(f"object_dependencies: {reason}")
                else:
                    skipped.append(
                        f"object_dependencies: could not read floor ({type(exc).__name__})"
                        + _TRANSIENT_SKIP_SUFFIX
                    )
                return WarehouseLineageResult(
                    edges=top.edges,
                    tier=LineageTier.SNOWFLAKE_GET_LINEAGE,
                    degraded_reason="floor unavailable — " + "; ".join(skipped),
                    skipped_tiers=tuple(skipped),
                    # `partial` (#1109) folds in a confirmed-vs-unclassified GET_LINEAGE blip from
                    # EARLIER in this same call (review finding on #1263: the first version of this
                    # branch dropped it, so a traversal already known-incomplete from its own per-
                    # seed failures could still be marked prunable just because the floor's
                    # SEPARATE failure happened to classify as confirmed).
                    prunable=reason is not None and not partial,
                )
            merged_top: dict[tuple[str, str], LineageEdgePair] = {
                (e.upstream.name, e.downstream.name): e for e in floor_for_top
            }
            merged_top.update({(e.upstream.name, e.downstream.name): e for e in top.edges})
            return WarehouseLineageResult(
                edges=tuple(merged_top.values()),
                tier=LineageTier.SNOWFLAKE_GET_LINEAGE,
                degraded_reason=("partial traversal — " + "; ".join(skipped)) if skipped else None,
                skipped_tiers=tuple(skipped),
                prunable=not partial,
            )

        # The two remaining sources are COMPLEMENTARY truths, not alternatives (#911 review — the
        # exclusive ladder was the deep defect): OBJECT_DEPENDENCIES is the current-state VIEW-
        # dependency graph (a view is never a DML write, so it can never appear in ACCESS_HISTORY's
        # objects_modified), and ACCESS_HISTORY is the DML event log (a table→table INSERT leaves
        # no dependency-view row).
        try:
            floor = self._from_object_dependencies(conn, namespace, database)
        except Exception as exc:  # the floor failing means we learned nothing
            raise WarehouseLineageUnavailableError(self._unavailable_reason(exc, skipped)) from exc

        dml: tuple[LineageEdgePair, ...] = ()
        try:
            dml = self._from_access_history(conn, namespace, database)
        except _FeatureUnsupportedError as exc:
            # Carry the REAL reason (edition gate vs missing grant, #902) — the same
            # honesty rule the tier-1 skip already follows. #1309/#1307: `exc` is already
            # labelled ACCESS_HISTORY's own grant by `_from_access_history` at raise time
            # (not `==`-matched against the generic GET_LINEAGE wording here) — GET_LINEAGE
            # may have already failed for an unrelated reason by the time this tier is
            # even reached.
            skipped.append(
                f"access_history: {exc}{_TRANSIENT_SKIP_SUFFIX if exc.transient else ''}"
            )
            partial = partial or exc.transient
        if not dml and not any(s.startswith("access_history") for s in skipped):
            # Scoped-empty is a normal state (an all-COPY database, or one idle in the window), NOT
            # evidence of edition gating.
            skipped.append(
                "access_history: no table-to-table DML in the scoped 90d window "
                "(all-COPY/idle databases and Standard edition all look like this)"
            )

        # Union, DML-side wins per edge pair (it can carry column pairs; a view-dep
        # edge never does). Both sides are already deduped and scoped.
        merged: dict[tuple[str, str], LineageEdgePair] = {
            (e.upstream.name, e.downstream.name): e for e in floor
        }
        merged.update({(e.upstream.name, e.downstream.name): e for e in dml})
        tier = (
            LineageTier.SNOWFLAKE_ACCESS_HISTORY
            if dml
            else LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
        )
        return WarehouseLineageResult(
            edges=tuple(merged.values()),
            tier=tier,
            # ACCOUNT_USAGE latency qualifies the DML half of the union; the view half
            # is current-state.
            freshness_lag="~2-3h (ACCOUNT_USAGE latency)" if dml else None,
            # The per-tier skip reasons are constructed, stable strings (edition gate / missing
            # grant / deferred traversal / scoped-empty — never raw connector text).
            degraded_reason=(
                (
                    ("column detail limited — " if dml else "view-level lineage only — ")
                    + "; ".join(skipped)
                )
                if skipped
                else None
            ),
            skipped_tiers=tuple(skipped),
            prunable=not partial,
        )

    @staticmethod
    def _unavailable_reason(exc: Exception, skipped: list[str]) -> str:
        """The classified `WarehouseLineageUnavailableError` message for a floor failure."""
        reason = (
            "snowflake lineage unavailable: could not read OBJECT_DEPENDENCIES "
            f"({type(exc).__name__})"
        )
        return f"{reason}; after {'; '.join(skipped)}" if skipped else reason

    # ── identity ──────────────────────────────────────────────────────────────
    def enumerate_tables(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        limit: int | None = None,
    ) -> tuple[AssetIdentity, ...]:
        """ADR 0040 — enumerate the connection database's tables from
        ``INFORMATION_SCHEMA.TABLES``.
        """
        namespace = self._namespace(connection_config)
        database = self._database(connection_config)
        # Every exclusion lives in the WHERE clause, BEFORE the LIMIT (review finding on this PR): a
        # Python-side filter after a SQL LIMIT lets excluded rows consume the cap+1 budget.
        sql = (
            "SELECT table_schema, table_name FROM INFORMATION_SCHEMA.TABLES"
            " WHERE table_catalog = :db"
            " AND table_schema IS NOT NULL AND table_name IS NOT NULL"
            " AND table_schema != 'INFORMATION_SCHEMA'"
            # The ephemera pattern is a BOUND PARAM, never a literal: the snowflake connector's
            # pyformat paramstyle reads a raw % in a text() statement as a format placeholder and
            # raises ProgrammingError (#1111 — found live; the fake-conn tests cannot execute real
            # SQL, the #823 driver-boundary class again).
            " AND table_name NOT LIKE :ephemeral ESCAPE '!'"
            " AND table_type IN ('BASE TABLE', 'VIEW', 'MATERIALIZED VIEW', 'EXTERNAL TABLE')"
            " ORDER BY table_schema, table_name"
        )
        # `!` as the LIKE escape char: backslash is an escape in BOTH the connector's param pipeline
        # AND Snowflake's string parser.
        params: dict[str, object] = {"db": database, "ephemeral": "SNOWPARK!_TEMP!_%"}
        if limit is not None:
            sql += " LIMIT :lim"
            params["lim"] = int(limit)
        rows = conn.execute(text(sql), params).all()  # type: ignore[attr-defined]
        # The SQL predicates above are the budget-correct filter.
        return tuple(
            self._identity(namespace, database, schema, table)
            for schema, table in rows
            if schema and table and not _is_ephemeral(table)
        )

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
        """The connection's configured database, folded to Snowflake's unquoted-UPPER (the case
        ACCOUNT_USAGE stores) — the pull's scope boundary (#908): a datasource connection speaks
        for ONE database, and the first unscoped live pull proved why (Snowpark ephemera, a
        dropped PERF schema, system views all materialized as browsable assets).
        """
        database = config.get("database")
        if not isinstance(database, str) or not database.strip():
            raise WarehouseLineageUnavailableError(
                "snowflake lineage unavailable: connection config has no database"
            )
        database = database.strip()
        # The same quote-strip-else-UPPER rule the identity formatter applies (#911 review): a
        # quoted database ("DataQ_Db") is stored by ACCOUNT_USAGE in its exact inner case.
        if len(database) >= 2 and database.startswith('"') and database.endswith('"'):
            return database[1:-1]
        return database.upper()

    # ── tier 3: OBJECT_DEPENDENCIES (live-verified) ─────────────────────────────
    def _from_object_dependencies(
        self, conn: Any, namespace: str, database: str
    ) -> tuple[LineageEdgePair, ...]:
        # At least ONE endpoint bound to the connection's database (#908) — OR.
        rows = conn.execute(
            text(
                "SELECT referenced_database, referenced_schema, referenced_object_name, "
                "referenced_object_domain, referencing_database, referencing_schema, "
                "referencing_object_name, referencing_object_domain "
                "FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES "
                "WHERE referenced_database = :db OR referencing_database = :db"
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

    # ── ACCESS_HISTORY: the DML event log (Enterprise; empty-but-present on Standard) ─
    def _from_access_history(
        self, conn: Any, namespace: str, database: str
    ) -> tuple[LineageEdgePair, ...]:
        """Query-derived DML lineage at BOTH grains (#908, live-tuned): table edges
        from the ``base_objects_accessed`` x ``objects_modified`` pairs, column pairs
        from ``objects_modified[].columns[].directSources``.
        """
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
                    "AND ah.query_start_time > DATEADD('day', -:lookback, CURRENT_TIMESTAMP()) "
                    "AND bo.value:objectDomain IN ('Table', 'View', 'Materialized view', "
                    "'Dynamic table', 'External table') "
                    "AND om.value:objectDomain IN ('Table', 'View', 'Materialized view', "
                    "'Dynamic table', 'External table') "
                    "AND bo.value:objectName IS NOT NULL "
                    "AND om.value:objectName IS NOT NULL "
                    "AND (SPLIT_PART(bo.value:objectName::string, '.', 1) = :db "
                    "OR SPLIT_PART(om.value:objectName::string, '.', 1) = :db)"
                ),
                {"db": database, "lookback": _ACCESS_HISTORY_LOOKBACK_DAYS},
            ).all()
        except Exception as exc:
            _reraise_confirmed_or_transient(
                exc, "call failed", not_authorized_label="ACCESS_HISTORY"
            )
        raw = _EdgeSet()
        # The bo x om cross-join repeats each statement's columns blob once per base object, and
        # repeated statements repeat it again.
        parsed_pairs_by_blob: dict[str, dict[str, list[tuple[str, str]]]] = {}
        dropped_names = 0
        for source_name, target_name, target_columns in rows:
            up = self._identity_from_qualified(namespace, source_name)
            down = self._identity_from_qualified(namespace, target_name)
            if up is None or down is None:
                dropped_names += 1  # non-3-part name (e.g. a dotted quoted identifier)
                continue
            if up.name == down.name:
                continue
            # Ephemeral endpoints are KEPT here (#912) — the stitch below needs the
            # interior hops to reattach A→B, and drops the scratch identities itself.
            pairs: Iterable[tuple[str, str]] = ()
            if target_columns:
                if target_columns not in parsed_pairs_by_blob:
                    parsed_pairs_by_blob[target_columns] = self._pairs_by_source_table(
                        target_columns, namespace=namespace
                    )
                pairs = parsed_pairs_by_blob[target_columns].get(up.name, ())
            raw.add(up, down, pairs)
        if dropped_names:
            log.info(
                "warehouse_lineage_unparseable_names_dropped",
                source=self.source,
                rows=dropped_names,
            )
        return _stitch_ephemera(raw, path="access_history")

    def _pairs_by_source_table(
        self, target_columns: str, *, namespace: str
    ) -> dict[str, list[tuple[str, str]]]:
        """Parse one ``objects_modified[].columns`` JSON blob ONCE into
        ``{source_table_identity_name: [(source_column, written_column), …]}`` (#908).
        """
        try:
            columns = json.loads(target_columns)
        except (TypeError, ValueError):
            return {}
        out: dict[str, list[tuple[str, str]]] = {}
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
                if not isinstance(src, dict) or src.get("objectDomain") not in (
                    _ACCESS_HISTORY_TABLE_DOMAINS
                ):
                    continue
                src_table = src.get("objectName")
                src_col = src.get("columnName")
                if not isinstance(src_table, str) or not isinstance(src_col, str):
                    continue
                ident = self._identity_from_qualified(namespace, src_table)
                if ident is None:
                    continue
                out.setdefault(ident.name, []).append((src_col, written))
        return out

    def _identity_from_qualified(
        self, namespace: str, qualified: str | None
    ) -> AssetIdentity | None:
        """Build an identity from a ``DB.SCHEMA.TABLE`` string (ACCESS_HISTORY /
        GET_LINEAGE return the qualified name whole). Returns ``None`` for a
        non-3-part name (a stage, a column-qualified ref) — not a table.
        """
        if not qualified:
            return None
        parts = qualified.split(".")
        if len(parts) != 3:
            return None
        return self._identity(namespace, parts[0], parts[1], parts[2])

    # ── tier 1: GET_LINEAGE (Enterprise; clean 0A000 when absent) ───────────────
    def _from_get_lineage(
        self, conn: Any, namespace: str, *, connection_config: dict[str, object]
    ) -> _GetLineageTraversal:
        """Per-seed ``SNOWFLAKE.CORE.GET_LINEAGE`` traversal (#892, live-captured
        2026-07-28 against prod Enterprise).
        """
        cap = get_settings().warehouse_lineage_max_seeds
        try:
            seeds = self.enumerate_tables(
                conn,
                connection_config=connection_config,
                # cap+1 so overflow is DETECTABLE (ADR 0040 — the caller owns the
                # honesty of any truncation, the enumerator stays cap-blind).
                limit=(cap + 1) if cap > 0 else None,
            )
        except Exception as exc:
            # No seed list → no traversal.
            _reraise_confirmed_or_transient(exc, "seed enumeration failed")
        if cap > 0 and len(seeds) > cap:
            log.warning(
                "get_lineage_seeds_truncated",
                source=self.source,
                cap=cap,
                enumerated=len(seeds),
            )
            seeds = seeds[:cap]
        if not seeds:
            raise _FeatureUnsupportedError("no seed tables to traverse")

        raw = _EdgeSet()
        first_call = True
        seed_failures = 0
        unclassified_failures = 0
        for seed in seeds:
            for direction in ("UPSTREAM", "DOWNSTREAM"):
                try:
                    rows = conn.execute(
                        text(
                            "SELECT source_object_database, source_object_schema, "
                            "source_object_name, source_object_domain, source_status, "
                            "source_column_name, target_object_database, "
                            "target_object_schema, target_object_name, "
                            "target_object_domain, target_status, target_column_name "
                            "FROM TABLE(SNOWFLAKE.CORE.GET_LINEAGE(:obj, 'TABLE', :dir, 2))"
                        ),
                        {"obj": seed.name, "dir": direction},
                    ).all()
                except Exception as exc:
                    if first_call:
                        # Edition gate / missing grant on the very first call → the ladder descends
                        # exactly as the old preflight probe did.
                        _reraise_confirmed_or_transient(exc, "call failed")
                    seed_failures += 1
                    # Classify it too (#1109 review).
                    if _feature_unsupported_reason(exc) is None:
                        unclassified_failures += 1
                    continue
                finally:
                    first_call = False
                self._collect_get_lineage_rows(rows, namespace=namespace, into=raw)
        if seed_failures:
            log.warning(
                "get_lineage_seed_failures",
                source=self.source,
                seeds=len(seeds),
                failed_calls=seed_failures,
                unclassified_calls=unclassified_failures,
            )
        edges = _stitch_ephemera(raw, path="get_lineage")
        if not edges:
            # Nothing observed, checked on the POST-stitch physical edge set (#1110 review): `raw`
            # can be non-empty purely from ephemeral (SNOWPARK_TEMP_*) rows the stitch then
            # collapses to nothing, and checking `raw` would have returned this confident-but-wrong
            # `()` as a top-tier success instead of descending — which prunes the floor's real
            # graph outright under this snapshot-regime provider.
            raise _FeatureUnsupportedError(
                f"no lineage rows for {len(seeds)} seed table(s)"
                + (f" ({seed_failures} failed call(s))" if seed_failures else ""),
                # A CLEAN empty traversal is a confirmed observation of this tier, and so is one
                # whose failures were all confirmed per-object denials.
                transient=bool(unclassified_failures),
            )
        return _GetLineageTraversal(
            edges=edges,
            calls=len(seeds) * 2,
            failed_calls=seed_failures,
            unclassified_failures=unclassified_failures,
        )

    def _collect_get_lineage_rows(
        self, rows: Iterable[Any], *, namespace: str, into: _EdgeSet
    ) -> None:
        """Fold one GET_LINEAGE result into ``into`` — one DIRECT edge per row."""
        for (
            up_db,
            up_schema,
            up_name,
            up_domain,
            up_status,
            up_column,
            down_db,
            down_schema,
            down_name,
            down_domain,
            down_status,
            down_column,
        ) in rows:
            if _is_masked(up_status, up_db, up_schema, up_name) or _is_masked(
                down_status, down_db, down_schema, down_name
            ):
                continue  # redacted endpoint — `***` must never become an asset
            if (
                up_domain not in _GET_LINEAGE_TABLE_DOMAINS
                or down_domain not in _GET_LINEAGE_TABLE_DOMAINS
            ):
                continue  # STAGE and friends are not table lineage
            if not all((up_db, up_schema, up_name, down_db, down_schema, down_name)):
                continue
            up = self._identity(namespace, up_db, up_schema, up_name)
            down = self._identity(namespace, down_db, down_schema, down_name)
            if up.name == down.name:
                continue  # self-edge — not lineage
            pairs = (
                ((up_column, down_column),)
                if isinstance(up_column, str) and isinstance(down_column, str)
                else ()
            )
            into.add(up, down, pairs)


class _FeatureUnsupportedError(Exception):
    """Internal: a tier could not answer — edition-gated (Snowflake 0A000), missing a
    grant, or (since #892) with nothing usable to traverse. Drives the ladder descent;
    the message is a stable, operator-legible reason surfaced in ``skipped_tiers``.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


# Appended to a TRANSIENT skip's operator-facing reason.
_TRANSIENT_SKIP_SUFFIX = " (transient — retried next refresh)"


def _skip_reason(exc: _FeatureUnsupportedError) -> str:
    """The operator-facing skip reason for a descended tier, tagged when transient."""
    return f"{exc}{_TRANSIENT_SKIP_SUFFIX}" if exc.transient else str(exc)


def _sqlstate(exc: BaseException) -> str | None:
    """Snowflake's SQLSTATE off a connector error, tolerating the SQLAlchemy wrapper."""
    for obj in (exc, getattr(exc, "orig", None)):
        code = getattr(obj, "sqlstate", None)
        if isinstance(code, str):
            return code
    return None


_UNSUPPORTED_EDITION_MSG = "unsupported on this edition"


def _not_authorized_msg(grant_label: str) -> str:
    """The not-authorized wording naming the ACCOUNT_USAGE view a role needs — a shared
    template rather than a set of hand-duplicated per-tier constants (#1307).
    """
    return f"not authorized (role lacks the ACCOUNT_USAGE / {grant_label} grant)"


# The default tier name — GET_LINEAGE is the ladder's top tier, so a caller that
# classifies without a ``not_authorized_label`` (its own preflight probe) gets this.
_NOT_AUTHORIZED_MSG = _not_authorized_msg("GET_LINEAGE")


def _feature_unsupported_reason(
    exc: BaseException, *, not_authorized_label: str | None = None
) -> str | None:
    """The stable, operator-legible reason if ``exc`` is a CONFIRMED capability or
    authorization denial — Snowflake's edition-gate 0A000 (matched by SQLSTATE, the
    reliable signal, OR the documented message text belt-and-braces) or the
    does-not-exist/not-authorized blur — else ``None``.

    ``not_authorized_label`` names the ACCOUNT_USAGE view/grant (e.g.
    ``"OBJECT_DEPENDENCIES"``) a not-authorized denial should be worded against instead
    of the GET_LINEAGE default — passed in by the caller at classification time, so a
    tier-specific denial is never detected by `==`-matching this function's own return
    value against the generic wording (#1264/#1307: that comparison silently stops
    substituting the moment the generic wording is ever enriched).
    """
    if _sqlstate(exc) == _FEATURE_UNSUPPORTED_SQLSTATE or "Unsupported feature" in str(exc):
        # The edition gate → a stable, operator-legible reason (NOT the raw connector
        # text, which can be noisy). The deferred-traversal path raises its own message.
        return _UNSUPPORTED_EDITION_MSG
    # Snowflake deliberately blurs missing-object and missing-grant into one message
    # (002003 "does not exist or not authorized"), so that text IS the structured signal.
    if "does not exist or not authorized" in str(exc):
        return (
            _not_authorized_msg(not_authorized_label)
            if not_authorized_label
            else _NOT_AUTHORIZED_MSG
        )
    return None


def _reraise_if_feature_unsupported(
    exc: BaseException, *, not_authorized_label: str | None = None
) -> None:
    """Raise :class:`_FeatureUnsupportedError` if ``exc`` is a confirmed capability or
    authorization denial (:func:`_feature_unsupported_reason`), so the ladder descends.
    """
    reason = _feature_unsupported_reason(exc, not_authorized_label=not_authorized_label)
    if reason is not None:
        raise _FeatureUnsupportedError(reason) from exc


def _reraise_confirmed_or_transient(
    exc: BaseException, label: str, *, not_authorized_label: str | None = None
) -> NoReturn:
    """Classify ``exc`` and raise accordingly — never return (#1109/#1228 — review
    finding on #1263: the same three-line pattern had drifted into three call
    sites with no shared definition).
    """
    _reraise_if_feature_unsupported(exc, not_authorized_label=not_authorized_label)
    raise _FeatureUnsupportedError(f"{label} ({type(exc).__name__})", transient=True) from exc
