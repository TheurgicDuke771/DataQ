"""Read-only asset view — the browse/reason surface over `assets` (ADR 0034, #760)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Float, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    DQ_DIMENSIONS,
    Asset,
    Check,
    Connection,
    Result,
    Run,
    Suite,
    User,
    worst_severity,
)
from backend.app.lineage import columns as lineage_columns
from backend.app.lineage.edges import lineage_neighbourhood
from backend.app.lineage.warehouse import (
    WAREHOUSE_LINEAGE_CONNECTION_TYPES,
    snapshot_lineage_connection_types,
)
from backend.app.services import audit_service, column_tags, scoring_settings_service
from backend.app.services.rollup import (
    AGGREGATABLE_RUN_STATUSES,
    SEVERITY_STATUSES,
    evaluated_total,
    health_score,
    latest_runs_per_suite_stmt,
    status_histograms,
)
from backend.app.services.run_service import operational_result_flags, outcome_from_histogram
from backend.app.services.scoring_settings_service import Weights
from backend.app.services.suite_authz import effective_permissions

log = get_logger(__name__)


class AssetNotFoundError(DataQError):
    """Raised when an asset id names no asset. Identity is workspace-visible
    (ADR 0037), so — unlike the suite endpoints — there is no no-leak case here:
    every existing asset opens for every member.
    """

    status_code = 404
    code = "asset_not_found"


class AssetOwnerInvalidError(DataQError):
    """The `owner_user_id` on a metadata update names no existing user — checked
    up front (the share-grant FK-precheck idiom, `share_service.grant_share`) so a
    bad id is a clean 422, never a raw FK IntegrityError surfacing as 500.
    """

    status_code = 422
    code = "asset_owner_invalid"


@dataclass(frozen=True)
class RunOutcome:
    """A suite's latest run outcome — execution status + the data-quality summary."""

    run_id: uuid.UUID | None = None
    status: str | None = None
    worst_severity: str | None = None
    checks_total: int = 0
    checks_passed: int = 0
    finished_at: datetime | None = None
    created_at: datetime | None = None
    has_error: bool = False
    has_skip: bool = False
    #: The run's result-status histogram — empty unless the run's result set is complete.
    counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ComposingSuite:
    """One suite the caller can see that targets the asset, with its latest run."""

    suite_id: uuid.UUID
    name: str
    my_permission: str
    latest_run: RunOutcome


@dataclass(frozen=True)
class AssetSummary:
    """List-row aggregation for one asset — **workspace-true** (ADR 0037): every
    field is identical for every viewer, and the health axes aggregate over ALL
    composing suites regardless of the caller's grants. One verdict per asset.
    """

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    description: str | None
    owner_user_id: uuid.UUID | None
    last_seen: datetime
    suite_count: int
    # ── suite health (data quality) ──
    worst_severity: str | None
    checks_total: int
    checks_passed: int
    last_run_at: datetime | None
    #: Health score over every evaluated result of the composing suites' latest complete
    #: runs (#1556). `None` when nothing evaluated — never run, or only skip/error.
    health_score: float | None = None
    # ── connection health (reachability / execution) ── `has_failed_run`: any latest run whose
    # *execution* `failed` (wrote no results).
    has_failed_run: bool = False
    has_active_run: bool = False
    has_cancelled_run: bool = False
    has_operational_error: bool = False
    has_skip: bool = False
    #: Left out of its connection's automatic coverage (ADR 0047).
    auto_coverage_excluded: bool = False


@dataclass(frozen=True)
class LineageNode:
    """A lineage neighbour — enough to render, no run data (ADR 0034 §2)."""

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    is_monitored: bool
    depth: int = 1


@dataclass(frozen=True)
class LineageEdgeRef:
    """One edge of the neighbourhood DAG, as ``(upstream → downstream)`` asset ids."""

    source: uuid.UUID
    target: uuid.UUID
    columns: tuple[tuple[str, str], ...] | None = None
    # #1710: why `columns` is None when it is — never read "no pairs" as "no column lineage".
    column_coverage: lineage_columns.ColumnCoverage = lineage_columns.ColumnCoverage.UNKNOWN


@dataclass(frozen=True)
class LineageSourceHealth:
    """Whether the integrations that FEED lineage are actually working (#828)."""

    connection_id: uuid.UUID
    name: str
    type: str
    consecutive_failures: int
    last_error: str | None
    last_polled_at: datetime | None


