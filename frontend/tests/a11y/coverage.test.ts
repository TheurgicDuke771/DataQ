import { describe, expect, it } from 'vitest';

import {
  type AxeCoverageLike,
  evaluatedNodes,
  scanUntilStable,
  stillLoading,
} from '../../scripts/a11y/coverage';

// The under-reporting guard (#2051): a scan of a half-loaded page evaluates fewer nodes than a
// scan of the loaded one, and the ratchet alone cannot see that.

function result(nodes: number): AxeCoverageLike {
  return { passes: [{ nodes: Array(nodes).fill(null) }], violations: [], incomplete: [] };
}

describe('evaluatedNodes', () => {
  it('counts the nodes judged by every outcome', () => {
    expect(
      evaluatedNodes({
        passes: [{ nodes: [1, 2] }, { nodes: [3] }],
        violations: [{ nodes: [4] }],
        incomplete: [{ nodes: [5, 6] }],
      }),
    ).toBe(6);
  });
});

describe('stillLoading', () => {
  it('ignores re-render noise and catches real growth', () => {
    expect(stillLoading(1000, 1040)).toBe(false);
    expect(stillLoading(1000, 900)).toBe(false);
    expect(stillLoading(1000, 1300)).toBe(true);
  });
});

describe('scanUntilStable', () => {
  const settle = async () => {};

  it('returns the first scan pair that agrees, keeping the later result', async () => {
    const counts = [400, 1200, 1210];
    let i = 0;
    const scan = await scanUntilStable(async () => result(counts[i++]), settle);
    expect(scan.settled).toBe(true);
    expect(scan.counts).toEqual([400, 1200, 1210]);
    expect(evaluatedNodes(scan.result)).toBe(1210);
  });

  it('fails a page that is still growing when the attempts run out', async () => {
    let n = 100;
    const scan = await scanUntilStable(async () => result((n *= 2)), settle, 3);
    expect(scan.settled).toBe(false);
    expect(scan.counts).toEqual([200, 400, 800]);
  });

  it('settles before every scan', async () => {
    let settles = 0;
    await scanUntilStable(
      async () => result(10),
      async () => {
        settles++;
      },
    );
    expect(settles).toBe(2);
  });
});
