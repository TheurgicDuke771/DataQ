// Under-reporting guard for the a11y scans (#2051). The ratchet fails only on violations that
// are NEW, so a scan that saw less page than it should have — a table still behind its loading
// state — always passes, and with an empty baseline there is no row whose absence could reveal
// it. So a scan waits until the page has stopped changing: the number of elements holds still,
// in both directions, with no loading affordance, over consecutive samples. A page that never
// holds still fails rather than being judged on whatever was in the DOM at that moment.
// Kept framework-free so the Vitest unit test can exercise it without a browser.

/** One cheap look at the page: how many elements it has, and how many are loading. */
export interface PageSample {
  elements: number;
  loading: number;
}

/** Elements that may come and go on a loaded page without it changing: a ripple, a tooltip. */
export const ELEMENT_TOLERANCE = 2;

/** Whether the page held still between two samples: nothing loading in either, and about the
 *  same number of elements. Shrinking counts as change too — a refetch that swaps a table for
 *  a placeholder is as half-loaded as one that has not rendered yet. */
export function heldStill(earlier: PageSample, later: PageSample): boolean {
  return (
    earlier.loading === 0 &&
    later.loading === 0 &&
    Math.abs(later.elements - earlier.elements) <= ELEMENT_TOLERANCE
  );
}

export interface StabilityOptions {
  /** Consecutive still pairs needed: 3 samples 250 ms apart is 750 ms with no change. */
  stillPairs?: number;
  /** Samples before giving up. */
  maxSamples?: number;
}

export interface Stability {
  settled: boolean;
  /** Each sample's element count, for the failure message. */
  trend: number[];
}

/** Sample until the page has held still for ``stillPairs`` consecutive pairs. */
export async function waitUntilStill(
  sample: () => Promise<PageSample>,
  pause: () => Promise<void>,
  { stillPairs = 2, maxSamples = 40 }: StabilityOptions = {},
): Promise<Stability> {
  let previous = await sample();
  const trend = [previous.elements];
  let still = 0;
  for (let i = 1; i < maxSamples; i++) {
    await pause();
    const current = await sample();
    trend.push(current.elements);
    still = heldStill(previous, current) ? still + 1 : 0;
    if (still >= stillPairs) return { settled: true, trend };
    previous = current;
  }
  return { settled: false, trend };
}

export function stillChangingMessage(trend: number[]): string {
  const shown = trend.length > 12 ? ['…', ...trend.slice(-12)] : trend;
  return (
    'the page never stopped changing, so an accessibility scan would judge only part of it ' +
    `(element count per sample: ${shown.join(' → ')}). Wait for the content the page shows ` +
    'before scanning, or find what keeps re-rendering.'
  );
}