@dataclass(frozen=True)
class WarehouseLineageStatus:
    """A warehouse-native lineage source (Snowflake / UC) that is DEGRADED or FAILING —
    surfaced so a view-level-only or stale graph never reads as a confident full one
    (#828, #858 slice 4).
    """

    connection_id: uuid.UUID
    name: str
    type: str
    tier: str | None
    degraded_reason: str | None
    last_error: str | None
    last_refreshed_at: datetime | None
    # #1091: the refresh loop silently STOPPED — no error, no degradation, just no refresh within
    # the staleness window.
    stale: bool = False
    # #1236: the last refresh did NOT prune stale edges, so the graph from this source can only
    # grow. Snapshot sources only — an incremental source never prunes by design.
    prune_suspended: bool = False
    # When this source last pruned. NULL means never, which is a different answer from "a long
    # time ago" and must not render as one.
    prune_suspended_since: datetime | None = None


@dataclass(frozen=True)
class DimensionScore:
    """One row of the asset DQ scorecard (#889, ADR 0038)."""

    dimension: str
    # Checks that EXIST in this dimension — the coverage number. Not a result
    # count: a check authored today but not yet run still counts as covered.
    checks_total: int
    # Of those, how many passed in the latest run. `checks_total - checks_passing`
    # therefore spans failing, skipped, errored, AND never-run checks.
    checks_passing: int
    # How many actually evaluated a severity — the score's denominator, which
    # excludes skip/error (#122). Below `checks_total` whenever checks didn't run.
    checks_evaluated: int
    score: float | None


@dataclass(frozen=True)
class Scorecard:
    """Per-dimension coverage + score for an asset, **workspace-true** (ADR 0037)."""

    covered: list[DimensionScore]
    uncovered: list[str]
    unclassified_checks: int


@dataclass(frozen=True)
class InheritedSource:
    """An upstream column whose ``sensitive`` classification a column inherits."""

    asset_id: uuid.UUID
    asset_name: str
    column: str


@dataclass(frozen=True)
class InheritedClassification:
    """A column masked as ``sensitive`` only because recorded lineage says it comes from one."""

    column: str
    sources: list[InheritedSource]


@dataclass(frozen=True)
class AssetDetail:
    """Asset detail: the workspace-true summary + the caller's per-suite breakdown
    + lineage. ``suites`` lists only suites the caller can view (ADR 0027);
    ``restricted_suite_count`` is how many more compose the asset — those still
    roll into ``summary`` (workspace-true, ADR 0037) but stay unnamed.
    """

    summary: AssetSummary
    suites: list[ComposingSuite]
    scorecard: Scorecard | None = None
    restricted_suite_count: int = 0
    upstream: list[LineageNode] = field(default_factory=list)
    downstream: list[LineageNode] = field(default_factory=list)
    lineage_edges: list[LineageEdgeRef] = field(default_factory=list)
    # Non-empty ⇒ a lineage source is broken, so the graph below may be stale or empty for a reason
    # that has nothing to do with this asset.
    failing_lineage_sources: list[LineageSourceHealth] = field(default_factory=list)
    # Warehouse-native lineage sources that are degraded (coarser tier) or failing — so the graph
    # can be qualified ("view-level only", "last refreshed 2h ago") rather than presented as
    # complete + current (#828, #858).
    warehouse_lineage_status: list[WarehouseLineageStatus] = field(default_factory=list)
    # Columns masked only through lineage (#2114). None = could not be determined (a lineage read
    # failed), never "none inherited"; truncated = the walk hit its depth cap.
    inherited_classifications: list[InheritedClassification] | None = field(default_factory=list)
    inherited_classifications_truncated: bool = False


# ── internals ────────────────────────────────────────────────────────────────


def _latest_run_per_suite(session: Session, suite_ids: list[uuid.UUID]) -> dict[uuid.UUID, Run]:
    """The most-recent run for each suite (DISTINCT ON, newest `created_at`)."""
    if not suite_ids:
        return {}
    rows = session.scalars(latest_runs_per_suite_stmt(suite_ids))
    return {run.suite_id: run for run in rows}


