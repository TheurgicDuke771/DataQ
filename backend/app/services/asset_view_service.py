"""Read-only asset view — the browse/reason surface over `assets` (ADR 0034, #760)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, or_, select, tuple_
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    DQ_DIMENSIONS,
    Asset,
    Check,
    Connection,
    LineageEdge,
    Result,
    Run,
    Suite,
    User,
    worst_severity,
)
from backend.app.lineage.edges import lineage_neighbourhood
from backend.app.services import audit_service
from backend.app.services.rollup import (
    AGGREGATABLE_RUN_STATUSES,
    evaluated_total,
    health_score,
    latest_runs_per_suite_stmt,
)
from backend.app.services.run_service import check_outcome_counts, operational_result_flags
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
    # ── connection health (reachability / execution) ── `has_failed_run`: any latest run whose
    # *execution* `failed` (wrote no results).
    has_failed_run: bool = False
    has_active_run: bool = False
    has_cancelled_run: bool = False
    has_operational_error: bool = False
    has_skip: bool = False


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
    # can be qualified ("view-level only".
    warehouse_lineage_status: list[WarehouseLineageStatus] = field(default_factory=list)


# ── internals ────────────────────────────────────────────────────────────────


def _latest_run_per_suite(session: Session, suite_ids: list[uuid.UUID]) -> dict[uuid.UUID, Run]:
    """The most-recent run for each suite (DISTINCT ON, newest `created_at`)."""
    if not suite_ids:
        return {}
    rows = session.scalars(latest_runs_per_suite_stmt(suite_ids))
    return {run.suite_id: run for run in rows}


def _run_outcome(
    run: Run | None,
    outcome: tuple[int, int, str | None] | None,
    op_flags: tuple[bool, bool] | None = None,
) -> RunOutcome:
    """Assemble a `RunOutcome` from a suite's latest run + its check-outcome tuple
    + its operational (`error`/`skip`) flags.
    """
    if run is None:
        return RunOutcome()
    total, passed, worst = outcome or (0, 0, None)
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
    Three grouped queries total (latest runs, check outcomes, operational flags).
    """
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    # An asset scorecard states how the ASSET is doing, so a partial or stranded result set must not
    # contribute (#318).
    outcomes = check_outcome_counts(session, run_ids, complete_runs_only=True)
    op_flags = operational_result_flags(session, run_ids)
    by_suite: dict[uuid.UUID, RunOutcome] = {}
    for suite in suites:
        run = latest_runs.get(suite.id)
        outcome = outcomes.get(run.id) if run is not None else None
        flags = op_flags.get(run.id) if run is not None else None
        by_suite[suite.id] = _run_outcome(run, outcome, flags)
    return by_suite


def _scorecard(session: Session, suite_ids: list[uuid.UUID], run_ids: list[uuid.UUID]) -> Scorecard:
    """Per-dimension coverage + score for an asset (#889)."""
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
                score=health_score(hist) if hist else None,
            )
        )
    uncovered = sorted(set(DQ_DIMENSIONS) - set(checks_by_dimension))
    return Scorecard(covered=covered, uncovered=uncovered, unclassified_checks=unclassified)


def _roll_up(asset: Asset, suite_outcomes: list[RunOutcome]) -> AssetSummary:
    """Roll the latest-run outcomes of ALL composing suites up into the asset-level
    health summary. Workspace-true (ADR 0037): the input is never grant-filtered,
    so every viewer computes — and sees — the same verdict.
    """
    statuses: list[str] = []
    checks_total = checks_passed = 0
    last_run_at: datetime | None = None
    has_failed_run = has_active_run = has_cancelled_run = False
    has_operational_error = has_skip = False
    for run in suite_outcomes:
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
        suite_count=len(suite_outcomes),
        worst_severity=worst_severity(statuses),
        checks_total=checks_total,
        checks_passed=checks_passed,
        last_run_at=last_run_at,
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


