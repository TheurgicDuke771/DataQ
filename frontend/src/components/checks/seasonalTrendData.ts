import type { CheckResultPoint, SeasonalPoint } from '../../api/suites';

/** Why a run was scored without the yearly comparison (backend `seasonal.py` reasons). */
export const YEARLY_REASON_LABEL: Record<string, string> = {
  no_earlier_year: 'no earlier year in the history',
  zero_level_in_earlier_year: 'the level was zero in the earlier year',
  negative_value: 'a negative value',
  insufficient_history: 'too little history',
  earlier_year_in_declared_period: 'the earlier year fell inside a declared period',
};

export function yearlyReasonLabel(reason: string | null | undefined): string {
  return (reason && YEARLY_REASON_LABEL[reason]) ?? reason ?? 'no reason recorded';
}

/** The ISO day (`YYYY-MM-DD`) of a stored point's timestamp. */
export function isoDay(ts: string): string {
  return ts.slice(0, 10);
}

/** The latest result for each judged period, keyed by the period's first day. */
export function resultsByPeriod(points: CheckResultPoint[]): Map<string, SeasonalPoint> {
  const out = new Map<string, SeasonalPoint>();
  for (const p of points) {
    const start = p.seasonal?.period_start;
    if (start) out.set(start, p.seasonal as SeasonalPoint);
  }
  return out;
}

/** How the judged periods shown were scored: how many had the yearly comparison, and why the
 *  rest did not, most common first. */
export function yearlySummary(judged: SeasonalPoint[]): {
  applied: number;
  notApplied: { reason: string; count: number }[];
} {
  const reasons = new Map<string, number>();
  let applied = 0;
  for (const s of judged) {
    if (s.yearly_applied === true) applied += 1;
    else if (s.yearly_applied === false) {
      const label = yearlyReasonLabel(s.yearly_reason);
      reasons.set(label, (reasons.get(label) ?? 0) + 1);
    }
  }
  const notApplied = [...reasons.entries()]
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count);
  return { applied, notApplied };
}

/** `[start, end]` snapped to the first and last plotted days inside it, or null if none is. */
export function snapToDays(
  days: string[],
  range: { start: string; end: string },
): { x1: string; x2: string } | null {
  const inside = days.filter((d) => d >= range.start && d <= range.end);
  return inside.length > 0 ? { x1: inside[0], x2: inside[inside.length - 1] } : null;
}