def _run_outcome(
    run: Run | None,
    counts: Mapping[str, int] | None,
    op_flags: tuple[bool, bool] | None = None,
) -> RunOutcome:
    """Assemble a `RunOutcome` from a suite's latest run + its status histogram
    + its operational (`error`/`skip`) flags.
    """
    if run is None:
        return RunOutcome()
    counts = counts or {}
    total, passed, worst = outcome_from_histogram(counts)
    has_error, has_skip = op_flags or (False, False)
    return RunOutcome(
        run_id=run.id,
        status=run.status,
        worst_severity=worst,
        checks_total=total,
        checks_passed=passed,
        finished_at=run.finished_at,
        created_at=run.created_at,
        has_error=has_error,
        has_skip=has_skip,
        counts=counts,
    )


def _composing_suites(
    suites: list[Suite],
    levels: dict[uuid.UUID, str | None],
    outcome_by_suite: dict[uuid.UUID, RunOutcome],
) -> list[ComposingSuite]:
    """Build the per-suite breakdown for one asset's suites (sorted by name).
    Consumes the SAME ``RunOutcome`` map the workspace-true rollup reads, so the
    listed rows and the rollup can never disagree about a run (#924 review).
    """
    composing: list[ComposingSuite] = []
    for suite in suites:
        level = levels.get(suite.id)
        if level is None:  # defensive: only reachable suites are passed in
            continue
        composing.append(
            ComposingSuite(
                suite_id=suite.id,
                name=suite.name,
                my_permission=level,
                latest_run=outcome_by_suite.get(suite.id, RunOutcome()),
            )
        )
    return composing


def _latest_outcomes(session: Session, suites: list[Suite]) -> dict[uuid.UUID, RunOutcome]:
    """One ``RunOutcome`` per suite (empty for a never-run suite) — the single
    computation both the workspace-true rollup and the per-suite breakdown read.
    Three grouped queries total (latest runs, status histograms, operational flags).
    """
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    # An asset scorecard states how the ASSET is doing, so a partial or stranded result set must not
    # contribute (#318).
    histograms = status_histograms(session, run_ids, complete_runs_only=True)
    op_flags = operational_result_flags(session, run_ids)
    by_suite: dict[uuid.UUID, RunOutcome] = {}
    for suite in suites:
        run = latest_runs.get(suite.id)
        counts = histograms.get(run.id) if run is not None else None
        flags = op_flags.get(run.id) if run is not None else None
        by_suite[suite.id] = _run_outcome(run, counts, flags)
    return by_suite


def _scorecard(session: Session, suite_ids: list[uuid.UUID], run_ids: list[uuid.UUID]) -> Scorecard:
    """Per-dimension coverage + score for an asset (#889)."""
    weights = scoring_settings_service.weights(session)
    # ── what exists (coverage) ──
    check_rows = session.execute(
        select(Check.dimension, func.count())
        .where(Check.suite_id.in_(suite_ids))
        .group_by(Check.dimension)
    ).all()
    checks_by_dimension = {d: n for d, n in check_rows if d is not None}
    unclassified = sum(n for d, n in check_rows if d is None)

    # ── how the latest run went (score) ── A plain dict, NOT a defaultdict: reading
    # `histograms[dim]` below would CREATE the key, silently mutating the mapping while iterating
    # over coverage.
    histograms: dict[str, dict[str, int]] = {}
    if run_ids:
        result_rows = session.execute(
            select(Check.dimension, Result.status, func.count())
            .select_from(Result)
            .join(Check, Check.id == Result.check_id)
            # Only runs whose result set is complete may be scored (#318).
            .join(Run, Run.id == Result.run_id)
            .where(Result.run_id.in_(run_ids), Run.status.in_(AGGREGATABLE_RUN_STATUSES))
            .group_by(Check.dimension, Result.status)
        ).all()
        for dimension, status, count in result_rows:
            if dimension is not None:
                histograms.setdefault(dimension, {})[status] = count

    covered = []
    for dimension, total in sorted(checks_by_dimension.items()):
        hist = histograms.get(dimension, {})
        covered.append(
            DimensionScore(
                dimension=dimension,
                checks_total=total,
                checks_passing=hist.get("pass", 0),
                checks_evaluated=evaluated_total(hist),
                # `None` when nothing EVALUATED — no run yet, or every result
                # skipped/errored. Distinct from 0, which means it ran and failed.
                score=health_score(hist, weights) if hist else None,
            )
        )
    uncovered = sorted(set(DQ_DIMENSIONS) - set(checks_by_dimension))
    return Scorecard(covered=covered, uncovered=uncovered, unclassified_checks=unclassified)


