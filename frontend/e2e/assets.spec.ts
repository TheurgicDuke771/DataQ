import { expect, type Page, test } from '@playwright/test';

// The read-only Assets view (ADR 0034 gap G-d phase 2, #760; hierarchical browse #802).
test.describe('Assets page', () => {
  test('drills the connection-rooted tree to an asset leaf and opens the detail', async ({
    page,
  }) => {
    await page.goto('/');
    await page.getByRole('link', { name: 'Assets' }).click();
    await expect(page.getByRole('heading', { name: 'Assets', level: 3 })).toBeVisible();

    // Default lens = the connection-rooted drill-down (#802): the OL namespace is
    // the datasource root, the table is a leaf under DB → schema.
    await expect(page.getByText('By source')).toBeVisible();
    const ordersLeaf = page.getByRole('treeitem', { name: /ORDERS/ });
    await expect(ordersLeaf.first()).toBeVisible();
    await ordersLeaf.first().click();
    await expect(page).toHaveURL(/\/assets\/[0-9a-f-]+$/);
  });

  test('the "All assets" table lens still lists and opens assets', async ({ page }) => {
    await page.goto('/assets');
    await page.getByText('All assets').click();

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
      .getByRole('treeitem', { name: /ORDERS/ })
      .first()
      .click();

    // Identity header + both composing suites of the shared asset.
    await expect(page.getByRole('heading', { name: 'ANALYTICS.PUBLIC.ORDERS' })).toBeVisible();
    await expect(page.getByText(/Monitored by 2 suites/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Orders quality' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Orders volume' })).toBeVisible();

    // Lineage renders as one graph (#805) — or its empty state when the seed has no
    // edges for this asset. Either way the Lineage card is present.
    await expect(page.getByText('Lineage', { exact: true })).toBeVisible();

    // A composing suite links back to its suite page.
    await page.getByRole('button', { name: 'Orders quality' }).click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
  });

  test('an admin overrides one table’s automatic-check sensitivity and removes it again', async ({
    page,
    request,
  }) => {
    const assets = (await (await request.get('/api/v1/assets')).json()) as { id: string }[];
    const assetId = assets[0].id;
    try {
      await overrideAndRemove(page, assetId);
    } finally {
      // Seeded state shared by every spec: remove the override even when the test fails.
      await request.patch(`/api/v1/assets/${assetId}`, { data: { auto_coverage_config: null } });
    }
  });

  async function overrideAndRemove(page: Page, assetId: string) {
    await page.goto(`/assets/${assetId}`);
    await page.getByText('Sensitivity of this table’s automatic checks').click();
    const block = page.getByTestId('coverage-sensitivity');

    await block.getByRole('switch', { name: 'Set for this table: Row count thresholds' }).click();
    const critical = block.getByRole('spinbutton', { name: 'Row count critical deviations' });
    await critical.fill('10');
    await critical.blur();
    await block.getByRole('button', { name: 'Save' }).click();
    await expect(block.locator('.ant-tag', { hasText: 'Set for this table' })).toBeVisible();
    await expect(critical).toHaveValue('10.0');

    await block.getByRole('button', { name: 'Use the workspace setting' }).click();
    await expect(block.locator('.ant-tag', { hasText: 'Set for this table' })).toHaveCount(0);
    await expect(critical).toBeDisabled();
  }
});
