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

  test('renders trend, per-suite performance, and recent runs', async ({ page }) => {
    await expect(page.getByText(/Quality Trends|Trends/i).first()).toBeVisible();
    await expect(page.getByText(/Suite Performance|per-suite/i).first()).toBeVisible();
    // The recent-runs feed resolves the seeded suite by name.
    await expect(page.getByText('Orders quality').first()).toBeVisible();
  });
});
