import type {
  BaselineShape,
  CoverageBaseline,
  CoverageDocument,
  CoverageLevel,
  EffectiveCoverage,
  Sensitivity,
} from '../../api/coverage';

export const BASELINES: { key: CoverageBaseline; label: string }[] = [
  { key: 'volume', label: 'Row count' },
  { key: 'freshness', label: 'Freshness' },
  { key: 'profile', label: 'Column profile' },
];

export const TIERS: { key: keyof Sensitivity; label: string }[] = [
  { key: 'warn', label: 'Warn' },
  { key: 'fail', label: 'Fail' },
  { key: 'critical', label: 'Critical' },
];

export const LEVEL_LABEL: Record<CoverageLevel, string> = {
  asset: 'This table',
  workspace: 'Workspace setting',
  default: 'Built-in default',
};

/** The server's bounds (`coverage_settings_service` and the anomaly kind's window limits),
 *  in one place for the inputs and the reasons below. A threshold must be above 0. */
export const MAX_DEVIATIONS = 99;
export const MIN_WINDOW = 3;
export const MAX_WINDOW = 90;

type Nullable<T> = { [K in keyof T]: T[K] | null };

/** What the form holds: each group is either set here (a value) or left to the level below (null). */
export interface CoverageDraft {
  sensitivity: Record<CoverageBaseline, Nullable<Sensitivity> | null>;
  baseline:
    | (Nullable<Omit<BaselineShape, 'seasonality' | 'yearly'>> & {
        seasonality: boolean;
        yearly: boolean;
      })
    | null;
}

export function draftOf(doc: CoverageDocument | null): CoverageDraft {
  const sensitivity = doc?.sensitivity ?? {};
  return {
    sensitivity: {
      volume: sensitivity.volume ? { ...sensitivity.volume } : null,
      freshness: sensitivity.freshness ? { ...sensitivity.freshness } : null,
      profile: sensitivity.profile ? { ...sensitivity.profile } : null,
    },
    baseline: doc?.baseline ? { ...doc.baseline, yearly: doc.baseline.yearly ?? false } : null,
  };
}

/** The document a draft saves as; call only when `invalidReason` is null. */
export function documentOf(draft: CoverageDraft): CoverageDocument {
  const sensitivity: Partial<Record<CoverageBaseline, Sensitivity>> = {};
  for (const { key } of BASELINES) {
    const group = draft.sensitivity[key];
    if (group) sensitivity[key] = group as Sensitivity;
  }
  const doc: CoverageDocument = {};
  if (Object.keys(sensitivity).length > 0) doc.sensitivity = sensitivity;
  if (draft.baseline) {
    // Stored only when on, as the server stores it.
    const { yearly, ...shape } = draft.baseline;
    doc.baseline = (yearly ? { ...shape, yearly } : shape) as BaselineShape;
  }
  return doc;
}

export function isEmpty(draft: CoverageDraft): boolean {
  return Object.keys(documentOf(draft)).length === 0;
}

/** Mirrors the server rules, so the reason shows before a round-trip. */
export function invalidReason(draft: CoverageDraft): string | null {
  for (const { key, label } of BASELINES) {
    const group = draft.sensitivity[key];
    if (!group) continue;
    const { warn, fail, critical } = group;
    if (warn === null || fail === null || critical === null) {
      return `${label}: every threshold needs a value.`;
    }
    if (warn <= 0 || critical > MAX_DEVIATIONS) {
      return `${label}: thresholds must be above 0 and at most ${MAX_DEVIATIONS}.`;
    }
    if (!(warn <= fail && fail <= critical)) {
      return `${label}: keep the order warn ≤ fail ≤ critical.`;
    }
  }
  const shape = draft.baseline;
  if (shape) {
    if (shape.window === null || shape.min_points === null) {
      return 'History: the window and the minimum both need a value.';
    }
    if (shape.window < MIN_WINDOW || shape.window > MAX_WINDOW) {
      return `History: the window must be ${MIN_WINDOW} to ${MAX_WINDOW} runs.`;
    }
    if (shape.min_points < MIN_WINDOW || shape.min_points > shape.window) {
      return `History: the minimum must be at least ${MIN_WINDOW} and no more than the window.`;
    }
  }
  return null;
}

/** The group a baseline starts from when it is switched to "set here": the values in effect. */
export function sensitivityFrom(effective: EffectiveCoverage, key: CoverageBaseline): Sensitivity {
  const { warn, fail, critical } = effective.sensitivity[key];
  return { warn, fail, critical };
}

export function shapeFrom(effective: EffectiveCoverage): BaselineShape & { yearly: boolean } {
  const { window, min_points, seasonality, yearly } = effective.baseline;
  return { window, min_points, seasonality, yearly };
}

/**
 * True when saving `draft` would shorten the window automatic checks run on: older history is
 * trimmed on their next run and has to build up again if the window is lengthened later. A
 * level that stops setting the window falls to the level below, which can be shorter too.
 */
export function shortensWindow(
  draft: CoverageDraft,
  below: EffectiveCoverage,
  current: EffectiveCoverage,
): boolean {
  const next = draft.baseline ? draft.baseline.window : below.baseline.window;
  return typeof next === 'number' && next < current.baseline.window;
}
