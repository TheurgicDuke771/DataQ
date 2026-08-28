import { Tag, Tooltip } from 'antd';

import { DIMENSION_LABEL, DQ_DIMENSION_HELP, type DqDimension } from './expectationCatalog';

/**
 * At-a-glance check badges (#1551) — the suite check-list card and the run-results table both
 * showed `expectation_type` alone, so two `monitor:freshness` checks on the same suite (one GX,
 * one Snowflake DMF) rendered identically, and a check's DQ dimension / severity thresholds were
 * invisible without opening its edit form.
 */

interface EngineVisual {
  label: string;
  color: string;
}

/**
 * Engine visual (ADR 0036) — label + Tag color for each engine DataQ currently offers. The
 * backend's `CHECK_ENGINES` also reserves `dqx`/`dataplex` (trigger-gated, not yet offered by any
 * connection); an engine outside this map falls back to a neutral color and its raw name below,
 * rather than silently matching gx or dmf's color.
 */
const ENGINE_VISUAL: Record<string, EngineVisual> = {
  gx: { label: 'Great Expectations (gx)', color: 'blue' },
  dmf: { label: 'Snowflake DMF (native)', color: 'purple' },
};

/** Full engine label (ADR 0036) — `CheckHistoryDrawer`'s Descriptions panel and the check editor's
 *  engine Select both read this, so a label change or a newly-offered engine has one place to update. */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (SimpleList precedent)
export const ENGINE_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(ENGINE_VISUAL).map(([key, v]) => [key, v.label]),
);

/** Short engine text for a Tag or a plain-text table cell; the full name lives in the tooltip.
 *  A falsy engine (omitted, or an empty string) defaults to gx. */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (SimpleList precedent)
export function engineShortLabel(engine?: string): string {
  return (engine || 'gx').toUpperCase();
}

/**
 * Compact engine badge. A DMF failure skews warehouse/permission issues, a GX failure skews
 * batch-resolution issues — naming the evaluator at a glance is the point (#1551).
 */
export function EngineTag({ engine }: { engine?: string }) {
  const key = engine || 'gx';
  const visual = ENGINE_VISUAL[key] ?? { label: key, color: 'default' };
  return (
    <Tooltip title={visual.label}>
      <Tag color={visual.color}>{engineShortLabel(engine)}</Tag>
    </Tooltip>
  );
}

/**
 * DQ-dimension badge (ADR 0038). `dimension` is `null`/`undefined` for an unclassified check —
 * that is a coverage gap by design and must render as an explicit state, never be hidden or
 * silently bucketed into another dimension. A value outside the closed 7-dimension vocabulary
 * (a legacy row) renders its raw text, uncolored — not a colored tag with a silently-empty
 * tooltip that looks identical to a real classification.
 */
export function DimensionTag({ dimension }: { dimension?: string | null }) {
  if (!dimension) {
    return (
      <Tooltip title="No DQ dimension set — a coverage gap (ADR 0038), not an error.">
        <Tag>Unclassified</Tag>
      </Tooltip>
    );
  }
  const label = DIMENSION_LABEL[dimension as DqDimension];
  if (!label) {
    return <Tag>{dimension}</Tag>;
  }
  return (
    <Tooltip title={DQ_DIMENSION_HELP[dimension as DqDimension]}>
      <Tag color="geekblue">{label}</Tag>
    </Tooltip>
  );
}

/**
 * Compact severity-threshold summary, e.g. "warn 5 · fail 10" — omits unset tiers and returns
 * `null` for a plain pass/fail check (every tier unset), so the caller can skip rendering it.
 */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (SimpleList precedent)
export function formatThresholdsCompact(check: {
  warn_threshold: number | null;
  fail_threshold: number | null;
  critical_threshold: number | null;
}): string | null {
  const parts: string[] = [];
  if (check.warn_threshold !== null) parts.push(`warn ${check.warn_threshold}`);
  if (check.fail_threshold !== null) parts.push(`fail ${check.fail_threshold}`);
  if (check.critical_threshold !== null) parts.push(`critical ${check.critical_threshold}`);
  return parts.length > 0 ? parts.join(' · ') : null;
}
