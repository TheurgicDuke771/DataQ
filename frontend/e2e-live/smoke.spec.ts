import { expect, test } from './live-test';

// Read-only live smoke against the DEPLOYED app + the ADR 0021 harness data.
const LIVE_SUITE = process.env.E2E_LIVE_SUITE || 'Snowflake — Orders (all paths)';

test('dashboard renders live KPIs', async ({ page }) => {
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: 'Dashboard', level: 3 })).toBeVisible();
  for (const label of ['Data Integrity Score', 'Pass Rate', 'Total Runs', 'Active Connections']) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
});

test(`suites list shows the live suite ("${LIVE_SUITE}") and its checks`, async ({ page }) => {
  await page.goto('/suites');
  await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
  await page.getByText(LIVE_SUITE).first().click();
  await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
  await expect(page.getByRole('heading', { name: LIVE_SUITE, level: 4 })).toBeVisible();
});

test('results page lists runs and a run detail opens', async ({ page }) => {
  await page.goto('/results');
  await expect(page.getByRole('heading', { name: 'Results', level: 3 })).toBeVisible();
  const firstRun = page.locator('tr.ant-table-row').first();
  await expect(firstRun).toBeVisible();
  await firstRun.click();
  await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
});