def _roll_up(asset: Asset, suite_outcomes: list[RunOutcome], weights: Weights) -> AssetSummary:
    """Roll the latest-run outcomes of ALL composing suites up into the asset-level
    health summary. Workspace-true (ADR 0037): the input is never grant-filtered,
    so every viewer computes — and sees — the same verdict.
    """
    statuses: list[str] = []
    checks_total = checks_passed = 0
    last_run_at: datetime | None = None
    has_failed_run = has_active_run = has_cancelled_run = False
    has_operational_error = has_skip = False
    counts: dict[str, int] = defaultdict(int)
    for run in suite_outcomes:
        for status, n in run.counts.items():
            counts[status] += n
        if run.worst_severity is not None:
            statuses.append(run.worst_severity)
        # Execution state, distinct from check severity (see AssetSummary): a `failed` run wrote no
        # results and must not roll up green; an active run hasn't concluded yet.
        if run.status == "failed":
            has_failed_run = True
        elif run.status in ("queued", "running"):
            has_active_run = True
        elif run.status == "cancelled":
            has_cancelled_run = True
        # Connection health (#803): a run that failed outright, or one that ran but whose checks
        # threw, both mean DataQ could not evaluate against the datasource.
        if run.status == "failed" or run.has_error:
            has_operational_error = True
        if run.has_skip:
            has_skip = True
        checks_total += run.checks_total
        checks_passed += run.checks_passed
        ts = run.finished_at or run.created_at
        if ts is not None and (last_run_at is None or ts > last_run_at):
            last_run_at = ts
    return AssetSummary(
        id=asset.id,
        namespace=asset.namespace,
        name=asset.name,
        env=asset.env,
        description=asset.description,
        owner_user_id=asset.owner_user_id,
        last_seen=asset.last_seen,
        auto_coverage_excluded=asset.auto_coverage_excluded,
        suite_count=len(suite_outcomes),
        worst_severity=worst_severity(statuses),
        checks_total=checks_total,
        checks_passed=checks_passed,
        last_run_at=last_run_at,
        health_score=health_score(counts, weights),
        has_failed_run=has_failed_run,
        has_active_run=has_active_run,
        has_cancelled_run=has_cancelled_run,
        has_operational_error=has_operational_error,
        has_skip=has_skip,
    )


# ── public API ───────────────────────────────────────────────────────────────


def count_assets(session: Session) -> int:
    """Total assets over the same population `list_visible_assets` pages through
    (#925) — unfiltered (ADR 0037: identity is workspace knowledge, not
    grant-scoped), so the count a client divides its `limit`/`offset` paging
    against always matches what the list endpoint can actually return.
    """
    return session.scalar(select(func.count()).select_from(Asset)) or 0


def _asset_scores(weights: Weights) -> Any:
    """``asset_id -> health score`` as a subquery, the number `_roll_up` computes in
    Python but left UNROUNDED: Postgres and Python round a half differently (81.25 is
    81.3 there and 81.2 here), so ordering on a rounded key could contradict the scores
    the page displays.
    """
    latest = latest_runs_per_suite_stmt(
        select(Suite.id).where(Suite.asset_id.is_not(None))
    ).subquery()
    penalty = case(
        *((Result.status == s, weights.penalty(s)) for s in SEVERITY_STATUSES), else_=0.0
    )
    score = 100.0 * (1.0 - cast(func.sum(penalty), Float) / (func.count() * weights.critical))
    return (
        select(Suite.asset_id.label("asset_id"), score.label("score"))
        .select_from(Suite)
        .join(latest, latest.c.suite_id == Suite.id)
        .join(Result, Result.run_id == latest.c.id)
        .where(
            latest.c.status.in_(AGGREGATABLE_RUN_STATUSES),
            Result.status.in_(SEVERITY_STATUSES),
        )
        .group_by(Suite.asset_id)
        .subquery()
    )


