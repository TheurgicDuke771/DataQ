import { describe, expect, it } from 'vitest';

import { type PageSample, heldStill, waitUntilStill } from '../../scripts/a11y/coverage';

// The under-reporting guard (#2051): a scan must not judge a page that is still changing, since
// the ratchet alone cannot see that it saw too little.

const at = (elements: number, loading = 0): PageSample => ({ elements, loading });

async function run(samples: PageSample[], maxSamples = samples.length) {
  let i = 0;
  return waitUntilStill(
    async () => samples[Math.min(i++, samples.length - 1)],
    async () => {},
    { maxSamples },
  );
}

describe('heldStill', () => {
  it('allows a ripple, catches growth and shrinkage, and never a loading page', () => {
    expect(heldStill(at(3000), at(3002))).toBe(true);
    expect(heldStill(at(3000), at(3040))).toBe(false); // a late ten-row table
    expect(heldStill(at(1200), at(400))).toBe(false); // a refetch swapped the table out
    expect(heldStill(at(40), at(40, 1))).toBe(false);
  });
});

describe('waitUntilStill', () => {
  it('settles once the page holds still for two pairs', async () => {
    const result = await run([at(400), at(1200), at(1200), at(1201)]);
    expect(result).toEqual({ settled: true, trend: [400, 1200, 1200, 1201] });
  });

  it('starts over when the page changes again: a second spinner after the first', async () => {
    const result = await run([at(500), at(500), at(500, 1), at(900), at(900), at(900)]);
    expect(result.settled).toBe(true);
    expect(result.trend).toHaveLength(6);
  });

  it('does not settle on one quiet pair', async () => {
    const result = await run([at(500), at(500), at(900)], 3);
    expect(result.settled).toBe(false);
  });

  it('fails a page that keeps changing', async () => {
    let n = 100;
    const result = await waitUntilStill(
      async () => at((n += 50)),
      async () => {},
      { maxSamples: 5 },
    );
    expect(result).toEqual({ settled: false, trend: [150, 200, 250, 300, 350] });
  });
});
