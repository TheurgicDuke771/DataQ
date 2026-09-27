"""Column-grain lineage over the `lineage_edges` cache (#1710, ADR 0034 amendment 2026-09-27).

Column pairs are a REFINEMENT carried on a table edge (`lineage_edges.columns`, #901) — there
is no separate column graph to maintain, so the provenance/prune rules of the table edge apply
to its pairs for free. This module reads that refinement two ways:

* :func:`edge_columns` — per table edge, its pairs plus a :class:`ColumnCoverage` saying WHY an
  edge has none (the #828 rule: "no column lineage exists" must never be conflated with "we
  could not / did not look").
* :func:`trace_column` — follow one column up to where it originates and/or down to every
  column derived from it, reporting each table edge the walk could NOT follow at column grain.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, func, select, tuple_
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Connection, LineageEdge
from backend.app.lineage.identity import canonical_identity
from backend.app.lineage.warehouse import WAREHOUSE_LINEAGE_CONNECTION_TYPES, ColumnGrain

log = get_logger(__name__)

#: Default and hard ceilings for one trace — a column fans out far wider than a table does.
DEFAULT_TRACE_DEPTH = 10
MAX_TRACE_DEPTH = 25
MAX_TRACE_NODES = 500


class ColumnCoverage(StrEnum):
    """Why a table edge does or does not carry column pairs — strongest first."""

    RECORDED = "recorded"  # pairs observed on this edge
    # A column-capable source's last pull read column lineage fine and recorded none for this
    # edge. Still not proof the columns are unrelated (a Snowflake view dependency is never a DML
    # write, so ACCESS_HISTORY never observes it; a write outside the retention window is gone).
    NONE_RECORDED = "none_recorded"
    UNAVAILABLE = "unavailable"  # the source's column-lineage read failed on its last pull
    UNKNOWN = "unknown"  # the source has not refreshed since column-grain state was recorded
    NOT_CAPTURED = "not_captured"  # this source never carries column lineage (dbt, catalog pull)


_PRECEDENCE: tuple[ColumnCoverage, ...] = tuple(ColumnCoverage)

_GRAIN_TO_COVERAGE: dict[str, ColumnCoverage] = {
    ColumnGrain.CAPTURED.value: ColumnCoverage.NONE_RECORDED,
    ColumnGrain.UNAVAILABLE.value: ColumnCoverage.UNAVAILABLE,
    ColumnGrain.NOT_SUPPORTED.value: ColumnCoverage.NOT_CAPTURED,
}


def row_coverage(*, source: str, has_pairs: bool, column_grain: str | None) -> ColumnCoverage:
    """The coverage of ONE provenance row (one source's view of a table edge)."""
    if has_pairs:
        return ColumnCoverage.RECORDED
    if source not in WAREHOUSE_LINEAGE_CONNECTION_TYPES:
        # dbt manifest / catalog (Marquez) pulls parse table identity only.
        return ColumnCoverage.NOT_CAPTURED
    if column_grain is None:
        return ColumnCoverage.UNKNOWN
    return _GRAIN_TO_COVERAGE.get(column_grain, ColumnCoverage.UNKNOWN)


def combine(coverages: Iterable[ColumnCoverage]) -> ColumnCoverage:
    """One table edge observed by several sources: the strongest statement any of them makes."""
    present = set(coverages)
    for coverage in _PRECEDENCE:
        if coverage in present:
            return coverage
    return ColumnCoverage.NOT_CAPTURED


@dataclass(frozen=True)
class EdgeColumns:
    """A table edge's column refinement, unioned across the sources that observed it."""

    pairs: tuple[tuple[str, str], ...]
    coverage: ColumnCoverage


def _parse_pairs(cols: Any, *, up: uuid.UUID, down: uuid.UUID) -> list[tuple[str, str]]:
    """`columns` is app-written JSONB, but a malformed value must degrade loudly, never 500."""
    if cols is None:
        return []
    if not isinstance(cols, (list, tuple)):
        log.warning(
            "lineage_edge_columns_malformed",
            upstream_asset_id=str(up),
            downstream_asset_id=str(down),
            value_type=type(cols).__name__,
        )
        return []
    valid = [
        (str(entry[0]), str(entry[1]))
        for entry in cols
        if isinstance(entry, (list, tuple)) and len(entry) == 2
    ]
    if len(valid) != len(cols):
        log.warning(
            "lineage_edge_column_entries_malformed",
            upstream_asset_id=str(up),
            downstream_asset_id=str(down),
            dropped=len(cols) - len(valid),
        )
    return valid


def _load(
    session: Session, where: ColumnElement[bool]
) -> dict[tuple[uuid.UUID, uuid.UUID], EdgeColumns]:
    rows = session.execute(
        select(
            LineageEdge.upstream_asset_id,
            LineageEdge.downstream_asset_id,
            LineageEdge.source,
            # JSON 'null' (#907) reads as no pairs, same as SQL NULL.
            func.nullif(func.jsonb_typeof(LineageEdge.columns), "null").label("kind"),
            LineageEdge.columns,
            Connection.lineage_column_grain,
        )
        .outerjoin(Connection, Connection.id == LineageEdge.connection_id)
        .where(where)
    ).all()
    pairs: dict[tuple[uuid.UUID, uuid.UUID], set[tuple[str, str]]] = {}
    coverages: dict[tuple[uuid.UUID, uuid.UUID], list[ColumnCoverage]] = {}
    for up, down, source, kind, cols, grain in rows:
        parsed = _parse_pairs(cols if kind is not None else None, up=up, down=down)
        key = (up, down)
        pairs.setdefault(key, set()).update(parsed)
        coverages.setdefault(key, []).append(
            row_coverage(source=str(source), has_pairs=bool(parsed), column_grain=grain)
        )
    return {
        key: EdgeColumns(pairs=tuple(sorted(pairs[key])), coverage=combine(coverages[key]))
        for key in coverages
    }


def edge_columns(
    session: Session, edges: Sequence[tuple[uuid.UUID, uuid.UUID]]
) -> dict[tuple[uuid.UUID, uuid.UUID], EdgeColumns]:
    """Column pairs + coverage for each ``(upstream, downstream)`` table edge given."""
    if not edges:
        return {}
    return _load(
        session,
        tuple_(LineageEdge.upstream_asset_id, LineageEdge.downstream_asset_id).in_(list(edges)),
    )


class TraceDirection(StrEnum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    BOTH = "both"


class TraceStatus(StrEnum):
    """What one direction of a trace found, from the traced column's own vantage."""

    TRACED = "traced"  # ≥1 column hop found (see `complete` for whether it is the whole story)
    NO_TABLE_LINEAGE = "no_table_lineage"  # the asset has no table edges this way at all
    # Every adjacent table edge carries column grain from a capable source, and none maps this
    # column — as far as the recorded lineage goes it neither derives from nor feeds another.
    NONE_RECORDED = "none_recorded"
    INCOMPLETE = "incomplete"  # no hop found, and some adjacent table edge lacks column grain


@dataclass(frozen=True)
class ColumnNode:
    asset_id: uuid.UUID
    column: str
    depth: int


@dataclass(frozen=True)
class ColumnHop:
    upstream_asset_id: uuid.UUID
    upstream_column: str
    downstream_asset_id: uuid.UUID
    downstream_column: str


@dataclass(frozen=True)
class CoverageGap:
    """A table edge touching a traced column's asset that the walk could not follow at column
    grain — the column MAY flow across it; nothing recorded says either way.
    """

    upstream_asset_id: uuid.UUID
    downstream_asset_id: uuid.UUID
    coverage: ColumnCoverage


@dataclass(frozen=True)
class ColumnOrigin:
    """An upstream-most column the walk reached. ``confirmed`` is False when a gap or the depth
    cap means something further upstream may still feed it. Empty when no upstream hop was found —
    read the upstream status for why.
    """

    asset_id: uuid.UUID
    column: str
    depth: int
    confirmed: bool


@dataclass
class _Walk:
    nodes: list[ColumnNode] = field(default_factory=list)
    hops: list[ColumnHop] = field(default_factory=list)
    gaps: list[CoverageGap] = field(default_factory=list)
    truncated: bool = False
    status: TraceStatus = TraceStatus.NO_TABLE_LINEAGE
    # Assets whose own table edges (this direction) contain a gap — the origin-confirmation input.
    gapped_assets: set[uuid.UUID] = field(default_factory=set)
    # (asset, folded column) keys that received at least one hop from further along the walk.
    fed: set[tuple[uuid.UUID, str]] = field(default_factory=set)
    frontier_at_cap: set[tuple[uuid.UUID, str]] = field(default_factory=set)


@dataclass(frozen=True)
class ColumnTrace:
    asset_id: uuid.UUID
    column: str
    upstream: list[ColumnNode]
    downstream: list[ColumnNode]
    hops: list[ColumnHop]
    gaps: list[CoverageGap]
    origins: list[ColumnOrigin]
    upstream_status: TraceStatus | None
    downstream_status: TraceStatus | None
    truncated: bool

    @property
    def complete(self) -> bool:
        """True only when no traversed edge lacked column grain and no cap cut the walk."""
        return not self.gaps and not self.truncated


def _fold(namespace: str, column: str) -> str:
    """Case-fold a column the way its engine folds an unquoted identifier (ADR 0034 §6)."""
    return canonical_identity(namespace, column.strip())[1]


class _Namespaces:
    """Asset id → namespace, loaded in bulk as the walk discovers assets."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._by_id: dict[uuid.UUID, str] = {}

    def ensure(self, ids: Iterable[uuid.UUID]) -> None:
        missing = [i for i in ids if i not in self._by_id]
        if missing:
            for aid, ns in self._session.execute(
                select(Asset.id, Asset.namespace).where(Asset.id.in_(missing))
            ):
                self._by_id[aid] = str(ns)

    def fold(self, asset_id: uuid.UUID, column: str) -> str:
        return _fold(self._by_id.get(asset_id, ""), column)


def _walk(
    session: Session,
    namespaces: _Namespaces,
    start: tuple[uuid.UUID, str],
    *,
    upstream: bool,
    max_depth: int,
) -> _Walk:
    walk = _Walk()
    near = LineageEdge.downstream_asset_id if upstream else LineageEdge.upstream_asset_id
    visited: set[tuple[uuid.UUID, str]] = {start}
    frontier: dict[tuple[uuid.UUID, str], None] = {start: None}
    depth = 0
    saw_any_edge = False
    saw_start_gap = False
    while frontier:
        if depth >= max_depth or len(walk.nodes) >= MAX_TRACE_NODES:
            walk.truncated = True
            walk.frontier_at_cap = set(frontier)
            break
        assets = {aid for aid, _ in frontier}
        edges = _load(session, near.in_(list(assets)))
        namespaces.ensure({aid for key in edges for aid in key})
        wanted: dict[uuid.UUID, set[str]] = {}
        for aid, col in frontier:
            wanted.setdefault(aid, set()).add(col)
        next_frontier: dict[tuple[uuid.UUID, str], None] = {}
        for (up, down), info in sorted(
            edges.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))
        ):
            here, there = (down, up) if upstream else (up, down)
            if here not in wanted:
                continue
            if depth == 0:
                saw_any_edge = True
            if info.coverage is not ColumnCoverage.RECORDED:
                walk.gaps.append(CoverageGap(up, down, info.coverage))
                walk.gapped_assets.add(here)
                if depth == 0:
                    saw_start_gap = True
                continue
            for up_col, down_col in info.pairs:
                here_col, there_col = (down_col, up_col) if upstream else (up_col, down_col)
                if namespaces.fold(here, here_col) not in wanted[here]:
                    continue
                walk.hops.append(ColumnHop(up, up_col, down, down_col))
                walk.fed.add((here, namespaces.fold(here, here_col)))
                key = (there, namespaces.fold(there, there_col))
                if key in visited:
                    continue
                visited.add(key)
                if len(walk.nodes) >= MAX_TRACE_NODES:
                    walk.truncated = True
                    walk.frontier_at_cap.add(key)
                    continue
                walk.nodes.append(ColumnNode(there, there_col, depth + 1))
                next_frontier[key] = None
        frontier = next_frontier
        depth += 1
    if walk.hops:
        walk.status = TraceStatus.TRACED
    elif not saw_any_edge:
        walk.status = TraceStatus.NO_TABLE_LINEAGE
    elif saw_start_gap:
        walk.status = TraceStatus.INCOMPLETE
    else:
        walk.status = TraceStatus.NONE_RECORDED
    return walk


def trace_column(
    session: Session,
    asset_id: uuid.UUID,
    column: str,
    *,
    direction: TraceDirection = TraceDirection.BOTH,
    max_depth: int = DEFAULT_TRACE_DEPTH,
) -> ColumnTrace:
    """Follow ``column`` of ``asset_id`` through the recorded column pairs.

    The column is matched with its engine's unquoted-identifier fold (Snowflake UPPER, Unity
    Catalog lower, exact elsewhere). Its existence on the asset is NOT verified — DataQ holds
    no schema for it here — so a misspelt column simply traces to nothing (``none_recorded`` /
    ``incomplete``), which the caller must present as "nothing recorded", never "unrelated".
    """
    max_depth = max(1, min(max_depth, MAX_TRACE_DEPTH))
    namespaces = _Namespaces(session)
    namespaces.ensure([asset_id])
    start = (asset_id, namespaces.fold(asset_id, column))
    up = (
        _walk(session, namespaces, start, upstream=True, max_depth=max_depth)
        if direction in (TraceDirection.UPSTREAM, TraceDirection.BOTH)
        else None
    )
    down = (
        _walk(session, namespaces, start, upstream=False, max_depth=max_depth)
        if direction in (TraceDirection.DOWNSTREAM, TraceDirection.BOTH)
        else None
    )
    origins: list[ColumnOrigin] = []
    if up is not None:
        # Only columns the walk REACHED: the traced column itself is never listed as its own origin
        # — with no hop found, that would assert a provenance (and an existence) nothing recorded.
        for node in up.nodes:
            key = (node.asset_id, namespaces.fold(node.asset_id, node.column))
            if key in up.fed:
                continue  # something further upstream feeds it — not an origin
            origins.append(
                ColumnOrigin(
                    asset_id=node.asset_id,
                    column=node.column,
                    depth=node.depth,
                    confirmed=node.asset_id not in up.gapped_assets
                    and key not in up.frontier_at_cap,
                )
            )
    return ColumnTrace(
        asset_id=asset_id,
        column=column.strip(),
        upstream=up.nodes if up else [],
        downstream=down.nodes if down else [],
        hops=[*(up.hops if up else []), *(down.hops if down else [])],
        gaps=[*(up.gaps if up else []), *(down.gaps if down else [])],
        origins=origins,
        upstream_status=up.status if up else None,
        downstream_status=down.status if down else None,
        truncated=bool((up and up.truncated) or (down and down.truncated)),
    )