def list_visible_assets(
    session: Session,
    *,
    limit: int = 200,
    offset: int = 0,
    sort: str = "name",
) -> list[AssetSummary]:
    """Every asset, fully identified, paginated with ``limit``/``offset`` — identical
    output for every caller (ADR 0037), which is why this takes no user: identity is
    workspace knowledge and the rollup is workspace-true (aggregated over ALL composing
    suites, never grant-filtered).

    Sorted by ``(namespace, name)``, or with ``sort="health_score"`` lowest score first
    over the WHOLE population (not just the page), unscored assets last.
    """
    weights = scoring_settings_service.weights(session)
    stmt = select(Asset)
    if sort == "health_score":
        scores = _asset_scores(weights)
        stmt = stmt.outerjoin(scores, scores.c.asset_id == Asset.id).order_by(
            scores.c.score.asc().nulls_last()
        )
    assets = list(
        session.scalars(stmt.order_by(Asset.namespace, Asset.name).limit(limit).offset(offset))
    )
    if not assets:
        return []
    page_ids = [a.id for a in assets]
    suites = list(session.scalars(select(Suite).where(Suite.asset_id.in_(page_ids))))
    outcome_by_suite = _latest_outcomes(session, suites)
    by_asset: dict[uuid.UUID, list[RunOutcome]] = defaultdict(list)
    for suite in suites:
        assert suite.asset_id is not None  # filtered on asset_id above
        by_asset[suite.asset_id].append(outcome_by_suite[suite.id])
    return [_roll_up(asset, by_asset.get(asset.id, []), weights) for asset in assets]


def _inherited_classifications(
    session: Session, asset: Asset
) -> tuple[list[InheritedClassification] | None, bool]:
    """What `column_tags.effective_column_tags` adds to the asset's own tags, and from where.

    Fail-soft in a SAVEPOINT like the redaction path: a lineage read error reports None
    (unknown), and the asset page still renders.
    """
    try:
        with session.begin_nested():
            cells, truncated = column_tags.inherited_sensitive_with_truncation(session, asset)
    except Exception as exc:
        log.warning(
            "inherited_classifications_failed",
            asset_id=str(asset.id),
            error_type=type(exc).__name__,
        )
        return None, False
    ids = {aid for sources in cells.values() for aid, _ in sources}
    names: dict[uuid.UUID, str] = (
        dict(session.execute(select(Asset.id, Asset.name).where(Asset.id.in_(ids))).tuples().all())
        if ids
        else {}
    )
    entries = [
        InheritedClassification(
            column=column,
            sources=[
                InheritedSource(asset_id=aid, asset_name=names.get(aid, str(aid)), column=col)
                for aid, col in sources
            ],
        )
        for column, sources in sorted(cells.items())
    ]
    return entries, truncated


def get_visible_asset(
    session: Session, asset_id: uuid.UUID, *, user_id: uuid.UUID, include_all: bool = False
) -> AssetDetail:
    """One asset's detail (workspace-true aggregation + the caller's per-suite
    breakdown + lineage). Opens for **every** member (ADR 0037) — only a truly
    unknown id raises `AssetNotFoundError` (404).
    """
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})
    all_suites = list(
        session.scalars(select(Suite).where(Suite.asset_id == asset_id).order_by(Suite.name))
    )
    # ONE visibility derivation (#924 review): `effective_permissions` encodes the same
    # owned/shared/workspace-admin rule `accessible_suite_ids` does (both resolve the admin off the
    # same allowlist), and it must be called anyway to label the rows — so a suite is listed iff it
    # has a label.
    levels = effective_permissions(session, all_suites, user_id)
    visible = [s for s in all_suites if include_all or levels.get(s.id)]

    # Latest runs / outcomes over ALL composing suites, computed ONCE — the
    # workspace-true rollup and the grant-filtered breakdown read the same map.
    outcome_by_suite = _latest_outcomes(session, all_suites)
    composing = _composing_suites(visible, levels, outcome_by_suite)

    summary = _roll_up(
        asset,
        [outcome_by_suite[s.id] for s in all_suites],
        scoring_settings_service.weights(session),
    )
    # Workspace-true, like the summary: ALL composing suites, never `visible`.
    scorecard = _scorecard(
        session,
        [s.id for s in all_suites],
        [o.run_id for o in outcome_by_suite.values() if o.run_id],
    )
    graph = lineage_neighbourhood(session, asset_id)
    neighbour_ids = [a.id for a, _ in graph.upstream] + [a.id for a, _ in graph.downstream]
    # One grouped lookup of "which of these assets has any suite" — the structural
    # `is_monitored` fact on the nodes.
    has_suite = _monitored_ids(session, neighbour_ids)
    inherited, inherited_truncated = _inherited_classifications(session, asset)
    return AssetDetail(
        summary=summary,
        suites=composing,
        scorecard=scorecard,
        restricted_suite_count=len(all_suites) - len(composing),
        upstream=_lineage_nodes(graph.upstream, has_suite),
        downstream=_lineage_nodes(graph.downstream, has_suite),
        lineage_edges=_lineage_edge_refs(session, graph.edges),
        # Source-health advisories name workspace connections — which every member can already read
        # off `GET /connections` (unscoped since Week 2).
        failing_lineage_sources=failing_lineage_sources(session),
        warehouse_lineage_status=warehouse_lineage_status(session),
        inherited_classifications=inherited,
        inherited_classifications_truncated=inherited_truncated,
    )


