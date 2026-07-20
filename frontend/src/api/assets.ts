import { api } from './client';

/**
 * Assets API — the read-only browse/reason surface over `assets` (ADR 0034,
 * #760). Assets are what users reason about; suites remain how checks execute.
 *
 * **The ADR 0037 three-layer rule** (backend `asset_view_service`): asset
 * identity + lineage topology (incl. column pairs) are visible to every member;
 * the aggregate rollup is workspace-true (over ALL composing suites — one
 * verdict for every viewer); only the composing-suite list is filtered to the
 * caller's ADR 0027 grants, the rest collapsing to `restricted_suite_count`.
 * The client never has to scope — it just renders what the API returns.
 */

/** A suite's latest run outcome — mirrors the backend `RunOutcomeRead`. */
export interface RunOutcome {
  run_id: string | null;
  /** Execution lifecycle (queued|running|succeeded|failed|cancelled), or null (never run). */
  status: string | null;
  /** Worst failing tier across evaluated checks, or null (all passed / no run). */
  worst_severity: 'warn' | 'fail' | 'critical' | null;
  checks_total: number;
  checks_passed: number;
  finished_at: string | null;
  created_at: string | null;
}

/** One suite composing an asset (caller-visible) — mirrors `ComposingSuiteRead`. */
export interface ComposingSuite {
  suite_id: string;
  name: string;
  my_permission: 'owner' | 'admin' | 'edit' | 'view';
  latest_run: RunOutcome;
}

/** List-row aggregation for one asset — mirrors `AssetSummaryRead`. Workspace-true
 *  (ADR 0037): identical for every viewer, aggregated over ALL composing suites. */
export interface AssetSummary {
  id: string;
  namespace: string;
  name: string;
  env: string | null;
  description: string | null;
  owner_user_id: string | null;
  last_seen: string;
  suite_count: number;
  /** Rolled up across ALL composing suites' latest runs (workspace-true). */
  worst_severity: 'warn' | 'fail' | 'critical' | null;
  checks_total: number;
  checks_passed: number;
  last_run_at: string | null;
  /** Latest-run execution states (distinct from check severity): any composing
   *  suite's latest run `failed` / still `queued`/`running` — an operationally
   *  failed run must never render as green health. */
  has_failed_run: boolean;
  has_active_run: boolean;
  /** Connection-health axis (#803) — could DataQ *execute* against the datasource?
   *  `has_operational_error`: a latest run `failed`, or any check `error`ed (the
   *  datasource threw). `has_skip`: a check's precondition wasn't met (e.g. the
   *  batch hasn't landed) — degraded, not down. Both are operational (#122): they
   *  never rank as severity, so they never colour *suite* (data-quality) health.
   *  Derived from the recorded runs — there is no connection-probe polling loop. */
  has_operational_error: boolean;
  has_skip: boolean;
  /** Any composing suite's latest run was `cancelled`. A cancelled run proves
   *  nothing — killed before a check ran, we may never have reached the datasource
   *  — so neither health axis may roll it up green. */
  has_cancelled_run: boolean;
}

/** A lineage neighbour — mirrors `LineageNodeRead`. Render-only (no run data).
 *  Fully named for every member (ADR 0037 — lineage topology is identity). */
export interface LineageNode {
  id: string;
  namespace: string;
  name: string;
  env: string | null;
  /** Whether the neighbour has ≥1 suite targeting it (a structural fact). */
  is_monitored: boolean;
  /** Hop distance from the asset under view (1 = a direct neighbour). Lets the
   *  graph lay nodes out in hop columns instead of flattening every hop (#805). */
  depth: number;
}

/** One edge of the lineage neighbourhood — mirrors `LineageEdgeRead`.
 *  `source` is the upstream asset id, `target` the downstream one.
 *
 *  `columns` is the edge's column-level refinement (#901) where a warehouse source
 *  recorded one — `[upstream_column, downstream_column]` pairs, shown to every
 *  member (ADR 0037). Null ⇒ a table-grain edge. */
export interface LineageEdge {
  source: string;
  target: string;
  columns?: [string, string][] | null;
}

