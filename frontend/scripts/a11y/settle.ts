import { expect, type Page } from '@playwright/test';

/** Wait until the page has stopped loading. A route's landmark can be visible while a table
 * inside it is still behind its `loading` Spin, so the rows a scan should judge are not in the
 * DOM yet and the scan passes vacuously. Also lets mount transitions finish, which otherwise
 * get sampled mid-colour. */
export async function settle(page: Page): Promise<void> {
  await expect(page.locator('.ant-spin-spinning, .ant-skeleton-active')).toHaveCount(0);
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
}
