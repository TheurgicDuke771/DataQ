import { expect, test } from '@playwright/test';

// Enhanced Monitoring Dashboard (W6, #333) with seeded data: the four KPI cards render real values
// (the seed lands runs + connections, so none of them are empty-state).
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
    // Card titles render unconditionally, so assert CONTENT: the seeded runs give the trend chart
    // an svg and put 'Orders quality' in per-suite + recent-runs.
    const trendCard = page.locator('.ant-card').filter({ hasText: /Trends/i });
    await expect(trendCard.locator('svg').first()).toBeVisible();
    const suiteCard = page.locator('.ant-card').filter({ hasText: /Suite Performance/i });
    await expect(suiteCard.getByText('Orders quality').first()).toBeVisible();
    await expect(page.getByText('Orders quality').first()).toBeVisible();
  });
});

// First-run path (#1668). The seeded workspace has done every step, so the status is answered by
// `page.route`: what is under test is the panel rendering in a real layout and its action
// navigating, not the status query.
test.describe('Dashboard — get started', () => {
  test('is absent on a workspace that has done every step', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page.getByText('Data Integrity Score', { exact: true })).toBeVisible();
    await expect(page.getByTestId('get-started-panel')).toHaveCount(0);
  });

  test('offers the first step on a new workspace and goes there', async ({ page }) => {
    await page.route('**/api/v1/dashboard/onboarding', (route) =>
      route.fulfill({
        json: {
          has_datasource: false,
          has_suite: false,
          has_check: false,
          has_run: false,
          complete: false,
        },
      }),
    );
    await page.goto('/dashboard');

    const panel = page.getByTestId('get-started-panel');
    await expect(panel.getByText('Get started — 0 of 4 done')).toBeVisible();
    await panel.getByRole('button', { name: 'Add a connection' }).click();
    await expect(page).toHaveURL(/\/connections\/new$/);
  });
});
