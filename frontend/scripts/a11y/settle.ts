import { expect, type Page } from '@playwright/test';

/** Wait until the page has stopped loading. axe reports `color-contrast` as *incomplete*, not
 * as a violation, for anything under a sub-opaque ancestor — and antd's `Spin` blurs its
 * container to opacity 0.5 — so a scan that lands mid-load passes vacuously. */
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
