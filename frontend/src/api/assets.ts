import { api } from './client';
import { toListPage, type ListPage } from './listPage';

/** Assets API — the read-only browse/reason surface over `assets` (ADR 0034, #760). */

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
  /** Left out of its connection's automatic coverage (ADR 0047); absent on older payloads. */
  auto_coverage_excluded?: boolean;
  suite_count: number;
  /** Rolled up across ALL composing suites' latest runs (workspace-true). */
  worst_severity: 'warn' | 'fail' | 'critical' | null;
  checks_total: number;
  checks_passed: number;
  last_run_at: string | null;
  /**
   * Latest-run execution states (distinct from check severity): any composing suite's latest run
   * `failed` / still `queued`/`running`.
   */
  has_failed_run: boolean;
  has_active_run: boolean;
  /** Connection-health axis (#803) — could DataQ *execute* against the datasource? */
  has_operational_error: boolean;
  has_skip: boolean;
  /** Any composing suite's latest run was `cancelled`. */
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

/**
 * Why an edge does or does not carry column pairs (#1710) — `recorded` is the only state that
 * supports a column-level claim; the rest say why `columns` is empty, never "no dependency".
 */
export type ColumnCoverage =
  'recorded' | 'none_recorded' | 'unavailable' | 'unknown' | 'not_captured';

/** One edge of the lineage neighbourhood — mirrors `LineageEdgeRead`. */
export interface LineageEdge {
  source: string;
  target: string;
  columns?: [string, string][] | null;
  /** Absent from a pre-#1710 API — read as `unknown`. */
  column_coverage?: ColumnCoverage;
}

export type TraceDirection = 'upstream' | 'downstream' | 'both';
export type TraceStatus = 'traced' | 'no_table_lineage' | 'none_recorded' | 'incomplete';

/** One column-grain trace — mirrors `ColumnTraceRead` (#1710). */
export interface ColumnTrace {
  asset_id: string;
  column: string;
  upstream: { asset_id: string; column: string; depth: number }[];
  downstream: { asset_id: string; column: string; depth: number }[];
  hops: {
    upstream_asset_id: string;
    upstream_column: string;
    downstream_asset_id: string;
    downstream_column: string;
  }[];
  gaps: { upstream_asset_id: string; downstream_asset_id: string; coverage: ColumnCoverage }[];
  origins: { asset_id: string; column: string; depth: number; confirmed: boolean }[];
  upstream_status: TraceStatus | null;
  downstream_status: TraceStatus | null;
  truncated: boolean;
  complete: boolean;
  assets: {
    id: string;
    namespace: string;
    name: string;
    env: string | null;
    is_monitored: boolean;
  }[];
  qualified_by: string[];
}

/** Asset detail — mirrors `AssetDetailRead`. */
/** A lineage-feeding connection whose poll is currently failing (#828). */
export interface LineageSourceHealth {
  connection_id: string;
  name: string;
  type: string;
  consecutive_failures: number;
  /** A classified reason — never raw exception text. */
  last_error: string | null;
  last_polled_at: string | null;
}

/** One scorecard row (#889, ADR 0038). */
export interface DimensionScore {
  dimension: string;
  /** Checks that EXIST in this dimension — coverage. A check authored today
   *  counts before it has ever run. */
  checks_total: number;
  /** Of those, how many passed in the latest run. */
  checks_passing: number;
  /** How many evaluated a severity — the score's denominator. Below
   *  `checks_total` when checks are unrun, skipped, or errored. */
  checks_evaluated: number;
  score: number | null;
}

/**
 * Per-dimension coverage + score, workspace-true (ADR 0037) — identical for every viewer who can
 * see the asset.
 */
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
  /**
   * How many MORE suites compose this asset outside the viewer's grants — they still roll into
   * `summary` (workspace-true) but stay unnamed (ADR 0037).
   */
  restricted_suite_count?: number;
  upstream: LineageNode[];
  downstream: LineageNode[];
  /** The real edges among the neighbourhood, so the graph draws truth, not a guess. */
  lineage_edges: LineageEdge[];
  failing_lineage_sources: LineageSourceHealth[];
  /** Warehouse-native lineage sources that are degraded (coarser tier) or failing, so
   *  the graph can be qualified rather than shown as complete + current (#858). */
  warehouse_lineage_status: WarehouseLineageStatus[];
  /**
   * Columns masked as sensitive only because recorded lineage traces them to a sensitive upstream
   * column (#2114). `null` = could not be determined; absent = a pre-#2114 API.
   */
  inherited_classifications?: InheritedClassification[] | null;
  /** The lineage walk hit its depth cap, so columns further upstream were not considered. */
  inherited_classifications_truncated?: boolean;
}

export interface InheritedClassification {
  column: string;
  sources: { asset_id: string; asset_name: string; column: string }[];
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
  /** #1091: the refresh loop silently stopped — last refresh is older than the
   *  staleness window, with no error and no degradation recorded. */
  stale: boolean;
  /** #1236: the last refresh left stale edges unpruned, so this source's graph can only grow —
   *  an edge shown may be a dependency that was REMOVED. Optional: an API predating #1236
   *  omits it, and `undefined` must read as "not reported", never as "pruning is fine". */
  prune_suspended?: boolean;
  /** When this source last pruned. `null` means NEVER — a different answer from "recently". */
  prune_suspended_since?: string | null;
}

/** Metadata mutation payload — mirrors `AssetMetadataUpdate` (admin-only). */
export interface AssetMetadataUpdate {
  owner_user_id?: string | null;
  description?: string | null;
  /** Leave this table out of its connection's automatic coverage (ADR 0047). */
  auto_coverage_excluded?: boolean;
}

/**
 * One page of `GET /assets` — the body (`items`) plus the workspace-wide `total` read off the
 * `X-Total-Count` header (#925).
 */
export type AssetListPage = ListPage<AssetSummary>;

export async function listAssets(
  params?: { limit?: number; offset?: number },
  // #1107: threaded through by the tree view's multi-page walk so an abort (unmount/toggle-away)
  // cancels the in-flight request too, not just the ones the walk loop hasn't issued yet.
  signal?: AbortSignal,
): Promise<AssetListPage> {
  const { data, headers } = await api.get<AssetSummary[]>('/assets', { params, signal });
  return toListPage(data, headers);
}

export async function getAsset(assetId: string): Promise<AssetDetail> {
  const { data } = await api.get<AssetDetail>(`/assets/${assetId}`);
  return data;
}

export async function traceColumn(
  assetId: string,
  column: string,
  direction: TraceDirection = 'both',
): Promise<ColumnTrace> {
  const { data } = await api.get<ColumnTrace>(`/assets/${assetId}/column-lineage`, {
    params: { column, direction },
  });
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
