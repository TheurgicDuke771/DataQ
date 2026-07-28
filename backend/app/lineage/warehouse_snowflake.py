"""Snowflake warehouse-native lineage provider (#858, ADR 0034).

The tier ladder, richest first — chosen and ordered from the 2026-07-17 live spike
(#858 comments):

1. **``SNOWFLAKE.CORE.GET_LINEAGE``** (Enterprise+) — first-class server-side lineage
   traversal, object-domain aware, no JSON parsing. **Its absence is a CLEAN, catchable
   ``0A000 Unsupported feature 'Data Lineage'``** — the best preflight signal, so it is
   tried first and its failure descends the ladder rather than erroring the pull. Since
   #892 it is a real per-seed traversal over the ADR 0040 enumeration seam, built and
   tested against a live prod-Enterprise capture (2026-07-28).
2. **``ACCOUNT_USAGE.ACCESS_HISTORY``** (Enterprise+) — the DML event log: query-derived
   table AND column lineage (CTAS / INSERT / MERGE). **On a Standard account the view is
   present but SILENTLY EMPTY** (live-verified: 45,630 ``QUERY_HISTORY`` rows in 90d vs
   ``COUNT(*)=0`` here). ~2-3h latency, surfaced as ``freshness_lag``.
3. **``ACCOUNT_USAGE.OBJECT_DEPENDENCIES``** (all editions) — the current-state
   VIEW-dependency graph, captured working on the demo account (real
   RETAIL→STG→ANALYTICS chain, UPPER identity byte-identical to ``asset_identity``).

**2 and 3 are COMPLEMENTARY, not alternatives (#908/#911):** a view never appears as a
DML write, and a table→table INSERT leaves no dependency row — so the pull always reads
the floor and UNIONS the scoped DML edges in. The reported ``tier`` names the richest
source that contributed; emptiness on the DML side degrades the union to the floor,
never to a confident empty.

**Tier 1 is unioned with the floor too, the same way (#1110):** GET_LINEAGE's traversal
is bounded (500 seeds, distance 2) while OBJECT_DEPENDENCIES is not, so a database over
the seed cap has real view dependencies GET_LINEAGE never walked to. A successful
GET_LINEAGE result no longer wins the floor exclusively — it is unioned with
``_from_object_dependencies`` (GET_LINEAGE edges win per pair), tier still reports as
``SNOWFLAKE_GET_LINEAGE``. Combined with checking the STITCHED result (not the
pre-stitch raw rows) for emptiness, an empty-but-successful traversal degrades to the
floor's answer under the top tier's name rather than a confident, prunable ``()``.

Identities are built with :func:`asset_identity.format_snowflake_name` +
:func:`normalize_snowflake_account`, the SAME functions the suite-target resolver and
the dbt canonicalizer use, so a pulled edge endpoint joins an existing `assets` row
byte-for-byte with no fold (`lineage.warehouse` docstring).

Every ``FUNCTION``-domain endpoint is dropped: a dependency on a UDF is not table
lineage and has no asset identity.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

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

# The 0A000 SQLSTATE Snowflake returns when a feature (Data Lineage / ACCESS_HISTORY on
# a lower edition) is not licensed — a clean, catchable preflight signal (the spike's
# key finding vs ACCESS_HISTORY's silent-empty).
_FEATURE_UNSUPPORTED_SQLSTATE = "0A000"

# Bounded ACCESS_HISTORY lookback (#908): the DML log is read this many days back,
# bound as a query param. An edge whose last producing query ages past the window is
# pruned by the snapshot regime — deliberate freshness semantics for DML evidence
# (the view-dependency half of the union is current-state and never expires).
_ACCESS_HISTORY_LOOKBACK_DAYS = 90

# The shared per-edge column-pair cap (#901/#908) — see `warehouse`.
_MAX_COLUMN_PAIRS_PER_EDGE = MAX_COLUMN_PAIRS_PER_EDGE

# ACCESS_HISTORY objectDomain values that are table-like (per-kind, title case —
# distinct from OBJECT_DEPENDENCIES' UPPER domain vocabulary in `_TABLE_DOMAINS`).
# A read FROM a view / external table into a table is real table lineage (#911).
# The SQL IN-list in `_from_access_history` mirrors this set verbatim (pinned by a
# test); it stays literal there because SQL text is never interpolated.
_ACCESS_HISTORY_TABLE_DOMAINS = frozenset(
    {"Table", "View", "Materialized view", "Dynamic table", "External table"}
)

# `SNOWFLAKE.CORE.GET_LINEAGE` object domains that are table-like — a THIRD spelling of
# the same vocabulary (UPPER with UNDERSCORES, live-captured 2026-07-28: TABLE / VIEW /
# DYNAMIC_TABLE / STAGE). Reusing `_TABLE_DOMAINS` (UPPER with spaces) would silently
# drop every dynamic table, and the captured payload is dominated by them.
_GET_LINEAGE_TABLE_DOMAINS = frozenset(
    {"TABLE", "VIEW", "DYNAMIC_TABLE", "MATERIALIZED_VIEW", "EXTERNAL_TABLE"}
)

# GET_LINEAGE redacts objects the calling role cannot see: the name parts come back as
# `***` with `*_status = 'MASKED'` (live-captured — a masked STAGE upstream of
# ORDERS_HEADER). A `***` must NEVER become an asset, so the status and the token are
# BOTH checked: either alone is a single point of failure for a fabricated identity.
_MASKED_STATUS = "MASKED"
_REDACTED_NAME_PART = "***"

# How many chained ephemeral hops the #912 stitch will collapse through
# (A → TEMP1 → … → TEMPn → B). Snowpark materializes one or two scratch objects per
# stage; a longer chain is far more likely to be a pathological/looping payload than a
# real pipeline, and an unbounded walk over a hostile graph is a worker hazard. Chains
# past this are DROPPED AND COUNTED (`ephemeral_chains_dropped`), never silently.
_EPHEMERAL_STITCH_MAX_DEPTH = 5


def _is_masked(status: Any, *parts: Any) -> bool:
    """True when GET_LINEAGE redacted this endpoint (the role cannot see the object).

    BOTH signals are checked — ``*_status = 'MASKED'`` and the ``***`` token in any
    name part — because either alone is a single point of failure for a fabricated
    ``***.***.***`` asset, and a redacted object is precisely the case where we have
    no identity to fall back on.
    """
    return status == _MASKED_STATUS or any(part == _REDACTED_NAME_PART for part in parts)


def _is_ephemeral(qualified_name: str) -> bool:
    """Snowpark session-scratch objects (``SNOWPARK_TEMP_TABLE_…``, stages) — real rows
    in ACCESS_HISTORY / GET_LINEAGE, gone before anyone could browse the asset (#908).

    They are never emitted as edge endpoints; a pipeline that materializes THROUGH one
    is re-attached by the #912 stitch instead of being dropped.
    """
    last = qualified_name.rsplit(".", 1)[-1]
    return last.startswith("SNOWPARK_TEMP_")


class _EdgeSet:
    """An insertion-ordered ``(upstream, downstream) → identities + column pairs`` bag.

    One place owns the merge rule (a pair seen on a later row joins the same edge) and
    the shared per-edge cap, so the two tiers that build column-grain edges cannot drift
    apart on either — they did once already (#911: the SF port shipped uncapped).
    """

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
    """Compose two hops' column pairs over the bridging (scratch) column.

    ``(a_col, temp_col)`` ∘ ``(temp_col, b_col)`` → ``(a_col, b_col)``. If EITHER hop is
    table-grain (no pairs), the composition is table-grain too — inventing a pair from a
    hop that never reported one would fabricate column lineage, which is exactly what the
    real captured payload (every ``directSources`` empty) exists to forbid.

    Capped at ``_MAX_COLUMN_PAIRS_PER_EDGE`` DURING composition (review finding, #1110):
    a wide table chained through up to ``_EPHEMERAL_STITCH_MAX_DEPTH`` scratch hops can
    build a first-hop x second-hop cross product far larger than the cap before
    `_EdgeSet.add` ever gets a chance to trim it — the cap belongs at the point the set
    grows, not only at the end.
    """
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
    """Collapse ``A → TEMP(→TEMP)* → B`` chains into ``A → B`` (#912).

    ``_is_ephemeral`` used to drop such rows EDGE-WISE, so a Snowpark pipeline that
    materializes through session scratch lost its real dependency entirely: the
    downstream rendered with zero upstreams, indistinguishable from genuinely
    unlineaged. This ports `dbt_manifest._ephemeral_ancestors`' memoized,
    seed-before-recurse resolution (mirrored downstream instead of upstream) and is
    shared by BOTH Snowflake tiers that can observe scratch — ACCESS_HISTORY and
    GET_LINEAGE — because a per-tier copy is how the two column-pair caps drifted.

    Guarantees: **no ephemeral identity reaches the returned edges**; a chain that
    dead-ends in scratch or runs past ``_EPHEMERAL_STITCH_MAX_DEPTH`` is dropped AND
    counted; the counters are logged once per pull, because the pre-#912 drop was
    silent and diagnosing it took manual archaeology against prod (#912 comment).
    """

    def __init__(self, raw: _EdgeSet) -> None:
        self._rows = raw.rows()
        # downstream adjacency FROM each ephemeral node — the only walk direction the
        # stitch needs (a physical row's own upstream is already an endpoint).
        self._out: dict[str, list[tuple[AssetIdentity, frozenset[tuple[str, str]]]]] = {}
        for up, down, pairs in self._rows:
            if _is_ephemeral(up.name):
                self._out.setdefault(up.name, []).append((down, pairs))
        self._memo: dict[str, list[tuple[AssetIdentity, frozenset[tuple[str, str]], int]]] = {}
        # Whether resolving `node` encountered (directly or transitively through a
        # child) at least one over-depth cut that already accounted for `node` coming
        # back empty — see `stitched_edges` (#1110 review: without this, an over-depth
        # chain was counted twice, once here and once again there).
        self._had_depth_drop: dict[str, bool] = {}
        self.rows_seen = 0
        self.stitched = 0
        self.dropped = 0

    def _resolve(self, node: str) -> list[tuple[AssetIdentity, frozenset[tuple[str, str]], int]]:
        """The PHYSICAL nodes reachable through ephemeral ``node``, with the composed
        column pairs and the ephemeral-hop count of each path (``node`` itself = 1).

        Cycle-safe exactly as the dbt port is: ``memo[node]`` is seeded ``[]`` *before*
        recursing, so a back-edge into an already-walked scratch object resolves to
        nothing instead of recursing forever. Hop counts are measured FROM ``node``, so
        they are node-intrinsic and the memo stays valid however deep the caller is.
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
                # An interior hop — reached (and composed) from whatever physical row
                # feeds it. A chain with NO physical origin contributes nothing but is
                # visible in `ephemeral_rows_seen`.
                continue
            if not down_ephemeral:
                stitched.add(up, down, pairs)
                continue
            resolved = self._resolve(down.name)
            if not resolved:
                # Only count here when `_resolve` did NOT already count this — a chain
                # cut for depth is counted once, at the point `_resolve` observed the
                # cut; a chain that dead-ends in scratch WITHOUT ever hitting the depth
                # cap (a genuine dead end, or a cycle exhausting the memo) has never
                # been counted yet and belongs here (#1110 review: the two sites used
                # to double-count the same over-depth chain).
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
        # The reason is carried from the exception, NOT hard-coded: the tier can also
        # skip for reasons that have nothing to do with edition (no seeds enumerable,
        # a traversal that observed nothing), and an Enterprise operator must never see
        # a false "unsupported on this edition" for one of those.
        #
        # A SUCCESSFUL traversal does NOT return early any more (#1110 review, one tier
        # up from #911's floor+ACCESS_HISTORY union): GET_LINEAGE is bounded (500 seeds,
        # distance 2) while OBJECT_DEPENDENCIES is not, so on a >500-table database a
        # view dependency past the seed cap would have been silently pruned by an
        # exclusive win. GET_LINEAGE edges still win per-pair (it is the richer source),
        # but the floor is always read underneath it and unioned in.
        try:
            top_edges = self._from_get_lineage(conn, namespace, connection_config=connection_config)
        except _FeatureUnsupportedError as exc:
            skipped.append(f"get_lineage: {exc}")
            top_edges = None

        if top_edges is not None:
            try:
                floor_for_top = self._from_object_dependencies(conn, namespace, database)
            except Exception as exc:
                raise WarehouseLineageUnavailableError(
                    "snowflake lineage unavailable: could not read OBJECT_DEPENDENCIES "
                    f"({type(exc).__name__})"
                ) from exc
            merged_top: dict[tuple[str, str], LineageEdgePair] = {
                (e.upstream.name, e.downstream.name): e for e in floor_for_top
            }
            merged_top.update({(e.upstream.name, e.downstream.name): e for e in top_edges})
            return WarehouseLineageResult(
                edges=tuple(merged_top.values()),
                tier=LineageTier.SNOWFLAKE_GET_LINEAGE,
                skipped_tiers=(),
            )

        # The two remaining sources are COMPLEMENTARY truths, not alternatives (#911
        # review — the exclusive ladder was the deep defect): OBJECT_DEPENDENCIES is
        # the current-state VIEW-dependency graph (a view is never a DML write, so it
        # can never appear in ACCESS_HISTORY's objects_modified), and ACCESS_HISTORY
        # is the DML event log (a table→table INSERT leaves no dependency-view row).
        # Reading only the "winner" erased whichever half the other tier held — and
        # since this source is snapshot-pruned, a tier-2 win would have PRUNED the
        # entire dbt view graph on the next refresh. So: read the floor ALWAYS, and
        # union the DML edges (with their column pairs) in when the account offers
        # them. An empty or unreadable ACCESS_HISTORY degrades the union to the
        # floor — never to a confident empty.
        try:
            floor = self._from_object_dependencies(conn, namespace, database)
        except Exception as exc:  # the floor failing means we learned nothing
            raise WarehouseLineageUnavailableError(
                "snowflake lineage unavailable: could not read OBJECT_DEPENDENCIES "
                f"({type(exc).__name__})"
            ) from exc

        dml: tuple[LineageEdgePair, ...] = ()
        try:
            dml = self._from_access_history(conn, namespace, database)
        except _FeatureUnsupportedError as exc:
            # Carry the REAL reason (edition gate vs missing grant, #902) — the same
            # honesty rule the tier-1 skip already follows.
            skipped.append(f"access_history: {exc}")
        if not dml and not any(s.startswith("access_history") for s in skipped):
            # Scoped-empty is a normal state (an all-COPY database, or one idle in the
            # window), NOT evidence of edition gating — the old "edition-gated or no
            # write history" label mislabeled healthy Enterprise accounts.
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
            # The per-tier skip reasons are constructed, stable strings (edition gate /
            # missing grant / deferred traversal / scoped-empty — never raw connector
            # text), so they can be surfaced verbatim; a blanket "need Enterprise"
            # would mislabel a grant-shaped skip (#902).
            degraded_reason=(
                (
                    ("column detail limited — " if dml else "view-level lineage only — ")
                    + "; ".join(skipped)
                )
                if skipped
                else None
            ),
            skipped_tiers=tuple(skipped),
        )

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

        Scope mirrors the lineage pull (#908/#911): ONE database (bound param on
        ``table_catalog`` — the session already runs in the connection's database,
        the predicate makes the boundary explicit), the same object vocabulary the
        lineage domains accept (temporaries excluded by TABLE_TYPE allowlist;
        dynamic tables arrive as ``BASE TABLE``), ``INFORMATION_SCHEMA`` itself and
        Snowpark session scratch (``SNOWPARK_TEMP_*``) excluded. Identities are
        built from the catalog's own strings — no fold (the #823-safe path).
        """
        namespace = self._namespace(connection_config)
        database = self._database(connection_config)
        # Every exclusion lives in the WHERE clause, BEFORE the LIMIT (review
        # finding on this PR): a Python-side filter after a SQL LIMIT lets
        # excluded rows consume the cap+1 budget, so the caller's overflow
        # detection can never fire and the sync silently under-covers — the
        # exact silent-cap the ADR forbids. ESCAPE makes the underscores in
        # the Snowpark prefix literal (LIKE's bare `_` matches any character).
        sql = (
            "SELECT table_schema, table_name FROM INFORMATION_SCHEMA.TABLES"
            " WHERE table_catalog = :db"
            " AND table_schema IS NOT NULL AND table_name IS NOT NULL"
            " AND table_schema != 'INFORMATION_SCHEMA'"
            # The ephemera pattern is a BOUND PARAM, never a literal: the
            # snowflake connector's pyformat paramstyle reads a raw % in a
            # text() statement as a format placeholder and raises
            # ProgrammingError (#1111 — found live; the fake-conn tests cannot
            # execute real SQL, the #823 driver-boundary class again).
            " AND table_name NOT LIKE :ephemeral ESCAPE '!'"
            " AND table_type IN ('BASE TABLE', 'VIEW', 'MATERIALIZED VIEW', 'EXTERNAL TABLE')"
            " ORDER BY table_schema, table_name"
        )
        # `!` as the LIKE escape char: backslash is an escape in BOTH the
        # connector's param pipeline AND Snowflake's string parser, and the
        # review of #1112 proved the literal `ESCAPE '\'` never closed its
        # quote server-side ( \' is an escaped quote to Snowflake) — `!` has
        # no escaping semantics in either layer, so what you read is what runs.
        params: dict[str, object] = {"db": database, "ephemeral": "SNOWPARK!_TEMP!_%"}
        if limit is not None:
            sql += " LIMIT :lim"
            params["lim"] = int(limit)
        rows = conn.execute(text(sql), params).all()  # type: ignore[attr-defined]
        # The SQL predicates above are the budget-correct filter; this residual
        # guard only catches driver weirdness the WHERE clause could not (the
        # real account-wide capture contains NULL-catalog rows, so nulls are a
        # fixture-proven possibility) — post-LIMIT it can no longer eat budget.
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
        database = database.strip()
        # The same quote-strip-else-UPPER rule the identity formatter applies (#911
        # review): a quoted database ("DataQ_Db") is stored by ACCOUNT_USAGE in its
        # exact inner case — blanket .upper() would exact-match nothing and turn a
        # config nuance into a silently empty (and prunable!) graph.
        if len(database) >= 2 and database.startswith('"') and database.endswith('"'):
            return database[1:-1]
        return database.upper()

    # ── tier 3: OBJECT_DEPENDENCIES (live-verified) ─────────────────────────────
    def _from_object_dependencies(
        self, conn: Any, namespace: str, database: str
    ) -> tuple[LineageEdgePair, ...]:
        # At least ONE endpoint bound to the connection's database (#908) — OR, not
        # AND: a cross-database dependency touching this database is real lineage
        # (dropping it would assert "nothing feeds this view", the #845-class
        # omission), while SNOWFLAKE.TRUST_CENTER.* and other system deps have
        # neither endpoint here and stay excluded. Exact-match bound params.
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

        Scope + hygiene, each proven necessary by the first live pull:

        * **Both endpoints in a table-like ``objectDomain``, in SQL** — the real
          history is dominated by ``Stage`` → Table (COPY) and ``Table function`` →
          Table (GENERATOR) rows, which are not table lineage and were what
          materialized stages as assets. The domain set mirrors ``_TABLE_DOMAINS``
          (a read FROM a view or an external table is table lineage; #911 review).
        * **At least ONE endpoint in the connection's database** (``SPLIT_PART``
          exact match on a bound param). ``OR``, not ``AND``: a cross-database edge
          touching this database is real lineage, and dropping it would assert
          "nothing feeds this table" — the omission the #845 amendment forbids. The
          junk this scope exists to kill (system views, other tenants' noise) has
          NEITHER endpoint here.
        * **Bounded lookback** (``_ACCESS_HISTORY_LOOKBACK_DAYS``, bound) — never the
          whole 365d retention; the dropped-schema ghosts (PERF) live in the old
          rows. Consequence, documented: a DML edge whose last producing query ages
          past the window is pruned — "no DML evidence in 90d" is this source's
          freshness semantics; the view half of the union never expires.
        * **Snowpark ephemera STITCHED, not dropped** (#912): ``SNOWPARK_TEMP_*``
          session scratch is collected like any other row and then collapsed
          transitively by :class:`_EphemeralStitch`, so ``A → TEMP → B`` yields the
          real ``A → B`` and no scratch identity ever reaches an edge. The old
          edge-level drop lost the dependency entirely — the downstream rendered
          with zero upstreams, indistinguishable from genuinely unlineaged.

        Returns the (possibly empty) scoped DML edges — the caller unions them with
        the OBJECT_DEPENDENCIES floor, so empty here never asserts an empty graph.
        Raises `_FeatureUnsupportedError` (via the reraise helper) when the view is
        edition-gated or unauthorized."""
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
            _reraise_if_feature_unsupported(exc)
            raise
        raw = _EdgeSet()
        # The bo x om cross-join repeats each statement's columns blob once per base
        # object, and repeated statements repeat it again — parse each distinct blob
        # once (#911 review: the un-memoized parse was O(rows x columns x sources)).
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

        Each written column carries ``directSources`` — the exact source columns the
        engine derived it from (Enterprise). A statement can read several tables, so
        the caller attaches each bucket to the edge whose upstream matches its key —
        sources in other tables belong to those edges' own rows. A malformed entry is
        skipped, never fatal (the table grain must survive a JSON surprise)."""
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
        non-3-part name (a stage, a column-qualified ref) — not a table."""
        if not qualified:
            return None
        parts = qualified.split(".")
        if len(parts) != 3:
            return None
        return self._identity(namespace, parts[0], parts[1], parts[2])

    # ── tier 1: GET_LINEAGE (Enterprise; clean 0A000 when absent) ───────────────
    def _from_get_lineage(
        self, conn: Any, namespace: str, *, connection_config: dict[str, object]
    ) -> tuple[LineageEdgePair, ...]:
        """Per-seed ``SNOWFLAKE.CORE.GET_LINEAGE`` traversal (#892, live-captured
        2026-07-28 against prod Enterprise).

        **Seeds ride the ADR 0040 enumeration seam** (`enumerate_tables`) — the same
        catalog read the #919 inventory sync uses, so discovery can never fork into two
        answers about "which tables does this connection have". Bounded by
        ``WAREHOUSE_LINEAGE_MAX_SEEDS`` with a loud `get_lineage_seeds_truncated`; a
        silently-capped traversal reads as a complete graph.

        **Each row is a DIRECT ``source → target`` edge** — the capture settles this:
        a ``distance``-2 call returns the whole local graph (its rows carry
        ``distance`` 1 AND 2), so ``distance`` is hops-from-seed, NOT a claim that the
        seed depends directly on the row's endpoint. Treating a distance-2 row as a
        seed→X edge would fabricate dependencies. Both directions are walked per seed
        so an edge is observed from whichever end the role can see.

        Hygiene, each proven by the captured payload:

        * **MASKED rows skipped** — a role that cannot see an object gets ``***`` name
          parts with ``*_status = 'MASKED'``; a ``***`` must never become an asset.
        * **Domain allowlist** (``_GET_LINEAGE_TABLE_DOMAINS``) — the capture's other
          domain is ``STAGE``, which is not table lineage (the junk the first live
          ACCESS_HISTORY pull materialized as browsable assets).
        * **Column grain** from ``source_column_name``/``target_column_name`` when the
          row carries both, capped per edge like every other tier.
        * **#912 stitch applied here too** — GET_LINEAGE can traverse straight through
          Snowpark scratch, so the same collapse runs on this tier's rows.

        Ladder contract: the FIRST call's edition gate / missing grant descends via
        :func:`_reraise_if_feature_unsupported` exactly as the old probe did. After one
        call has succeeded the feature demonstrably exists, so a later seed's failure
        is a per-object problem — skipped and counted, never an aborted pull. A
        traversal that yields nothing (checked on the STITCHED result, not the
        pre-stitch raw rows — #1110 review: `raw` can be non-empty purely from
        ephemeral rows the stitch then collapses to `()`) descends too: under the
        snapshot-prune regime a confident empty at the top tier would wipe the floor's
        real view graph (#911). A traversal that yields SOMETHING is no longer an
        exclusive win either — `fetch_edges` unions it with the OBJECT_DEPENDENCIES
        floor (#1110), since the 500-seed/distance-2 bound means a >500-table database
        has real view dependencies this traversal never reached.
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
            _reraise_if_feature_unsupported(exc)
            # No seed list → no traversal. Descend with the classified reason rather
            # than abort: the floor can still answer, and losing the whole graph
            # because a catalog read failed is the #828 failure this ladder exists for.
            raise _FeatureUnsupportedError(
                f"seed enumeration failed ({type(exc).__name__})"
            ) from exc
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
                        # Edition gate / missing grant on the very first call → the
                        # ladder descends exactly as the old preflight probe did.
                        _reraise_if_feature_unsupported(exc)
                        raise
                    seed_failures += 1
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
            )
        edges = _stitch_ephemera(raw, path="get_lineage")
        if not edges:
            # Nothing observed, checked on the POST-stitch physical edge set (#1110
            # review): `raw` can be non-empty purely from ephemeral (SNOWPARK_TEMP_*)
            # rows the stitch then collapses to nothing, and checking `raw` would have
            # returned this confident-but-wrong `()` as a top-tier success instead of
            # descending — which prunes the floor's real graph outright under this
            # snapshot-regime provider. Descend to the union rather than claim a
            # confident empty from the pruning tier (#911: a confident empty needs the
            # current-state authority to have answered it).
            raise _FeatureUnsupportedError(
                f"no lineage rows for {len(seeds)} seed table(s)"
                + (f" ({seed_failures} failed call(s))" if seed_failures else "")
            )
        return edges

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
    the message is a stable, operator-legible reason surfaced in ``skipped_tiers``."""


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
