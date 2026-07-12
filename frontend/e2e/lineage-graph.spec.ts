import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

// Lineage graph (#805). The demo seed lands a lineage neighbourhood around the
// shared ANALYTICS.PUBLIC.ORDERS asset — two hops of provenance (RAW → STG) and
// two of blast radius (MART → BI) — so the graph has real depth-≥2 structure to
// lay out. We reach it the way a user does (via the assets tree), never by a
// hardcoded id, so this runs against any freshly-seeded stack.
async function openOrdersAsset(page: Page) {
  await page.goto('/assets');
  await page
    .getByRole('treeitem', { name: /ORDERS/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/assets\/[0-9a-f-]+$/);
}

test('renders a directional graph with clickable nodes', async ({ page }) => {
  await openOrdersAsset(page);

  const graph = page.getByRole('img', { name: /Lineage graph/ });
  await expect(graph).toBeVisible();

  // Depth ≥2 both ways: STG is one hop upstream and RAW two; MART is one hop
  // downstream and the BI dashboard two.
  await expect(page.getByLabel(/Open asset .*STG_ORDERS/)).toBeVisible();
  await expect(page.getByLabel(/Open asset .*ORDERS_RAW/)).toBeVisible();
  await expect(page.getByLabel(/Open asset .*MART\.REVENUE/)).toBeVisible();
  await expect(page.getByLabel(/Open asset .*REVENUE_DAILY/)).toBeVisible();

  // One drawn edge per real backend edge, each with a direction arrow.
  expect(await graph.locator('path[marker-end]').count()).toBeGreaterThanOrEqual(4);

  // A node navigates to that asset.
  await page.getByLabel(/Open asset .*MART\.REVENUE/).click();
  await expect(page.getByRole('heading', { name: 'ANALYTICS.MART.REVENUE' })).toBeVisible();
});

test('mobile: the graph scrolls inside its card, the page does not overflow', async ({ page }) => {
  await openOrdersAsset(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('img', { name: /Lineage graph/ })).toBeVisible();

  // The graph is wider than the phone, and it scrolls in its own container…
  const scrollable = await page.evaluate(() => {
    // Target the graph specifically: the brand mark is also an svg[role="img"].
    const svg = document.querySelector('svg[aria-label^="Lineage graph"]');
    const box = svg?.parentElement as HTMLElement | null;
    return box ? box.scrollWidth > box.clientWidth : false;
  });
  expect(scrollable).toBe(true);

  // …while the PAGE itself never gains a horizontal scrollbar (the #805 AC).
  const pageOverflows = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(pageOverflows).toBe(false);
});
