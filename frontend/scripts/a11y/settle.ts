import { expect, type Page } from '@playwright/test';

import { type PageSample, stillChangingMessage, waitUntilStill } from './coverage';

const LOADING = '.ant-spin-spinning, .ant-skeleton-active';
const SAMPLE_MS = 250;

/** Wait until the page has stopped loading. A route's landmark can be visible while a table
 * inside it is still behind its `loading` Spin, so the rows a scan should judge are not in the
 * DOM yet and the scan passes vacuously. Also lets mount transitions finish, which otherwise
 * get sampled mid-colour.
 *
 * Loading is not always a Spin, and one spinner can follow another (#2051), so the page must
 * also hold still: no Spin or Skeleton and the same element count, within a ripple or two,
 * across three samples 250 ms apart. Failing that, this fails: the ratchet only catches NEW
 * violations, so a scan of a half-loaded page would otherwise pass. */
export async function settle(page: Page): Promise<void> {
  await expect(page.locator(LOADING)).toHaveCount(0);
  const stability = await waitUntilStill(
    () =>
      page.evaluate(
        (loading): PageSample => ({
          elements: document.querySelectorAll('*').length,
          loading: document.querySelectorAll(loading).length,
        }),
        LOADING,
      ),
    () => page.waitForTimeout(SAMPLE_MS),
  );
  expect(stability.settled, stillChangingMessage(stability.trend)).toBe(true);
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
}
