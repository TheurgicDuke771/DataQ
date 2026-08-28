import { Tag, Tooltip } from 'antd';

import { DQ_DIMENSION_HELP, type DqDimension } from './expectationCatalog';

/**
 * At-a-glance check badges (#1551) — the suite check-list card and the run-results table both
 * showed `expectation_type` alone, so two `monitor:freshness` checks on the same suite (one GX,
 * one Snowflake DMF) rendered identically, and a check's DQ dimension / severity thresholds were
 * invisible without opening its edit form.
 */

/** Full engine label (ADR 0036) — the single source `CheckHistoryDrawer`'s Descriptions panel and
 *  the compact badges below both read, so the two surfaces can't drift apart. */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (SimpleList precedent)
export const ENGINE_LABEL: Record<string, string> = {
  gx: 'Great Expectations (gx)',
  dmf: 'Snowflake DMF (native)',
};

/** Short engine text for a Tag or a plain-text table cell; the full name lives in the tooltip. */
// eslint-disable-next-line react-refresh/only-export-components -- helper + its badge belong together (SimpleList precedent)
export function engineShortLabel(engine?: string): string {
  return (engine ?? 'gx').toUpperCase();
}

/**
 * Compact engine badge. A DMF failure skews warehouse/permission issues, a GX failure skews
 * batch-resolution issues — naming the evaluator at a glance is the point (#1551).
 */
export function EngineTag({ engine }: { engine?: string }) {
  const full = ENGINE_LABEL[engine ?? 'gx'] ?? engine ?? 'gx';
  return (
    <Tooltip title={full}>
      <Tag color={engine === 'dmf' ? 'purple' : 'blue'}>{engineShortLabel(engine)}</Tag>
    </Tooltip>
  );
}

function titleCase(s: string): string {
  return `${s.charAt(0).toUpperCase()}${s.slice(1)}`;
}

/**
 * DQ-dimension badge (ADR 0038). `dimension` is `null`/`undefined` for an unclassified check —
 * that is a coverage gap by design and must render as an explicit state, never be hidden or
 * silently bucketed into another dimension.
 */
export function DimensionTag({ dimension }: { dimension?: string | null }) {
  if (!dimension) {
    return (
      <Tooltip title="No DQ dimension set — a coverage gap (ADR 0038), not an error.">
        <Tag>Unclassified</Tag>
      </Tooltip>
    );
  }
  return (
    <Tooltip title={DQ_DIMENSION_HELP[dimension as DqDimension]}>
      <Tag color="geekblue">{titleCase(dimension)}</Tag>
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
