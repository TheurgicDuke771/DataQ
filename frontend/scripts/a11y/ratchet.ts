// Shared accessibility-violation ratchet — imported by both the Playwright a11y spec
// (frontend/e2e/a11y.spec.ts, real axe-core results via @axe-core/playwright) and the
// Vitest component a11y test (frontend/tests/a11y/components.a11y.test.tsx, axe-core run
// directly against jsdom). Kept framework-free so neither test runner's globals leak here.
//
// The ratchet (#1670): fail a run only on a violation NOT already in the committed
// baseline. This stops regression today without requiring every existing violation to be
// fixed first — that fix work is tracked separately (see the filed follow-up issue).
import { readFileSync, writeFileSync } from 'node:fs';

/** A minimal axe-core violation shape — just enough of the real `AxeResults['violations']`
 *  element to build a stable identity, independent of which axe binding produced it
 *  (`@axe-core/playwright`'s AxeResults and axe-core's own `run()` result share this shape).
 *  Callers must run axe with `{ ancestry: true }` so `nodes[].ancestry` is populated. */
export interface AxeViolationLike {
  id: string;
  impact?: string | null;
  help: string;
  nodes: Array<{ target: unknown[]; ancestry?: unknown[] }>;
}

/** One ratcheted violation, flattened to one row per affected DOM node (an axe violation
 * can list several nodes; each is a separately fixable instance). */
export interface ViolationRecord {
  /** `route:<path>` or `component:<name>` — which spec / render this came from. */
  surface: string;
  ruleId: string;
  impact: string;
  /** axe-core's structural `ancestry` path for the node (nth-child based, no ids) — this,
   * not `selector`, is the baseline identity. Two elements whose only difference is a
   * React `useId()`-minted id still have distinct ancestries, since ancestry walks DOM
   * position rather than reporting the node's own selector. Falls back to `target` if the
   * caller ran axe without `ancestry: true` (defensive; every caller here passes it). */
  ancestry: string;
  /** The raw CSS selector axe-core reports for the offending node — human-readable context
   * in failure output only, never part of the baseline identity (a `useId()` id here is
   * expected to vary from run to run without meaning a violation is new or gone). */
  selector: string;
  help: string;
}

export type Baseline = ViolationRecord[];

const MIN_IMPACT = new Set(['serious', 'critical']);

/** Only serious/critical violations count toward the floor (#1670) — moderate/minor are
 * real but not what this ratchet gates on. */
export function isGatedImpact(impact?: string | null): boolean {
  return !!impact && MIN_IMPACT.has(impact);
}

/** Flatten one surface's axe violations (already filtered by the caller to serious/critical
 * if desired — `filterGated` does that) into the flat per-node record shape the baseline
 * stores. */
export function toRecords(surface: string, violations: AxeViolationLike[]): ViolationRecord[] {
  const records: ViolationRecord[] = [];
  for (const violation of violations) {
    const impact = violation.impact ?? 'unknown';
    for (const node of violation.nodes) {
      const ancestrySource =
        node.ancestry && node.ancestry.length > 0 ? node.ancestry : node.target;
      records.push({
        surface,
        ruleId: violation.id,
        impact,
        ancestry: JSON.stringify(ancestrySource),
        selector: JSON.stringify(node.target),
        help: violation.help,
      });
    }
  }
  return records;
}

/** Keep only serious/critical violations — the ratchet's floor (#1670 item 1). */
export function filterGated(violations: AxeViolationLike[]): AxeViolationLike[] {
  return violations.filter((v) => isGatedImpact(v.impact));
}

function key(record: Pick<ViolationRecord, 'surface' | 'ruleId' | 'ancestry'>): string {
  return `${record.surface}\0${record.ruleId}\0${record.ancestry}`;
}

export function loadBaseline(path: string): Baseline {
  try {
    const raw = readFileSync(path, 'utf-8');
    const parsed = JSON.parse(raw) as { violations: Baseline };
    return parsed.violations ?? [];
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw err;
  }
}

export function saveBaseline(path: string, baseline: Baseline): void {
  const sorted = [...baseline].sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
  const payload = {
    // Informational only — never read back, so a regen never conflicts on this field.
    generatedAt: new Date().toISOString(),
    count: sorted.length,
    violations: sorted,
  };
  writeFileSync(path, JSON.stringify(payload, null, 2) + '\n', 'utf-8');
}

/** Records present in `current` but not in `baseline` — what the ratchet fails on. */
export function diffNew(current: ViolationRecord[], baseline: Baseline): ViolationRecord[] {
  const known = new Set(baseline.map(key));
  return current.filter((r) => !known.has(key(r)));
}

export function formatViolations(records: ViolationRecord[]): string {
  return records
    .map((r) => `  [NEW] ${r.surface} — ${r.ruleId} (${r.impact}) at ${r.selector}\n    ${r.help}`)
    .join('\n');
}
