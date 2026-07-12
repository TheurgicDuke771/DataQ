import { expect, test } from '@playwright/test';

// Lineage graph (#805). The local dev seed carries a dbt-derived chain
// (RETAIL → ANALYTICS_STG → ANALYTICS marts), so a mid-chain asset has ≥2 hops of
// upstream — enough to pin the depth-≥2 layout and the mobile scroll behaviour.
const MART = '/assets/765f532f-08a4-4ee3-a474-596184b9b168';

test('renders a directional graph with clickable nodes', async ({ page }) => {
  await page.goto(MART);
  const graph = page.getByRole('img', { name: /Lineage graph/ });
  await expect(graph).toBeVisible();

  // Depth ≥2: the staging views are one hop out, the raw tables two.
  await expect(page.getByLabel(/Open asset .*STG_ORDERS/)).toBeVisible();
  await expect(page.getByLabel(/Open asset .*ORDERS_HEADER/)).toBeVisible();
  // Edges are drawn (one per real backend edge), with direction arrows.
  expect(await graph.locator('path[marker-end]').count()).toBeGreaterThanOrEqual(2);

  // A node navigates to that asset.
  await page
    .getByLabel(/Open asset .*STG_ORDERS/)
    .first()
    .click();
  await expect(page).toHaveURL(/\/assets\/[0-9a-f-]+$/);
});

test('mobile: the graph scrolls inside its card, the page does not overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(MART);
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
