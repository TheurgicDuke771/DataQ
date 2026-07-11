import { api } from './client';

/**
 * Assets API — the read-only browse/reason surface over `assets` (ADR 0034,
 * #760). Assets are what users reason about; suites remain how checks execute.
 *
 * **Authz is derived, never granted** (backend `asset_view_service`): an asset is
 * visible iff the caller can view ≥1 suite targeting it, the aggregation is
 * filtered to their grants, and an asset outside their grants 404s (no-leak). The
 * client never has to scope — it just renders what the API returns.
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

/** List-row aggregation for one visible asset — mirrors `AssetSummaryRead`. */
export interface AssetSummary {
  id: string;
  namespace: string;
  name: string;
  env: string | null;
  description: string | null;
  owner_user_id: string | null;
  last_seen: string;
  suite_count: number;
  /** Rolled up across the caller-visible composing suites' latest runs. */
  worst_severity: 'warn' | 'fail' | 'critical' | null;
  checks_total: number;
  checks_passed: number;
  last_run_at: string | null;
}

/** A lineage neighbour — mirrors `LineageNodeRead`. Render-only (no run data). */
export interface LineageNode {
  id: string;
  namespace: string;
  name: string;
  env: string | null;
  /** Whether the neighbour has ≥1 suite targeting it (a structural fact). */
  is_monitored: boolean;
}

/** Asset detail — mirrors `AssetDetailRead`. */
export interface AssetDetail {
  summary: AssetSummary;
  suites: ComposingSuite[];
  upstream: LineageNode[];
  downstream: LineageNode[];
}

/** Metadata mutation payload — mirrors `AssetMetadataUpdate` (admin-only). */
export interface AssetMetadataUpdate {
  owner_user_id?: string | null;
  description?: string | null;
}

export async function listAssets(): Promise<AssetSummary[]> {
  const { data } = await api.get<AssetSummary[]>('/assets');
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
