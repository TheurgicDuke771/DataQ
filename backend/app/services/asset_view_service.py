"""Read-only asset view — the browse/reason surface over `assets` (ADR 0034, #760).

Assets are what users *reason about*; suites remain how checks *execute* (ADR 0034
guiding principle). This module aggregates, per asset, the suites that target it +
their latest run health + the lineage neighbourhood, for the `/assets` API.

**The ADR 0037 three-layer rule (supersedes ADR 0034 decision 5 + #845/#846/#920):**

- **Identity & topology are workspace knowledge.** Every authenticated member sees
  every asset's full identity (name, namespace, env, description, owner,
  ``last_seen``) and the full lineage neighbourhood — named nodes, real edges, and
  column-level pairs. Nothing here is redacted, anonymized, or 404'd: the detail
  endpoint opens for every existing asset, and only a truly unknown id 404s.
- **Aggregate verdicts are workspace-true.** The health rollup (``worst_severity``,
  check counts, run-state flags, ``suite_count``) is computed over **all** composing
  suites for **every** viewer — one truth per asset, never a per-viewer partial that
  silently disagrees between users (#889's two-verdicts-on-one-page problem).
- **Itemized evaluation stays behind the ADR 0027 suite grants.** The per-suite
  breakdown on the detail page lists only suites the caller can view
  (`suite_service.accessible_suite_ids` — the single source of truth, so this can
  never drift from the suites/runs surfaces); the rest collapse to
  ``restricted_suite_count`` — a count, never names. Suite/run/result/sample/incident
  endpoints keep their own grant scoping and 404-no-leak at the suite grain.

Asset-metadata mutation (owner, description) is workspace-Admin-only — enforced at
the API layer (`require_workspace_admin`), not here; `update_asset_metadata` is the
plain persistence half.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    Asset,
    Connection,
    LineageEdge,
    Run,
    Suite,
    User,
    worst_severity,
)
from backend.app.lineage.edges import lineage_neighbourhood
from backend.app.services.run_service import check_outcome_counts, operational_result_flags
from backend.app.services.suite_authz import effective_permissions
from backend.app.services.suite_service import accessible_suite_ids

log = get_logger(__name__)


class AssetNotFoundError(DataQError):
    """Raised when an asset id names no asset. Identity is workspace-visible
    (ADR 0037), so — unlike the suite endpoints — there is no no-leak case here:
    every existing asset opens for every member."""

    status_code = 404
    code = "asset_not_found"


class AssetOwnerInvalidError(DataQError):
    """The `owner_user_id` on a metadata update names no existing user — checked
    up front (the share-grant FK-precheck idiom, `share_service.grant_share`) so a
    bad id is a clean 422, never a raw FK IntegrityError surfacing as 500."""

    status_code = 422
    code = "asset_owner_invalid"


@dataclass(frozen=True)
class RunOutcome:
    """A suite's latest run outcome — execution status + the data-quality summary.

    ``worst_severity`` is the highest failing tier among evaluated checks (warn <
    fail < critical), or ``None`` when all passed / nothing evaluated. ``run_id`` /
    timestamps are ``None`` when the suite has never run.

    ``has_error`` / ``has_skip`` are the run's **operational** results (#122) — a
    check the datasource threw on, or one whose precondition wasn't met. They are
    deliberately *not* severity: they say nothing about data quality, only about
    whether DataQ could evaluate at all, and so feed connection health (#803)."""

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

    **Two orthogonal health axes (#803).** The old single "health" conflated them:

    - *Suite health* (data quality, ADR 0005) — ``worst_severity`` over the
      **evaluated** checks, plus ``checks_total``/``checks_passed``. Operational
      results never rank here, so a datasource DataQ couldn't reach reads as "no
      data", never as a green "passing" nor as a red data failure.
    - *Connection health* (reachability) — ``has_operational_error`` /
      ``has_skip``: could DataQ execute against the datasource at all? Derived
      **purely from the runs already recorded** (a latest run that `failed`, or
      any ``error``/``skip`` result on one) — there is no connection-probe polling
      loop behind this, by design.
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
    # ── connection health (reachability / execution) ──
    # `has_failed_run`: any latest run whose *execution* `failed` (wrote no results).
    # `has_active_run`: any latest run still `queued`/`running` (hasn't concluded).
    # `has_cancelled_run`: any latest run `cancelled`. A cancelled run proves
    #   nothing — if it was killed before a single check ran, we may never have
    #   reached the datasource at all, so it must not roll up green.
    # `has_operational_error`: a failed run OR any `error` result — DataQ could not
    #   evaluate against the datasource. `has_skip`: any `skip` result (a
    #   precondition, e.g. the batch hasn't landed, wasn't met) — degraded, not down.
    has_failed_run: bool = False
    has_active_run: bool = False
    has_cancelled_run: bool = False
    has_operational_error: bool = False
    has_skip: bool = False


@dataclass(frozen=True)
class LineageNode:
    """A lineage neighbour — enough to render, no run data (ADR 0034 §2).

    ``depth`` is the hop distance from the asset under view (1 = a direct
    neighbour), which is what lets the UI lay the graph out in columns (#805)
    instead of flattening every hop into one list.

    Fully named for every member (ADR 0037): lineage topology is identity, and
    identity is workspace knowledge. ``is_monitored`` is the true structural fact.
    """

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    is_monitored: bool
    depth: int = 1


@dataclass(frozen=True)
class LineageEdgeRef:
    """One edge of the neighbourhood DAG, as ``(upstream → downstream)`` asset ids.

    The UI draws exactly these; without them a graph could only *guess* which node
    at depth 2 hangs off which node at depth 1 (#805).

    ``columns`` is the edge's column-level refinement (#901) when a warehouse source
    recorded one — ``[upstream_column, downstream_column]`` pairs, unioned across the
    sources that observed the edge, shown to every member (a column name is schema
    metadata — identity, not measurement; ADR 0037). ``None`` ⇒ the edge simply has
    no column grain (a table-level source recorded it).
    """

    source: uuid.UUID
    target: uuid.UUID
    columns: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True)
class LineageSourceHealth:
    """Whether the integrations that FEED lineage are actually working (#828).

    Without this, an empty lineage graph is a lie by omission: "no lineage recorded" is
    rendered identically whether the asset genuinely has no upstreams or whether the dbt
    poll has been failing for six days behind an expired credential. The UI must be able
    to tell the user which one it is looking at.
    """

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

    Distinct from :class:`LineageSourceHealth` (a dbt-poll failure counter): a warehouse
    refresh has no poll counter, and its most important signal is the *tier* — a healthy
    refresh can still be degraded (``OBJECT_DEPENDENCIES`` view-level only because the
    account isn't Enterprise). ``degraded_reason`` is that "working but coarse" note;
    ``last_error`` is a genuine refresh failure (classified). A source with neither is
    healthy and is NOT listed (no banner over a clean full-tier graph).
    """

    connection_id: uuid.UUID
    name: str
    type: str
    tier: str | None
    degraded_reason: str | None
    last_error: str | None
    last_refreshed_at: datetime | None


@dataclass(frozen=True)
class AssetDetail:
    """Asset detail: the workspace-true summary + the caller's per-suite breakdown
    + lineage. ``suites`` lists only suites the caller can view (ADR 0027);
    ``restricted_suite_count`` is how many more compose the asset — those still
    roll into ``summary`` (workspace-true, ADR 0037) but stay unnamed."""

    summary: AssetSummary
    suites: list[ComposingSuite]
    restricted_suite_count: int = 0
    upstream: list[LineageNode] = field(default_factory=list)
    downstream: list[LineageNode] = field(default_factory=list)
    lineage_edges: list[LineageEdgeRef] = field(default_factory=list)
    # Non-empty ⇒ a lineage source is broken, so the graph below may be stale or empty
    # for a reason that has nothing to do with this asset. Never show a clean empty
    # state over a broken integration.
    failing_lineage_sources: list[LineageSourceHealth] = field(default_factory=list)
    # Warehouse-native lineage sources that are degraded (coarser tier) or failing — so
    # the graph can be qualified ("view-level only", "last refreshed 2h ago") rather than
    # presented as complete + current (#828, #858).
    warehouse_lineage_status: list[WarehouseLineageStatus] = field(default_factory=list)


# ── internals ────────────────────────────────────────────────────────────────


def _latest_run_per_suite(session: Session, suite_ids: list[uuid.UUID]) -> dict[uuid.UUID, Run]:
    """The most-recent run for each suite (DISTINCT ON, newest `created_at`)."""
    if not suite_ids:
        return {}
    rows = session.scalars(
        select(Run)
        .where(Run.suite_id.in_(suite_ids))
        .distinct(Run.suite_id)
        .order_by(Run.suite_id, Run.created_at.desc())
    )
    return {run.suite_id: run for run in rows}


def _run_outcome(
    run: Run | None,
    outcome: tuple[int, int, str | None] | None,
    op_flags: tuple[bool, bool] | None = None,
) -> RunOutcome:
    """Assemble a `RunOutcome` from a suite's latest run + its check-outcome tuple
    + its operational (`error`/`skip`) flags."""
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
    latest_runs: dict[uuid.UUID, Run],
    outcomes: dict[uuid.UUID, tuple[int, int, str | None]],
    op_flags: dict[uuid.UUID, tuple[bool, bool]] | None = None,
) -> list[ComposingSuite]:
    """Build the per-suite breakdown for one asset's suites (sorted by name)."""
    op_flags = op_flags or {}
    composing: list[ComposingSuite] = []
    for suite in suites:
        level = levels.get(suite.id)
        if level is None:  # defensive: only reachable suites are passed in
            continue
        run = latest_runs.get(suite.id)
        outcome = outcomes.get(run.id) if run is not None else None
        flags = op_flags.get(run.id) if run is not None else None
        composing.append(
            ComposingSuite(
                suite_id=suite.id,
                name=suite.name,
                my_permission=level,
                latest_run=_run_outcome(run, outcome, flags),
            )
        )
    return composing


def _suite_outcomes(
    suites: list[Suite],
    latest_runs: dict[uuid.UUID, Run],
    outcomes: dict[uuid.UUID, tuple[int, int, str | None]],
    op_flags: dict[uuid.UUID, tuple[bool, bool]],
) -> dict[uuid.UUID, list[RunOutcome]]:
    """One ``RunOutcome`` per suite (empty for a never-run suite), grouped by
    asset — the workspace-true aggregation input (ADR 0037): EVERY composing
    suite contributes, independent of any caller's grants."""
    by_asset: dict[uuid.UUID, list[RunOutcome]] = defaultdict(list)
    for suite in suites:
        assert suite.asset_id is not None  # callers filter on asset_id
        run = latest_runs.get(suite.id)
        outcome = outcomes.get(run.id) if run is not None else None
        flags = op_flags.get(run.id) if run is not None else None
        by_asset[suite.asset_id].append(_run_outcome(run, outcome, flags))
    return by_asset


def _roll_up(asset: Asset, suite_outcomes: list[RunOutcome]) -> AssetSummary:
    """Roll the latest-run outcomes of ALL composing suites up into the asset-level
    health summary. Workspace-true (ADR 0037): the input is never grant-filtered,
    so every viewer computes — and sees — the same verdict."""
    statuses: list[str] = []
    checks_total = checks_passed = 0
    last_run_at: datetime | None = None
    has_failed_run = has_active_run = has_cancelled_run = False
    has_operational_error = has_skip = False
    for run in suite_outcomes:
        if run.worst_severity is not None:
            statuses.append(run.worst_severity)
        # Execution state, distinct from check severity (see AssetSummary): a
        # `failed` run wrote no results and must not roll up green; an active run
        # hasn't concluded yet.
        if run.status == "failed":
            has_failed_run = True
        elif run.status in ("queued", "running"):
            has_active_run = True
        elif run.status == "cancelled":
            has_cancelled_run = True
        # Connection health (#803): a run that failed outright, or one that ran but
        # whose checks threw, both mean DataQ could not evaluate against the
        # datasource. `skip` is weaker — it executed, a precondition just wasn't met.
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

    Pagination is applied at the SQL level over the *asset* page (not the suite
    rows), so a page is a stable, deterministic slice regardless of how many
    suites compose each asset."""
    assets = list(
        session.scalars(
            select(Asset).order_by(Asset.namespace, Asset.name).limit(limit).offset(offset)
        )
    )
    if not assets:
        return []
    page_ids = [a.id for a in assets]
    suites = list(session.scalars(select(Suite).where(Suite.asset_id.in_(page_ids))))
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    outcomes = check_outcome_counts(session, run_ids)
    op_flags = operational_result_flags(session, run_ids)
    by_asset = _suite_outcomes(suites, latest_runs, outcomes, op_flags)
    return [_roll_up(asset, by_asset.get(asset.id, [])) for asset in assets]


def get_visible_asset(
    session: Session, asset_id: uuid.UUID, *, user_id: uuid.UUID, include_all: bool = False
) -> AssetDetail:
    """One asset's detail (workspace-true aggregation + the caller's per-suite
    breakdown + lineage). Opens for **every** member (ADR 0037) — only a truly
    unknown id raises `AssetNotFoundError` (404).

    The caller shapes exactly one thing: which composing suites are *listed*
    (their grants; ``include_all`` for a workspace-admin). Suites outside the
    grants still roll into the summary — workspace-true, one verdict for every
    viewer — but surface only as ``restricted_suite_count``: a count, never
    names (suite names can reveal intent; the ADR 0027 boundary lives at the
    suite grain, where its 404-no-leak is intact)."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})
    all_suites = list(
        session.scalars(select(Suite).where(Suite.asset_id == asset_id).order_by(Suite.name))
    )
    # `accessible_suite_ids` is a SQL subquery (the single source of truth shared
    # with the suites/runs surfaces) — resolve it once for this asset's suites.
    accessible = accessible_suite_ids(user_id, include_all=include_all)
    accessible_ids = set(
        session.scalars(
            select(Suite.id).where(Suite.asset_id == asset_id, Suite.id.in_(accessible))
        )
    )
    visible = [s for s in all_suites if s.id in accessible_ids]

    levels = effective_permissions(session, visible, user_id)
    # Latest runs / outcomes over ALL composing suites — the workspace-true rollup
    # input; the grant-filtered `visible` list reuses the same lookups.
    latest_runs = _latest_run_per_suite(session, [s.id for s in all_suites])
    run_ids = [r.id for r in latest_runs.values()]
    outcomes = check_outcome_counts(session, run_ids)
    op_flags = operational_result_flags(session, run_ids)
    composing = _composing_suites(visible, levels, latest_runs, outcomes, op_flags)
    by_asset = _suite_outcomes(all_suites, latest_runs, outcomes, op_flags)

    summary = _roll_up(asset, by_asset.get(asset_id, []))
    graph = lineage_neighbourhood(session, asset_id)
    neighbour_ids = [a.id for a, _ in graph.upstream] + [a.id for a, _ in graph.downstream]
    # One grouped lookup of "which of these assets has any suite" — the structural
    # `is_monitored` fact on the nodes.
    has_suite = _monitored_ids(session, neighbour_ids)
    return AssetDetail(
        summary=summary,
        suites=composing,
        restricted_suite_count=len(all_suites) - len(composing),
        upstream=_lineage_nodes(graph.upstream, has_suite),
        downstream=_lineage_nodes(graph.downstream, has_suite),
        lineage_edges=_lineage_edge_refs(session, graph.edges),
        # Source-health advisories name workspace connections — which every member
        # can already read off `GET /connections` (unscoped since Week 2), so there
        # is nothing to gate (ADR 0037 retires the former stake gate). What matters
        # is the #828 rule: never render a confident empty graph over a broken feed.
        failing_lineage_sources=failing_lineage_sources(session),
        warehouse_lineage_status=warehouse_lineage_status(session),
    )


def warehouse_lineage_status(session: Session) -> list[WarehouseLineageStatus]:
    """Warehouse-native lineage sources that are degraded or failing (#858 slice 4).

    Workspace-wide (like `failing_lineage_sources`): a warehouse's lineage tier is a
    property of the source, not the asset. Lists only Snowflake / Unity Catalog
    connections that have refreshed at least once AND are either degraded (a coarser
    tier — e.g. Snowflake OBJECT_DEPENDENCIES because the account isn't Enterprise) or
    errored (the last refresh failed). A healthy full-tier source is omitted — no banner
    over a clean, current graph.
    """
    rows = session.scalars(
        select(Connection).where(
            Connection.type.in_(("snowflake", "unity_catalog")),
            Connection.lineage_last_refresh_at.is_not(None),
            or_(
                Connection.lineage_degraded_reason.is_not(None),
                Connection.lineage_last_error.is_not(None),
            ),
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
        )
        for c in rows
    ]


def failing_lineage_sources(session: Session) -> list[LineageSourceHealth]:
    """Lineage-feeding connections whose poll is currently failing (#828).

    Workspace-wide, not per-asset, and deliberately so: lineage arrives from a *source*
    (a dbt project's manifest), not from the asset. If that source is down, every asset's
    lineage is suspect — including the ones that legitimately have none — so the caveat
    belongs on all of them.

    Scoped to `dbt` because it is the only orchestration provider that feeds lineage
    today (`read_manifest`, #759). Widening it means adding a provider, not rewriting
    this: the filter rides the existing capability, not a hardcoded list of names.
    """
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
    asset need not have suites to have metadata."""
    suites = list(session.scalars(select(Suite).where(Suite.asset_id == asset.id)))
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    outcomes = check_outcome_counts(session, run_ids)
    op_flags = operational_result_flags(session, run_ids)
    by_asset = _suite_outcomes(suites, latest_runs, outcomes, op_flags)
    return _roll_up(asset, by_asset.get(asset.id, []))


def _monitored_ids(session: Session, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of ``ids`` have ≥1 suite targeting them (globally — a structural fact,
    not a grant). One grouped query for the whole neighbourhood (no N+1)."""
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
    (two provenance rows for one asset pair are one drawn edge)."""
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
        # Defensive shape check: `columns` is app-written JSONB, but a malformed
        # value must degrade to "skipped", never 500 the asset page — LOUDLY (#907
        # review: a silent skip is the confident-empty-state failure mode #828
        # taught; an operator must be able to see the backfill missed rows).
        if not isinstance(cols, (list, tuple)):
            log.warning(
                "lineage_edge_columns_malformed",
                upstream_asset_id=str(up),
                downstream_asset_id=str(down),
                value_type=type(cols).__name__,
            )
            continue
        bucket = pairs.setdefault((up, down), set())
        bucket.update(
            (str(entry[0]), str(entry[1]))
            for entry in cols
            if isinstance(entry, (list, tuple)) and len(entry) == 2
        )
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
    structural fact."""
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
) -> Asset:
    """Set an asset's owner and/or description (workspace-Admin-only; gated at API).

    ``set_owner`` / ``set_description`` are the partial-update discriminators, so a
    field can be explicitly cleared to ``None`` versus left untouched (mirrors the
    suite PATCH's None-means-leave-alone problem, made explicit). Raises
    `AssetNotFoundError` (404) for an unknown id — no authz derivation here, since a
    workspace-admin sees every asset (ADR 0027) — and `AssetOwnerInvalidError` (422)
    when ``owner_user_id`` names no existing user (FK pre-check, never a raw 500)."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})
    if set_owner:
        if owner_user_id is not None and session.get(User, owner_user_id) is None:
            raise AssetOwnerInvalidError(
                "owner user does not exist", detail={"owner_user_id": str(owner_user_id)}
            )
        asset.owner_user_id = owner_user_id
    if set_description:
        asset.description = description
    session.commit()
    session.refresh(asset)
    log.info("asset_metadata_updated", asset_id=str(asset.id))
    return asset