def warehouse_lineage_status(session: Session) -> list[WarehouseLineageStatus]:
    """Warehouse-native lineage sources that are degraded, failing — or STALE (#1091)."""
    settings = get_settings()
    stale_after_hours = settings.lineage_stale_after_hours
    stale_before = (
        datetime.now(UTC) - timedelta(hours=stale_after_hours)
        if settings.warehouse_lineage_enabled and stale_after_hours > 0
        else None
    )
    # #1236: a prune suspension is only meaningful for a SNAPSHOT source — an incremental
    # one never prunes, so "has not pruned" would read as a fault on a healthy connection.
    snapshot_types = snapshot_lineage_connection_types()
    suspended_condition = and_(
        Connection.type.in_(snapshot_types),
        # NULL stamp = no prune ever recorded. Reported (the graph can only grow) but never a
        # backstop trigger — see `warehouse_refresh._suspension_exhausted`.
        or_(
            Connection.lineage_last_authoritative_refresh_at.is_(None),
            Connection.lineage_last_refresh_at > Connection.lineage_last_authoritative_refresh_at,
        ),
    )
    conditions: list[ColumnElement[bool]] = [
        Connection.lineage_degraded_reason.is_not(None),
        Connection.lineage_last_error.is_not(None),
        suspended_condition,
    ]
    if stale_before is not None:
        conditions.append(Connection.lineage_last_refresh_at < stale_before)

    rows = session.scalars(
        select(Connection).where(
            Connection.type.in_(WAREHOUSE_LINEAGE_CONNECTION_TYPES),
            Connection.lineage_last_refresh_at.is_not(None),
            or_(*conditions),
        )
    ).all()
    return [
        WarehouseLineageStatus(
            connection_id=c.id,
            name=c.name,
            type=c.type,
            tier=c.lineage_last_tier,
            degraded_reason=c.lineage_degraded_reason,
            last_error=c.lineage_last_error,
            last_refreshed_at=c.lineage_last_refresh_at,
            stale=bool(
                stale_before is not None
                and c.lineage_last_refresh_at is not None
                and c.lineage_last_refresh_at < stale_before
            ),
            prune_suspended=_prune_suspended(c, snapshot_types),
            prune_suspended_since=c.lineage_last_authoritative_refresh_at,
        )
        for c in rows
    ]


def _prune_suspended(connection: Connection, snapshot_types: tuple[str, ...]) -> bool:
    """Did the connection's most recent refresh leave stale edges unpruned (#1236)?

    Reported ALONGSIDE `last_error`/`stale`, never instead of them: a failing source is
    also a non-pruning one, and folding either into the other is the #987 shape — one
    field silently suppressing another.
    """
    if connection.type not in snapshot_types or connection.lineage_last_refresh_at is None:
        return False
    last_pruned = connection.lineage_last_authoritative_refresh_at
    return last_pruned is None or connection.lineage_last_refresh_at > last_pruned


