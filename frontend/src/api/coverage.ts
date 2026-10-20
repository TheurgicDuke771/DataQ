import { api } from './client';

/** The automatic baselines that carry deviation thresholds (ADR 0047). */
export type CoverageBaseline = 'volume' | 'freshness' | 'profile';
/** Where a group of settings came from: this table's override, the workspace setting, or the built-in default. */
export type CoverageLevel = 'asset' | 'workspace' | 'default';

export interface Sensitivity {
  warn: number;
  fail: number;
  critical: number;
}

export interface BaselineShape {
  window: number;
  min_points: number;
  seasonality: boolean;
  /** Also compare with the same time in earlier years (row-count and freshness baselines
   *  only). Off when a stored group leaves it out. */
  yearly?: boolean;
}

/** What one level stores. A group it omits is left to the level below; a group is set whole. */
export interface CoverageDocument {
  sensitivity?: Partial<Record<CoverageBaseline, Sensitivity>>;
  baseline?: BaselineShape;
}

export interface EffectiveCoverage {
  sensitivity: Record<CoverageBaseline, Sensitivity & { source: CoverageLevel }>;
  baseline: BaselineShape & { yearly: boolean; source: CoverageLevel };
}

/** `GET /admin/coverage`. `effective` is what a table with no override of its own resolves to. */
export interface CoverageSettings {
  setting: CoverageDocument;
  effective: EffectiveCoverage;
  defaults: EffectiveCoverage;
  is_default: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

/** `GET /assets/{id}/coverage-settings`. `override` is null when the table sets nothing itself. */
export interface AssetCoverageSettings {
  override: CoverageDocument | null;
  effective: EffectiveCoverage;
}

export async function getCoverageSettings(): Promise<CoverageSettings> {
  const { data } = await api.get<CoverageSettings>('/admin/coverage');
  return data;
}

export async function putCoverageSettings(body: CoverageDocument): Promise<CoverageSettings> {
  const { data } = await api.put<CoverageSettings>('/admin/coverage', body);
  return data;
}

export async function resetCoverageSettings(): Promise<CoverageSettings> {
  const { data } = await api.delete<CoverageSettings>('/admin/coverage');
  return data;
}

export async function getAssetCoverageSettings(assetId: string): Promise<AssetCoverageSettings> {
  const { data } = await api.get<AssetCoverageSettings>(`/assets/${assetId}/coverage-settings`);
  return data;
}
