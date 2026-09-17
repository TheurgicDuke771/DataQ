// Shared accessibility-violation ratchet — imported by both the Playwright a11y specs
// (frontend/e2e/a11y.spec.ts + frontend/e2e-otp/a11y.spec.ts, real axe-core results via
// @axe-core/playwright) and the Vitest component a11y test
// (frontend/tests/a11y/components.a11y.test.tsx, axe-core run directly against jsdom).
// Kept framework-free so neither test runner's globals leak here.
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

/** Records present in `current` but not in `baseline` — what the ratchet fails on. Not
 * pre-filtered to `current`'s surface by the caller: `key()` already includes `surface`, so
 * a baseline row for a different surface can never collide with one of `current`'s keys. */
export function diffNew(current: ViolationRecord[], baseline: Baseline): ViolationRecord[] {
  const known = new Set(baseline.map(key));
  return current.filter((r) => !known.has(key(r)));
}

export function formatViolations(records: ViolationRecord[]): string {
  return records
    .map((r) => `  [NEW] ${r.surface} — ${r.ruleId} (${r.impact}) at ${r.selector}\n    ${r.help}`)
    .join('\n');
}

export interface RatchetOptions {
  /** Path to the committed baseline JSON (frontend/a11y-baseline.json). */
  path: string;
  /** `A11Y_BASELINE=1` — regenerate this surface's baseline rows instead of gating. */
  capture: boolean;
}

export interface RatchetResult {
  /** Empty in capture mode (nothing to fail on during a deliberate regen) — otherwise the
   * violations present now but absent from the committed baseline. */
  newViolations: ViolationRecord[];
}

/** The ratchet-or-capture glue shared by all three a11y specs (#1670): in capture mode,
 * overwrite `surface`'s rows in the baseline; otherwise diff `records` against the
 * committed baseline. Each call does an immediate load-modify-save in capture mode, so
 * regenerating multiple surfaces from one process (the Vitest file, or a Playwright run
 * with `--workers=1`) is safe called once per surface, in any order. */
export function ratchet(
  surface: string,
  records: ViolationRecord[],
  { path, capture }: RatchetOptions,
): RatchetResult {
  if (capture) {
    const rest = loadBaseline(path).filter((r) => r.surface !== surface);
    saveBaseline(path, [...rest, ...records]);
    return { newViolations: [] };
  }
  return { newViolations: diffNew(records, loadBaseline(path)) };
}

/** The one failure-message builder for all three specs — tells the reader what broke and
 * how to respond, so a reviewer reading either Playwright's or Vitest's output gets the
 * same instructions either way. `undefined` (rather than a "no violations" string) when
 * there's nothing new, since `expect(x, undefined)` uses the default matcher message and
 * Vitest's `throw` path is skipped entirely in that case. */
export function newViolationsMessage(
  surface: string,
  newViolations: ViolationRecord[],
): string | undefined {
  if (newViolations.length === 0) return undefined;
  return (
    `${surface}: NEW serious/critical a11y violation(s) not in frontend/a11y-baseline.json. ` +
    'Fix them, or if this is deliberately deferred work, regenerate the baseline with ' +
    '`pnpm a11y:baseline` and explain why in the PR:\n' +
    formatViolations(newViolations)
  );
}
