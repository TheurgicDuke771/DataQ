import type { SuiteDeletionImpact } from '../../api/suites';

/** Fallback shown when the impact fetch fails — the delete itself is never
 *  blocked on it (#1320). */
export const DELETION_IMPACT_UNAVAILABLE =
  'Counts unavailable — this removes the suite and everything in it. This cannot be undone.';

function plural(n: number, noun: string): string {
  return `${n.toLocaleString()} ${noun}${n === 1 ? '' : 's'}`;
}

/**
 * States the exact blast radius of deleting a suite (#1320) — e.g. "Deletes 12
 * checks, 431 runs and 5,180 results. This cannot be undone." A zero-everywhere
 * (never-run) suite renders the same sentence with zeros, not a special warning.
 */
export function describeDeletionImpact(impact: SuiteDeletionImpact): string {
  const core =
    `Deletes ${plural(impact.checks, 'check')}, ${plural(impact.runs, 'run')} ` +
    `and ${plural(impact.results, 'result')}.`;

  const bindings = impact.trigger_bindings;
  const schedules = impact.schedules;
  const linked: string[] = [];
  if (bindings > 0) linked.push(plural(bindings, 'trigger binding'));
  if (schedules > 0) linked.push(plural(schedules, 'schedule'));

  if (linked.length === 0) return `${core} This cannot be undone.`;
  // A compound subject ("X and Y") always takes a plural verb; a lone subject
  // agrees with its own count (1 schedule *points*, 2 schedules *point*).
  const soleCount = linked.length === 1 ? bindings || schedules : null;
  const verb = soleCount === 1 ? 'points' : 'point';
  return `${core} ${linked.join(' and ')} ${verb} at this suite and will be removed. This cannot be undone.`;
}
