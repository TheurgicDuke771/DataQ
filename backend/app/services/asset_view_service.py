"""Read-only asset view — the browse/reason surface over `assets` (ADR 0034, #760).

Assets are what users *reason about*; suites remain how checks *execute* (ADR 0034
guiding principle). This module aggregates, per asset, the suites that target it +
their latest run health + the lineage neighbourhood, for the `/assets` API.

**Authz is derived, never granted (ADR 0034 decision 5 / ADR 0027).** An asset is
visible iff the caller can `view` ≥1 suite mapped to it (`suites.asset_id`); the
aggregation is filtered to *only* the suites the caller's grants cover; a
workspace-admin sees every suite (`include_all`). An asset wholly outside the
caller's grants is 404-no-leak (the API layer raises `AssetNotFoundError`). This
reuses `suite_service.accessible_suite_ids` verbatim, so the visibility rule has a
single source of truth and can never drift from the suites/runs surfaces.

Asset-metadata mutation (owner, description) is workspace-Admin-only — enforced at
the API layer (`require_workspace_admin`), not here; `update_asset_metadata` is the
plain persistence half.

Lineage nodes are **not** authz-filtered: blast radius is the point (ADR 0034 §2),
and a node exposes only its OpenLineage `(namespace, name)` + whether it is
monitored — never run data or grants — so nothing sensitive leaks through it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Connection, Run, Suite, User, worst_severity
from backend.app.lineage.edges import lineage_neighbourhood
from backend.app.services.run_service import check_outcome_counts, operational_result_flags
from backend.app.services.suite_authz import effective_permissions
from backend.app.services.suite_service import accessible_suite_ids

log = get_logger(__name__)


class AssetNotFoundError(DataQError):
    """Raised when an asset does not exist *or* is wholly outside the caller's
    grants — the two are indistinguishable by design (404-no-leak, ADR 0027)."""

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
    """List-row aggregation for one visible asset.

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
    """

    source: uuid.UUID
    target: uuid.UUID


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
class AssetDetail:
    """Asset detail: the summary aggregation + per-suite breakdown + lineage."""

    summary: AssetSummary
    suites: list[ComposingSuite]
    upstream: list[LineageNode] = field(default_factory=list)
    downstream: list[LineageNode] = field(default_factory=list)
    lineage_edges: list[LineageEdgeRef] = field(default_factory=list)
    # Non-empty ⇒ a lineage source is broken, so the graph below may be stale or empty
    # for a reason that has nothing to do with this asset. Never show a clean empty
    # state over a broken integration.
    failing_lineage_sources: list[LineageSourceHealth] = field(default_factory=list)


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


def _roll_up(asset: Asset, composing: list[ComposingSuite]) -> AssetSummary:
    """Roll a set of composing suites up into the asset-level health summary."""
    statuses: list[str] = []
    checks_total = checks_passed = 0
    last_run_at: datetime | None = None
    has_failed_run = has_active_run = has_cancelled_run = False
    has_operational_error = has_skip = False
    for suite in composing:
        run = suite.latest_run
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
        suite_count=len(composing),
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
    user_id: uuid.UUID,
    include_all: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[AssetSummary]:
    """Assets the caller can see (via ≥1 accessible composing suite), sorted by
    ``(namespace, name)`` and paginated with ``limit``/``offset``.

    Pagination is applied at the SQL level over the *asset* page (not the suite
    rows), so a page is a stable, deterministic slice regardless of how many
    suites compose each asset. Aggregation is filtered to the caller's grants: a
    partial-grant caller sees the asset but only the suites/runs they can view
    roll up into its health."""
    accessible = accessible_suite_ids(user_id, include_all=include_all)
    visible_asset_ids = select(Suite.asset_id).where(
        Suite.asset_id.is_not(None), Suite.id.in_(accessible)
    )
    assets = list(
        session.scalars(
            select(Asset)
            .where(Asset.id.in_(visible_asset_ids))
            .order_by(Asset.namespace, Asset.name)
            .limit(limit)
            .offset(offset)
        )
    )
    if not assets:
        return []

    suites = list(
        session.scalars(
            select(Suite)
            .where(Suite.asset_id.in_([a.id for a in assets]), Suite.id.in_(accessible))
            .order_by(Suite.name)
        )
    )
    levels = effective_permissions(session, suites, user_id)
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    outcomes = check_outcome_counts(session, run_ids)
    op_flags = operational_result_flags(session, run_ids)

    suites_by_asset: dict[uuid.UUID, list[Suite]] = defaultdict(list)
    for suite in suites:
        assert suite.asset_id is not None  # filtered in the query
        suites_by_asset[suite.asset_id].append(suite)

    return [
        _roll_up(
            asset,
            _composing_suites(
                suites_by_asset.get(asset.id, []), levels, latest_runs, outcomes, op_flags
            ),
        )
        for asset in assets
    ]


def get_visible_asset(
    session: Session, asset_id: uuid.UUID, *, user_id: uuid.UUID, include_all: bool = False
) -> AssetDetail:
    """One asset's detail (aggregation + per-suite breakdown + lineage).

    Raises `AssetNotFoundError` (404) if the asset does not exist or the caller can
    view no suite targeting it — the two are indistinguishable (no-leak). A
    workspace-admin (``include_all``) sees every asset, **including suite-less
    orphans** (e.g. a lineage-only node, or an asset whose last composing suite was
    deleted) — for them only a truly unknown id 404s."""
    asset = session.get(Asset, asset_id)
    accessible = accessible_suite_ids(user_id, include_all=include_all)
    suites = list(
        session.scalars(
            select(Suite)
            .where(Suite.asset_id == asset_id, Suite.id.in_(accessible))
            .order_by(Suite.name)
        )
    )
    # No-leak: for a non-admin, an unknown id and an id they can see no suite for
    # both 404. An admin's visibility is workspace-wide (ADR 0027), so a suite-less
    # asset is still returned (with an empty suites list) rather than hidden.
    if asset is None or (not suites and not include_all):
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})

    levels = effective_permissions(session, suites, user_id)
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    outcomes = check_outcome_counts(session, run_ids)
    op_flags = operational_result_flags(session, run_ids)
    composing = _composing_suites(suites, levels, latest_runs, outcomes, op_flags)

    summary = _roll_up(asset, composing)
    graph = lineage_neighbourhood(session, asset_id)
    monitored = _monitored_ids(
        session, [a.id for a, _ in graph.upstream] + [a.id for a, _ in graph.downstream]
    )
    return AssetDetail(
        summary=summary,
        suites=composing,
        upstream=_lineage_nodes(graph.upstream, monitored),
        downstream=_lineage_nodes(graph.downstream, monitored),
        lineage_edges=[LineageEdgeRef(source=u, target=d) for u, d in graph.edges],
        failing_lineage_sources=failing_lineage_sources(session),
    )


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


def summarize_asset(
    session: Session, asset: Asset, *, user_id: uuid.UUID, include_all: bool = False
) -> AssetSummary:
    """Roll one already-loaded asset up into its list-row summary.

    Unlike `get_visible_asset`, this never 404s on "no composing suites" — an asset
    with zero suites rolls up to an empty (no-run) health summary. Used by the
    admin PATCH response, where the asset need not have suites to have metadata."""
    accessible = accessible_suite_ids(user_id, include_all=include_all)
    suites = list(
        session.scalars(
            select(Suite)
            .where(Suite.asset_id == asset.id, Suite.id.in_(accessible))
            .order_by(Suite.name)
        )
    )
    levels = effective_permissions(session, suites, user_id) if suites else {}
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    outcomes = check_outcome_counts(session, run_ids)
    op_flags = operational_result_flags(session, run_ids)
    composing = _composing_suites(suites, levels, latest_runs, outcomes, op_flags)
    return _roll_up(asset, composing)


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


def _lineage_nodes(assets: list[tuple[Asset, int]], monitored: set[uuid.UUID]) -> list[LineageNode]:
    """Map reachable lineage assets (+ their hop depth) to render-only nodes."""
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
