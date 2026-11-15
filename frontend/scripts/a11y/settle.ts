import { expect, type Page } from '@playwright/test';

/** How long the page must stay free of loading affordances to count as loaded: one spinner
 *  ending and a second one starting (two sequential fetches) leave a gap shorter than this. */
const QUIET_MS = 300;

/** Wait until the page has stopped loading. A route's landmark can be visible while a table
 * inside it is still behind its `loading` Spin, so the rows a scan should judge are not in the
 * DOM yet and the scan passes vacuously. Also lets mount transitions finish, which otherwise
 * get sampled mid-colour.
 *
 * Loading is not always a Spin (#2051): a lazy chunk behind a `Suspense` fallback, or a second
 * fetch that starts as the first one ends. So the network must go quiet too, and the spinners
 * must STAY gone for a moment, not merely reach zero once. */
export async function settle(page: Page): Promise<void> {
  const loading = page.locator('.ant-spin-spinning, .ant-skeleton-active');
  await expect(loading).toHaveCount(0);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(QUIET_MS);
  await expect(loading).toHaveCount(0);
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
}
