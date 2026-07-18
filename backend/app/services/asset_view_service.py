"""Read-only asset view — the browse/reason surface over `assets` (ADR 0034, #760).

Assets are what users *reason about*; suites remain how checks *execute* (ADR 0034
guiding principle). This module aggregates, per asset, the suites that target it +
their latest run health + the lineage neighbourhood, for the `/assets` API.

**Authz is derived, never granted (ADR 0034 decision 5 / ADR 0027).** An asset is
visible iff the caller can `view` ≥1 suite mapped to it (`suites.asset_id`) **or the
asset has no suites at all**; the aggregation is filtered to *only* the suites the
caller's grants cover; a workspace-admin sees every suite (`include_all`). An asset
whose suites the caller can all not see is 404-no-leak (the API layer raises
`AssetNotFoundError`). This reuses `suite_service.accessible_suite_ids` verbatim, so
the visibility rule has a single source of truth and can never drift from the
suites/runs surfaces.

**The suite-less clause is an ADR 0034 amendment (#845/#846).** Redaction protects a
*grant*; an asset nobody has granted is protected by nothing — a suite-less asset (a
raw source table, an unmonitored mart, an asset whose last suite was deleted, its runs
cascading with it per #540) has no runs, results or samples behind it. Withholding only
its *name*, while the lineage graph reveals its existence anyway, bought nothing and
cost a great deal: it made browse disagree with the detail endpoint about what exists,
and it would have painted every raw upstream "🔒 Restricted" — which isn't restricted,
it's merely unmonitored.

Asset-metadata mutation (owner, description) is workspace-Admin-only — enforced at
the API layer (`require_workspace_admin`), not here; `update_asset_metadata` is the
plain persistence half.

**Lineage nodes ARE authz-filtered — but redacted, never dropped (#845).** The walk
itself is unscoped, because blast radius is the point (ADR 0034 §2) and a table's
consumers do not stop existing because you can't see them. A neighbour behind the grant
boundary is therefore returned as an **anonymous** node (id + depth only; no name,
namespace, env, or monitored flag) rather than named — a graph that named it would
defeat the no-leak 404 one click earlier, which is precisely what it used to do — and
rather than removed, since removing it would assert "nothing consumes this table".
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
    # ``None`` = a REDACTED row (#920): the asset exists, is monitored by suites the
    # caller holds no grant on, and browse includes it as an anonymous entry rather
    # than omitting it — the tree-level twin of the lineage graph's #845 rule
    # (omission asserts "this schema holds nothing else"). The namespace stays (the
    # tree needs the placement — a deliberate, user-directed disclosure); the NAME
    # is the protected fact, along with env/description/owner and every health
    # field (forced to their empty defaults — a hidden asset's health is itself a
    # fact about it). The detail endpoint keeps 404ing these no-leak.
    name: str | None
    env: str | None
    description: str | None
    owner_user_id: uuid.UUID | None
    # None on redacted rows (#920) — liveness cadence is a fact about the asset.
    last_seen: datetime | None
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
    is_accessible: bool = True
    # Redacted rows only (#920): the non-leaf path segments (db/schema or folder),
    # pre-split server-side so client and server can never disagree on the
    # separator rule. None on full rows and single-segment redacted names.
    name_prefix_segments: list[str] | None = None


@dataclass(frozen=True)
class LineageNode:
    """A lineage neighbour — enough to render, no run data (ADR 0034 §2).

    ``depth`` is the hop distance from the asset under view (1 = a direct
    neighbour), which is what lets the UI lay the graph out in columns (#805)
    instead of flattening every hop into one list.

    **A neighbour outside the caller's grants is REDACTED, not omitted (#845).** The
    lineage walk is not authz-scoped — it can't be, because the graph's job is blast
    radius, and a table's real consumers do not stop existing because you can't see
    them. But the asset endpoint 404s those assets *no-leak* (ADR 0034 decision 5), so
    handing their ``name``/``namespace``/``env`` to a non-grantee through the graph
    would defeat that guarantee one click earlier — and did.

    So an inaccessible neighbour keeps only ``id`` (an opaque UUID; the edges need it
    to draw the shape) and ``depth``: identity fields are ``None`` and ``is_monitored``
    is forced ``False``. The user still learns that *something* is downstream — which
    keeps the blast radius honest instead of asserting the confident falsehood
    "nothing consumes this table" (the #828/#823 lesson: never fix a leak by shipping a
    lie). What they don't learn is *what* it is.

    Redaction is done **here, server-side**: a name that is hidden in CSS has still
    crossed the wire.
    """

    id: uuid.UUID
    namespace: str | None
    name: str | None
    env: str | None
    is_monitored: bool
    depth: int = 1
    is_accessible: bool = True


@dataclass(frozen=True)
class LineageEdgeRef:
    """One edge of the neighbourhood DAG, as ``(upstream → downstream)`` asset ids.

    The UI draws exactly these; without them a graph could only *guess* which node
    at depth 2 hangs off which node at depth 1 (#805).

    ``columns`` is the edge's column-level refinement (#901) when a warehouse source
    recorded one — ``[upstream_column, downstream_column]`` pairs, unioned across the
    sources that observed the edge. **Redacted server-side by the #845 one-rule**: when
    either endpoint is outside the caller's grants, the pairs are withheld and only
    ``column_count`` survives — a column name is schema disclosure, the exact class the
    node redaction exists to prevent, and a name hidden in CSS has still crossed the
    wire. ``column_count`` is present whenever the edge has column data (it is the
    redacted box's honest label: *something* maps, in N column links).
    """

    source: uuid.UUID
    target: uuid.UUID
    columns: tuple[tuple[str, str], ...] | None = None
    column_count: int | None = None


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
    # Browse shows what actually exists — ALL of it (#846, extended by #920):
    #
    # - assets whose suites you can view → full rows;
    # - suite-less assets → full rows (#846 — redaction protects a grant, and an
    #   asset nobody granted is protected by nothing);
    # - assets monitored ONLY by suites you can't see → **REDACTED rows** (#920,
    #   user-directed): omitting them asserted "this schema holds nothing else" —
    #   the same falsehood the lineage graph's #845 rule exists to prevent, one
    #   surface over. The row keeps id + placement and nothing else; the detail
    #   endpoint keeps 404ing it.
    #
    # Accessibility is derived by `_accessible_asset_ids` — the SAME helper the
    # lineage graph redacts with (#911-review: a second SQL encoding of the rule
    # here would be the #845/#847 drift class reborn). Ordering note: redacted rows
    # sort by their true (hidden) name — a weak alphabetical-position signal,
    # accepted with the placement disclosure.
    assets = list(
        session.scalars(
            select(Asset).order_by(Asset.namespace, Asset.name).limit(limit).offset(offset)
        )
    )
    if not assets:
        return []
    page_ids = [a.id for a in assets]
    open_ids = _accessible_asset_ids(
        session,
        page_ids,
        user_id=user_id,
        include_all=include_all,
        has_suite=_monitored_ids(session, page_ids),
    )

    suites = list(
        session.scalars(
            select(Suite)
            .where(Suite.asset_id.in_(list(open_ids)), Suite.id.in_(accessible))
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
        (
            _roll_up(
                asset,
                _composing_suites(
                    suites_by_asset.get(asset.id, []), levels, latest_runs, outcomes, op_flags
                ),
            )
            if asset.id in open_ids
            else _redacted_summary(asset)
        )
        for asset in assets
    ]


def _redacted_summary(asset: Asset) -> AssetSummary:
    """A #920 redacted browse row: id + namespace + the PARENT path only — the leaf
    name is the protected fact, along with env/description/owner and every health
    axis (empty defaults — a hidden asset's health, monitoredness, and suite count
    are facts about it).

    ``name_prefix_segments`` (db/schema or folder path, leaf stripped, ALREADY
    segmented server-side with the same separator rule the tree uses) is a
    deliberate, user-directed disclosure: the tree places the 🔒 row inside its
    real group (`DATAQ_DB → ANALYTICS → Restricted`) — the same placement the
    lineage graph already reveals for any redacted node connected by an edge.
    Shipping segments, not a joined string, keeps one segmentation rule: a
    re-split client-side would re-detect the separator on the PREFIX (which can
    differ from the full name's — e.g. a dotted directory under a slashed path)
    and file the locked row in a fabricated group."""
    sep = "/" if "/" in asset.name else "."
    segments = [part for part in asset.name.split(sep) if part]
    return AssetSummary(
        id=asset.id,
        namespace=asset.namespace,
        name=None,
        name_prefix_segments=segments[:-1] or None,
        env=None,
        description=None,
        owner_user_id=None,
        # Liveness is a fact about the hidden asset too (watching it tick reveals
        # the pipeline's cadence) — withheld like everything else (#921 review).
        last_seen=None,
        suite_count=0,
        worst_severity=None,
        checks_total=0,
        checks_passed=0,
        last_run_at=None,
        is_accessible=False,
    )


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
    # No-leak: an asset whose suites the caller can ALL not see is indistinguishable from
    # one that doesn't exist. That is the grant boundary, and it stays closed.
    #
    # A **suite-less** asset is not behind that boundary (ADR 0034 amendment, #845/#846):
    # no suites means no grant to withhold and nothing behind it — no runs, no results, no
    # samples. It opens to an honest, empty page (identity + lineage, no health). Hiding it
    # only produced the dead link that surfaced #845: the lineage graph must draw it (else
    # the graph claims the table feeds nothing), and a node the graph draws must be a node
    # the endpoint opens.
    if asset is None:
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})
    if not suites and not include_all and _monitored_ids(session, [asset_id]):
        # It HAS suites — the caller simply can't view any of them. 404, no-leak.
        raise AssetNotFoundError("asset not found", detail={"asset_id": str(asset_id)})

    levels = effective_permissions(session, suites, user_id)
    latest_runs = _latest_run_per_suite(session, [s.id for s in suites])
    run_ids = [r.id for r in latest_runs.values()]
    outcomes = check_outcome_counts(session, run_ids)
    op_flags = operational_result_flags(session, run_ids)
    composing = _composing_suites(suites, levels, latest_runs, outcomes, op_flags)

    summary = _roll_up(asset, composing)
    graph = lineage_neighbourhood(session, asset_id)
    neighbour_ids = [a.id for a, _ in graph.upstream] + [a.id for a, _ in graph.downstream]
    # ONE lookup of "which of these assets has any suite" — it answers both questions
    # below (is it monitored? could a grant even exist for it?), which are the same fact.
    has_suite = _monitored_ids(session, neighbour_ids)
    # The lineage walk is not authz-scoped (blast radius must stay true), so the caller's
    # own visibility is applied HERE, at the boundary — a neighbour they hold no grant for
    # is redacted to an anonymous node rather than handed over (#845).
    accessible_assets = _accessible_asset_ids(
        session,
        neighbour_ids,
        user_id=user_id,
        include_all=include_all,
        has_suite=has_suite,
    )
    return AssetDetail(
        summary=summary,
        suites=composing,
        upstream=_lineage_nodes(graph.upstream, has_suite, accessible_assets),
        downstream=_lineage_nodes(graph.downstream, has_suite, accessible_assets),
        # The viewed asset is accessible by definition (this endpoint already authz'd
        # it); neighbours use the same accessibility set the node redaction derives
        # from, so the edge-level and node-level redaction can never disagree (#845).
        lineage_edges=_lineage_edge_refs(
            session, graph.edges, accessible={asset_id} | accessible_assets
        ),
        # Lineage-source health names ORCHESTRATION CONNECTIONS (name, type, classified
        # poll error) — infrastructure information, not asset information. It is shown to
        # a caller with a stake in this asset (≥1 suite on it) or an admin; a caller who
        # merely reached a suite-less asset through browse has no stake and is not handed
        # the workspace's connection inventory. Without this gate, the #846 visibility
        # widening would have quietly widened an infra disclosure too (#848 review).
        failing_lineage_sources=(
            failing_lineage_sources(session) if (suites or include_all) else []
        ),
        # Same stake gate as failing_lineage_sources — it names workspace connections
        # (infra), so only a caller with a suite on this asset (or an admin) sees it.
        warehouse_lineage_status=(
            warehouse_lineage_status(session) if (suites or include_all) else []
        ),
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


def _accessible_asset_ids(
    session: Session,
    ids: list[uuid.UUID],
    *,
    user_id: uuid.UUID,
    include_all: bool,
    has_suite: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Of ``ids``, the assets the caller may see — the one visibility rule (#845).

    An asset is visible when **either**:

    - the caller can view ≥1 suite targeting it (ADR 0034 decision 5 — authz derived from
      the suite ladder, never granted separately); **or**
    - it has **no suites at all** (ADR 0034 amendment, #845/#846).

    The second clause is the point, and it is not a loophole: redaction exists to protect
    a *grant*, and an asset nobody has granted is protected by nothing. A suite-less asset
    (a raw source table, a dbt mart nobody monitors yet, an asset whose last suite was
    deleted — its runs/results cascade with it, #540) has no runs, no results and no
    samples behind it. The only thing withheld was its **name** — which the lineage graph
    reveals the existence of anyway.

    Keeping those hidden forced a worse lie than the one #845 fixes: every raw upstream
    would render "🔒 Restricted" to a non-admin, when it isn't restricted at all — it's
    merely unmonitored. Lineage would be a wall of locked boxes.

    So the only thing we withhold is an asset that someone *else* monitors and you may
    not — exactly the grant boundary the no-leak 404 defends.

    A workspace-admin (``include_all``) sees everything (ADR 0027).

    ``has_suite`` is the caller-supplied "assets that ANY suite targets" set — the only
    assets a grant could exist for. It is passed in rather than re-queried because the
    caller has already computed it (it is the same set the ``is_monitored`` flag reads),
    and querying it twice made the two derivations independent when they are in fact the
    same fact about the same rows.
    """
    if not ids:
        return set()
    if include_all:
        return set(ids)
    granted = {
        asset_id
        for (asset_id,) in session.execute(
            select(Suite.asset_id)
            .where(Suite.asset_id.in_(ids), Suite.id.in_(accessible_suite_ids(user_id)))
            .group_by(Suite.asset_id)
        )
    }
    # The granted ones, plus the suite-less ones: an id absent from `has_suite` is
    # targeted by no suite at all, so nothing is being kept from anyone.
    return granted | (set(ids) - has_suite)


def _lineage_edge_refs(
    session: Session,
    edges: list[tuple[uuid.UUID, uuid.UUID]],
    *,
    accessible: set[uuid.UUID],
) -> list[LineageEdgeRef]:
    """The neighbourhood's edges with their column-level refinement (#901), redacted
    by the #845 one-rule: an edge touching a redacted node yields ``column_count``
    only — the pairs (column NAMES of an asset the caller can't see) never leave the
    server. Column data is unioned across the sources that observed the edge (two
    provenance rows for one asset pair are one drawn edge)."""
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
    refs: list[LineageEdgeRef] = []
    for up, down in edges:
        cols_set = pairs.get((up, down))
        if not cols_set:
            refs.append(LineageEdgeRef(source=up, target=down))
            continue
        both_visible = up in accessible and down in accessible
        refs.append(
            LineageEdgeRef(
                source=up,
                target=down,
                columns=tuple(sorted(cols_set)) if both_visible else None,
                column_count=len(cols_set),
            )
        )
    return refs


def _lineage_nodes(
    assets: list[tuple[Asset, int]],
    monitored: set[uuid.UUID],
    accessible: set[uuid.UUID],
) -> list[LineageNode]:
    """Map reachable lineage assets (+ their hop depth) to render-only nodes,
    **redacting the ones outside the caller's grants** (#845 — see `LineageNode`).

    A redacted node keeps its id (the edges reference it) and its depth, and nothing
    else: no name, no namespace, no env, and ``is_monitored`` forced ``False`` rather
    than reported — whether someone else monitors an asset you can't see is itself a
    fact about that asset.
    """
    return [
        LineageNode(
            id=a.id,
            namespace=a.namespace if a.id in accessible else None,
            name=a.name if a.id in accessible else None,
            env=a.env if a.id in accessible else None,
            is_monitored=a.id in monitored and a.id in accessible,
            depth=depth,
            is_accessible=a.id in accessible,
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