def lineage_qualifiers(
    failing_sources: Sequence[LineageSourceHealth],
    warehouse_status: Sequence[WarehouseLineageStatus],
) -> list[str]:
    """The human/LLM-readable qualifier strings for a lineage graph fed by
    ``failing_sources``/``warehouse_status`` — the SHARED SEAM behind `get_asset`'s
    `lineage.qualified_by` **and** `downstream_blast_radius` (#1990). Any consumer
    of the lineage graph reuses this rather than re-deriving its own wording, so a
    prune-suspension is always stated the same way everywhere it appears.

    A prune-suspension is the one qualifier that runs the OTHER direction from the
    rest: a failing/stale/coarse source under-reports (missing edges), while a
    suspended prune can only accrete (extra edges, some possibly removed from the
    warehouse) — the wording says so explicitly rather than folding it into a
    generic "may be incomplete".
    """
    qualifiers: list[str] = []
    for src in failing_sources:
        qualifiers.append(
            f"lineage poll failing on connection '{src.name}' "
            f"({src.consecutive_failures} consecutive failures)"
        )
    for wh in warehouse_status:
        if wh.last_error:
            qualifiers.append(f"warehouse lineage refresh failing on '{wh.name}'")
        if wh.stale:
            qualifiers.append(f"warehouse lineage on '{wh.name}' has not refreshed recently")
        if wh.degraded_reason:
            qualifiers.append(f"warehouse lineage on '{wh.name}' is coarse: {wh.degraded_reason}")
        if wh.prune_suspended:
            # Stated as accretion, not staleness: the edges may include removed
            # dependencies, which is the opposite failure from a missing one.
            since = wh.prune_suspended_since
            when = (
                f"has not pruned removed edges since {since.isoformat()}"
                if since
                else "has never pruned removed edges"
            )
            qualifiers.append(
                f"warehouse lineage on '{wh.name}' {when} — edges below may include "
                "dependencies that no longer exist (new edges are still discovered "
                "normally, so this is a risk of EXTRA edges, not missing ones)"
            )
    return qualifiers


def failing_lineage_sources(session: Session) -> list[LineageSourceHealth]:
    """Lineage-feeding connections whose poll is currently failing (#828)."""
    rows = session.scalars(
        select(Connection).where(
            Connection.type == "dbt",
            Connection.consecutive_poll_failures > 0,
        )
    ).all()
    return [
        LineageSourceHealth(
            connection_id=c.id,
            name=c.name,
            type=c.type,
            consecutive_failures=c.consecutive_poll_failures,
            last_error=c.last_poll_error,
            last_polled_at=c.last_polled_at,
        )
        for c in rows
    ]


def summarize_asset(session: Session, asset: Asset) -> AssetSummary:
    """Roll one already-loaded asset up into its list-row summary — workspace-true
    (ADR 0037), so it takes no user. An asset with zero suites rolls up to an
    empty (no-run) health summary. Used by the admin PATCH response, where the
    asset need not have suites to have metadata.
    """
    suites = list(session.scalars(select(Suite).where(Suite.asset_id == asset.id)))
    outcome_by_suite = _latest_outcomes(session, suites)
    return _roll_up(
        asset, [outcome_by_suite[s.id] for s in suites], scoring_settings_service.weights(session)
    )


def _monitored_ids(session: Session, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of ``ids`` have ≥1 suite targeting them (globally — a structural fact,
    not a grant). One grouped query for the whole neighbourhood (no N+1).
    """
    if not ids:
        return set()
    return {
        asset_id
        for (asset_id,) in session.execute(
            select(Suite.asset_id).where(Suite.asset_id.in_(ids)).group_by(Suite.asset_id)
        )
    }


@dataclass(frozen=True)
class AssetCoverage:
    """How many of a connection's known assets any suite targets (#1701)."""

    total: int
    unmonitored: int


def coverage_by_connection(
    session: Session, connection_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AssetCoverage]:
    """Per connection: assets DataQ knows of, and how many no suite targets.

    Counted over the whole workspace, not the caller's grants — the same
    workspace-true rollup ADR 0037 defines for assets. Connections with no known
    assets are absent from the mapping rather than present with a zero.
    """
    if not connection_ids:
        return {}
    rows = session.execute(
        select(Asset.connection_id, Asset.id).where(Asset.connection_id.in_(connection_ids))
    ).all()
    if not rows:
        return {}
    monitored = _monitored_ids(session, [asset_id for _, asset_id in rows])
    totals: dict[uuid.UUID, list[int]] = defaultdict(lambda: [0, 0])
    for connection_id, asset_id in rows:
        entry = totals[connection_id]
        entry[0] += 1
        if asset_id not in monitored:
            entry[1] += 1
    return {cid: AssetCoverage(total=t, unmonitored=u) for cid, (t, u) in totals.items()}


@dataclass(frozen=True)
class ColumnTraceView:
    """A column trace plus what it needs to be read honestly: the identities of every asset it
    names, and the TABLE-level lineage qualifiers (a trace is never more complete than the table
    graph it walks — a failing or coarse source under-reports columns exactly as it does tables).
    """

    trace: lineage_columns.ColumnTrace
    assets: list[LineageNode]
    qualified_by: list[str]


def trace_asset_column(
    session: Session,
    asset_id: uuid.UUID,
    column: str,
    *,
    direction: lineage_columns.TraceDirection = lineage_columns.TraceDirection.BOTH,
    max_depth: int = lineage_columns.DEFAULT_TRACE_DEPTH,
) -> ColumnTraceView:
    """Column-grain provenance / impact for one asset column (#1710). Workspace-visible like the
    rest of the lineage topology (ADR 0037); only an unknown asset id raises (404).
    """
    if session.get(Asset, asset_id) is None:
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})
    trace = lineage_columns.trace_column(
        session, asset_id, column, direction=direction, max_depth=max_depth
    )
    ids = {asset_id}
    ids.update(n.asset_id for n in (*trace.upstream, *trace.downstream))
    for gap in trace.gaps:
        ids.update((gap.upstream_asset_id, gap.downstream_asset_id))
    by_id = {a.id: a for a in session.scalars(select(Asset).where(Asset.id.in_(ids)))}
    monitored = _monitored_ids(session, list(by_id))
    return ColumnTraceView(
        trace=trace,
        assets=_lineage_nodes(
            [(by_id[i], 0) for i in sorted(by_id, key=lambda i: (by_id[i].name, str(i)))],
            monitored,
        ),
        qualified_by=lineage_qualifiers(
            failing_lineage_sources(session), warehouse_lineage_status(session)
        ),
    )


