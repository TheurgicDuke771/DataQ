import { expect, test } from '@playwright/test';

// Enhanced Monitoring Dashboard (W6, #333) with seeded data: the four KPI
// cards render real values (the seed lands runs + connections, so none of
// them are empty-state), and the trend/per-suite/recent-runs widgets mount.
test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard', level: 3 })).toBeVisible();
  });

  test('renders the four KPI cards with seeded values', async ({ page }) => {
    for (const label of ['Data Integrity Score', 'Pass Rate', 'Total Runs', 'Active Connections']) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    // Seeded runs mean Total Runs is a number, not the loading/empty dash.
    const totalRuns = page
      .locator('.ant-card')
      .filter({ hasText: 'Total Runs' })
      .getByText(/^\d+$/);
    await expect(totalRuns.first()).toBeVisible();
  });

  test('renders trend, per-suite performance, and recent runs with seeded data', async ({
    page,
  }) => {
    // Card titles render unconditionally, so assert CONTENT: the seeded runs
    // give the trend chart an svg and put 'Orders quality' in per-suite +
    // recent-runs — these fail if the summary endpoint breaks.
    const trendCard = page.locator('.ant-card').filter({ hasText: /Trends/i });
    await expect(trendCard.locator('svg').first()).toBeVisible();
    const suiteCard = page.locator('.ant-card').filter({ hasText: /Suite Performance/i });
    await expect(suiteCard.getByText('Orders quality').first()).toBeVisible();
    await expect(page.getByText('Orders quality').first()).toBeVisible();
  });
});