def list_visible_assets(
    session: Session,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[AssetSummary]:
    """Every asset, fully identified, sorted by ``(namespace, name)`` and paginated
    with ``limit``/``offset`` — identical output for every caller (ADR 0037), which
    is why this takes no user: identity is workspace knowledge and the rollup is
    workspace-true (aggregated over ALL composing suites, never grant-filtered).
    """
    assets = list(
        session.scalars(
            select(Asset).order_by(Asset.namespace, Asset.name).limit(limit).offset(offset)
        )
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
    return [_roll_up(asset, by_asset.get(asset.id, [])) for asset in assets]


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
    levels = effective_permissions(session, all_suites, user_id)
    visible = [s for s in all_suites if include_all or levels.get(s.id)]

    # Latest runs / outcomes over ALL composing suites, computed ONCE — the
    # workspace-true rollup and the grant-filtered breakdown read the same map.
    outcome_by_suite = _latest_outcomes(session, all_suites)
    composing = _composing_suites(visible, levels, outcome_by_suite)

    summary = _roll_up(asset, [outcome_by_suite[s.id] for s in all_suites])
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
    conditions: list[ColumnElement[bool]] = [
        Connection.lineage_degraded_reason.is_not(None),
        Connection.lineage_last_error.is_not(None),
    ]
    if stale_before is not None:
        conditions.append(Connection.lineage_last_refresh_at < stale_before)

    rows = session.scalars(
        select(Connection).where(
            Connection.type.in_(("snowflake", "unity_catalog")),
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
        )
        for c in rows
    ]


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
    return _roll_up(asset, [outcome_by_suite[s.id] for s in suites])


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


def _lineage_edge_refs(
    session: Session,
    edges: list[tuple[uuid.UUID, uuid.UUID]],
) -> list[LineageEdgeRef]:
    """The neighbourhood's edges with their column-level refinement (#901), shown
    in full to every member (ADR 0037 — column names are schema metadata, i.e.
    identity). Column data is unioned across the sources that observed the edge
    (two provenance rows for one asset pair are one drawn edge).
    """
    if not edges:
        return []
    pairs: dict[tuple[uuid.UUID, uuid.UUID], set[tuple[str, str]]] = {}
    for up, down, cols in session.execute(
        select(
            LineageEdge.upstream_asset_id,
            LineageEdge.downstream_asset_id,
            LineageEdge.columns,
        ).where(
            tuple_(LineageEdge.upstream_asset_id, LineageEdge.downstream_asset_id).in_(edges),
            LineageEdge.columns.is_not(None),
            # Exclude JSON 'null' in SQL (#907) — rows bulk-written before
            # `none_as_null` carry it and pass `is_not(None)`.
            func.jsonb_typeof(LineageEdge.columns) != "null",
        )
    ):
        # Defensive shape check: `columns` is app-written JSONB, but a malformed value must degrade
        # to "skipped", never 500 the asset page.
        if not isinstance(cols, (list, tuple)):
            log.warning(
                "lineage_edge_columns_malformed",
                upstream_asset_id=str(up),
                downstream_asset_id=str(down),
                value_type=type(cols).__name__,
            )
            continue
        valid = [
            (str(entry[0]), str(entry[1]))
            for entry in cols
            if isinstance(entry, (list, tuple)) and len(entry) == 2
        ]
        # The loud-degradation contract covers ENTRIES too (#924 review): a wrong-arity/non-list
        # item inside a well-formed list must not vanish silently.
        if len(valid) != len(cols):
            log.warning(
                "lineage_edge_column_entries_malformed",
                upstream_asset_id=str(up),
                downstream_asset_id=str(down),
                dropped=len(cols) - len(valid),
            )
        pairs.setdefault((up, down), set()).update(valid)
    return [
        LineageEdgeRef(
            source=up,
            target=down,
            columns=tuple(sorted(cols)) if (cols := pairs.get((up, down))) else None,
        )
        for up, down in edges
    ]


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