/** Asset detail — mirrors `AssetDetailRead`. */
/** A lineage-feeding connection whose poll is currently failing (#828).
 *  Non-empty ⇒ the lineage below may be stale or empty for reasons that have nothing
 *  to do with this asset, so the UI must NOT render a clean "no lineage" empty state. */
export interface LineageSourceHealth {
  connection_id: string;
  name: string;
  type: string;
  consecutive_failures: number;
  /** A classified reason — never raw exception text. */
  last_error: string | null;
  last_polled_at: string | null;
}

/** One scorecard row (#889, ADR 0038). `score` is `null` when nothing evaluated —
 *  which is neither 0 nor 100. Render "no signal": "we ran nothing" and
 *  "everything failed" are opposite facts a 0 would conflate. */
export interface DimensionScore {
  dimension: string;
  checks_total: number;
  checks_passing: number;
  score: number | null;
}

/** Per-dimension coverage + score, workspace-true (ADR 0037) — identical for every
 *  viewer who can see the asset.
 *
 *  `uncovered` is the half users act on: dimensions with no checks at all.
 *  `unclassified_checks` counts checks with no dimension (custom SQL, or anything
 *  nobody classified); they are deliberately NOT bucketed, because filing them
 *  under a dimension they may not belong to would make `uncovered` a lie. */
export interface Scorecard {
  covered: DimensionScore[];
  uncovered: string[];
  unclassified_checks: number;
}

export interface AssetDetail {
  summary: AssetSummary;
  /** Absent from a pre-#889 API — the panel simply doesn't render. */
  scorecard?: Scorecard | null;
  /** Only the suites the viewer can see (ADR 0027). */
  suites: ComposingSuite[];
  /** How many MORE suites compose this asset outside the viewer's grants — they
   *  still roll into `summary` (workspace-true) but stay unnamed (ADR 0037).
   *  Optional because the UI does NOT read it (#924 review): the workspace-true
   *  `summary.suite_count` is the single owner of the total, and the restricted
   *  count is derived as `suite_count - suites.length` — so the card title and
   *  the footnote can never disagree, and a deploy-skew response from a pre-0037
   *  API (field absent) degrades gracefully instead of rendering NaN. */
  restricted_suite_count?: number;
  upstream: LineageNode[];
  downstream: LineageNode[];
  /** The real edges among the neighbourhood, so the graph draws truth, not a guess. */
  lineage_edges: LineageEdge[];
  failing_lineage_sources: LineageSourceHealth[];
  /** Warehouse-native lineage sources that are degraded (coarser tier) or failing, so
   *  the graph can be qualified rather than shown as complete + current (#858). */
  warehouse_lineage_status: WarehouseLineageStatus[];
}

/** A warehouse-native lineage source (Snowflake / UC) that is degraded or failing. */
export interface WarehouseLineageStatus {
  connection_id: string;
  name: string;
  type: string;
  /** The source that answered, e.g. `snowflake_object_dependencies`. */
  tier: string | null;
  /** The "working but coarse" note (view-level only, Enterprise needed). */
  degraded_reason: string | null;
  /** A classified refresh failure — never raw exception text. */
  last_error: string | null;
  last_refreshed_at: string | null;
}

/** Metadata mutation payload — mirrors `AssetMetadataUpdate` (admin-only). */
export interface AssetMetadataUpdate {
  owner_user_id?: string | null;
  description?: string | null;
}

export async function listAssets(params?: {
  limit?: number;
  offset?: number;
}): Promise<AssetSummary[]> {
  const { data } = await api.get<AssetSummary[]>('/assets', { params });
  return data;
}

export async function getAsset(assetId: string): Promise<AssetDetail> {
  const { data } = await api.get<AssetDetail>(`/assets/${assetId}`);
  return data;
}

/** Update an asset's owner/description (workspace-admin only; backend 403s others). */
export async function updateAsset(
  assetId: string,
  payload: AssetMetadataUpdate,
): Promise<AssetSummary> {
  const { data } = await api.patch<AssetSummary>(`/assets/${assetId}`, payload);
  return data;
}
