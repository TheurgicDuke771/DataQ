// Unit coverage for the a11y ratchet's identity logic (#1670, #1981 review). The behaviour
// under test crosses a real defect: keying a violation on its normalized CSS `selector`
// collapsed every React `useId()`-identified element on a route into one baseline row, so a
// genuinely NEW violation on a second such element passed the ratchet silently. `key()` (not
// exported) is exercised only indirectly, through `diffNew`/`toRecords` — that's the surface
// the specs actually call, and it's also the surface that hid the bug.
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  type AxeViolationLike,
  diffNew,
  loadBaseline,
  saveBaseline,
  toRecords,
} from '../../scripts/a11y/ratchet';

/** Two elements axe-core reports with different `useId()`-minted ids but distinct DOM
 * positions — the shape a real run produces for, say, two unlabelled antd `Select`s on the
 * same route. `ancestry` differs (real structural paths); `target` is normalized to the
 * same value by the app under test's own id-collapsing regex in older code, which is
 * exactly what made the two nodes indistinguishable under the old selector-based key. */
function twoDistinctUseIdNodes(): AxeViolationLike {
  return {
    id: 'label',
    impact: 'serious',
    help: 'Form elements must have labels',
    nodes: [
      { target: ['#_r_5_'], ancestry: ['html > body > div:nth-child(1) > div:nth-child(2)'] },
      { target: ['#_r_9_'], ancestry: ['html > body > div:nth-child(1) > div:nth-child(7)'] },
    ],
  };
}

describe('toRecords + diffNew key on ancestry, not the normalized selector', () => {
  it('two useId-identified nodes under the same rule produce two distinct keys', () => {
    const records = toRecords('route:/results', [twoDistinctUseIdNodes()]);
    expect(records).toHaveLength(2);

    // A run that already knows about the FIRST node only — the baseline before someone
    // adds a second unlabelled control to the route.
    const baseline = [records[0]];
    const newViolations = diffNew(records, baseline);

    // The second node must be reported as NEW. Under the old target-based key, both nodes'
    // ids collapsed to the same normalized selector and this returned zero.
    expect(newViolations).toHaveLength(1);
    expect(newViolations[0]?.ancestry).toBe(records[1]?.ancestry);
  });

  it('the same node reported twice (identical ancestry) is not a new violation', () => {
    const violation = twoDistinctUseIdNodes();
    const records = toRecords('route:/results', [violation]);
    const newViolations = diffNew(records, records);
    expect(newViolations).toHaveLength(0);
  });
});

describe('baseline round-trip has one row per unique key (no useId collapse)', () => {
  let dir: string;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
  });

  it('captures both useId-identified nodes as separate rows, and diffs cleanly after reload', () => {
    dir = mkdtempSync(join(tmpdir(), 'a11y-ratchet-test-'));
    const path = join(dir, 'baseline.json');

    const records = toRecords('route:/results', [twoDistinctUseIdNodes()]);
    saveBaseline(path, records);

    const baseline = loadBaseline(path);
    expect(baseline).toHaveLength(2);
    // The whole point of keying on ancestry: two rows, not one collapsed row.
    const uniqueKeys = new Set(baseline.map((r) => `${r.surface}\0${r.ruleId}\0${r.ancestry}`));
    expect(uniqueKeys.size).toBe(baseline.length);

    // Re-running the exact same scan against the just-saved baseline reports nothing new.
    const rescanned = diffNew(records, loadBaseline(path));
    expect(rescanned).toEqual([]);
  });

  it('saveBaseline never collapses rows that differ only in ancestry', () => {
    dir = mkdtempSync(join(tmpdir(), 'a11y-ratchet-test-'));
    const path = join(dir, 'baseline.json');
    const records = toRecords('route:/results', [twoDistinctUseIdNodes()]);
    saveBaseline(path, records);
    const reloaded = loadBaseline(path);
    expect(reloaded).toHaveLength(records.length);
  });
});