def _lineage_edge_refs(
    session: Session,
    edges: list[tuple[uuid.UUID, uuid.UUID]],
) -> list[LineageEdgeRef]:
    """The neighbourhood's edges with their column-level refinement (#901) and WHY an edge has
    no pairs when it has none (#1710) — shown in full to every member (ADR 0037 — column names
    are schema metadata, i.e. identity). Unioned across the sources that observed the edge.
    """
    if not edges:
        return []
    refined = lineage_columns.edge_columns(session, edges)
    refs: list[LineageEdgeRef] = []
    for up, down in edges:
        info = refined.get((up, down))
        refs.append(
            LineageEdgeRef(
                source=up,
                target=down,
                columns=info.pairs if info is not None and info.pairs else None,
                column_coverage=(
                    info.coverage if info is not None else lineage_columns.ColumnCoverage.UNKNOWN
                ),
            )
        )
    return refs


def _lineage_nodes(
    assets: list[tuple[Asset, int]],
    monitored: set[uuid.UUID],
) -> list[LineageNode]:
    """Map reachable lineage assets (+ their hop depth) to render-only nodes —
    fully named for every member (ADR 0037); ``is_monitored`` is the true
    structural fact.
    """
    return [
        LineageNode(
            id=a.id,
            namespace=a.namespace,
            name=a.name,
            env=a.env,
            is_monitored=a.id in monitored,
            depth=depth,
        )
        for a, depth in assets
    ]


def update_asset_metadata(
    session: Session,
    asset_id: uuid.UUID,
    *,
    owner_user_id: uuid.UUID | None = None,
    description: str | None = None,
    set_owner: bool = False,
    set_description: bool = False,
    auto_coverage_excluded: bool | None = None,
    actor_id: uuid.UUID | None = None,
) -> Asset:
    """Set an asset's owner and/or description (workspace-Admin-only; gated at API)."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})
    audit_before = audit_service.snapshot("asset", asset)
    if set_owner:
        if owner_user_id is not None and session.get(User, owner_user_id) is None:
            raise AssetOwnerInvalidError(
                "owner user does not exist", detail={"owner_user_id": str(owner_user_id)}
            )
        asset.owner_user_id = owner_user_id
    if set_description:
        asset.description = description
    if auto_coverage_excluded is not None:
        asset.auto_coverage_excluded = auto_coverage_excluded
    # Metadata mutation only (ADR 0041 §2.5). The inventory-sync column family and
    # `first_seen`/`last_seen` are machine writes and never reach a payload.
    audit_service.record_entity_change(
        session,
        action="asset.update",
        entity_type="asset",
        entity=asset,
        actor=actor_id,
        before=audit_before,
        if_changed=True,
    )
    session.commit()
    session.refresh(asset)
    log.info("asset_metadata_updated", asset_id=str(asset.id))
    return asset
