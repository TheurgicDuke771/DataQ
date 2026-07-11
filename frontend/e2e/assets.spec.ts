import { expect, test } from '@playwright/test';

// The read-only Assets view (ADR 0034 gap G-d phase 2, #760). The demo seed
// (backend/scripts/demo_data.py) lands TWO suites on the ANALYTICS.ORDERS table —
// "Orders quality" and "Orders volume" — with the same run target, so they
// resolve to ONE asset. The asset detail therefore renders health across ≥2
// composing suites (the #760 acceptance criterion). Visibility is derived from
// suite grants; the seed owner sees every suite.
test.describe('Assets page', () => {
  test('lists monitored assets and reaches the detail from the sidebar', async ({ page }) => {
    await page.goto('/');
    // The Assets nav item is a sidebar addition (phase 2 — not the phase-4
    // navigation inversion; Suites stays primary).
    await page.getByRole('link', { name: 'Assets' }).click();
    await expect(page.getByRole('heading', { name: 'Assets', level: 3 })).toBeVisible();

    // The seeded ORDERS asset appears (name = DB.SCHEMA.TABLE, upper-cased).
    const ordersRow = page
      .locator('tr.ant-table-row')
      .filter({ hasText: 'ANALYTICS.PUBLIC.ORDERS' });
    await expect(ordersRow.first()).toBeVisible();
    await ordersRow.first().click();
    await expect(page).toHaveURL(/\/assets\/[0-9a-f-]+$/);
  });

  test('renders health across ≥2 suites on the shared asset', async ({ page }) => {
    await page.goto('/assets');
    await page
      .locator('tr.ant-table-row')
      .filter({ hasText: 'ANALYTICS.PUBLIC.ORDERS' })
      .first()
      .click();

    // Identity header + both composing suites of the shared asset.
    await expect(page.getByRole('heading', { name: 'ANALYTICS.PUBLIC.ORDERS' })).toBeVisible();
    await expect(page.getByText(/Monitored by 2 suites/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Orders quality' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Orders volume' })).toBeVisible();

    // Lineage sections render (empty or populated depending on dbt-manifest data).
    // exact: substring matching would also hit the "No known upstream sources." empty-state.
    await expect(page.getByText('Upstream', { exact: true })).toBeVisible();
    await expect(page.getByText('Downstream', { exact: true })).toBeVisible();

    // A composing suite links back to its suite page.
    await page.getByRole('button', { name: 'Orders quality' }).click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
  });
});
