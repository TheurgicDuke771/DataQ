import { expect, test } from '@playwright/test';

// Reads the seeded demo connections through the real API (proxy → api → DB).
// Names/labels come from backend/scripts/demo_data.py.
test.describe('Connections page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/connections');
    await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();
  });

  test('lists the seeded connections under kind sections, grouped by type', async ({ page }) => {
    // The two top-level kind sections (datasource vs orchestration) and the
    // per-type sub-headings are both present.
    await expect(page.getByRole('heading', { name: 'Data sources', level: 4 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Orchestration', level: 4 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Snowflake', level: 5 })).toBeVisible();

    // A datasource and an orchestration provider both render.
    await expect(page.getByText('snowflake-analytics').first()).toBeVisible();
    await expect(page.getByText('s3-datalake').first()).toBeVisible();
    await expect(page.getByText('airflow-dags').first()).toBeVisible();
  });

  test('the edit page opens read-only on type + env (no create fields)', async ({ page }) => {
    // Open the ⋮ menu on a seeded Snowflake connection → Edit.
    const card = page.locator('.ant-card').filter({ hasText: 'snowflake-analytics' }).first();
    await card
      .locator('button')
      .filter({ has: page.locator('.anticon-more') })
      .click();
    await page.getByRole('menuitem', { name: 'Edit' }).click();

    // Editing is now a dedicated page (/connections/:id/edit), not a drawer.
    await expect(page).toHaveURL(/\/connections\/[0-9a-f-]+\/edit$/);
    await expect(page.getByRole('heading', { name: /Edit Snowflake connection/ })).toBeVisible();
    // Type is shown read-only; name stays editable; the secret field is omitted
    // (rotation is the separate Re-auth flow) — i.e. none of the create-only UI.
    await expect(page.getByText('Snowflake').first()).toBeVisible();
    await expect(page.getByLabel('Name')).toBeVisible();
    await expect(page.getByLabel('Password')).toHaveCount(0);
    // Two Cancels (page header + form footer), both → /connections; take the first.
    await page.getByRole('button', { name: 'Cancel' }).first().click();
    await expect(page).toHaveURL(/\/connections$/);
  });

  test('add a connection via the dedicated page, then delete it', async ({ page }) => {
    const name = `e2e-conn-${Date.now()}`;

    await page.getByRole('button', { name: 'Add connection' }).click();
    await expect(page).toHaveURL(/\/connections\/new$/);

    // Step 1: the categorized source picker (Orchestration first); pick a datasource.
    await expect(page.getByText('Orchestration', { exact: true })).toBeVisible();
    await expect(page.getByText('Warehouses', { exact: true })).toBeVisible();
    await page.getByText('Snowflake', { exact: true }).click();

    // Step 2: the Snowflake form appears; fill the required fields + secret.
    await expect(page.getByRole('heading', { name: /New Snowflake connection/ })).toBeVisible();
    await page.getByLabel('Name').fill(name);
    const env = page.getByLabel('Environment');
    await env.click();
    await env.press('ArrowDown');
    await env.press('Enter');
    for (const label of ['Account', 'User', 'Database', 'Schema', 'Warehouse']) {
      await page.getByLabel(label, { exact: true }).fill(`${label.toLowerCase()}-val`);
    }
    await page.getByLabel('Password').fill('sekret');
    await page.getByRole('button', { name: 'Create' }).click();

    // Back on the list, the new connection card is visible.
    await expect(page).toHaveURL(/\/connections$/);
    const card = page.locator('.ant-card').filter({ hasText: name });
    await expect(card).toBeVisible();

    // Clean up: the card's ⋮ menu → Delete → confirm.
    await card
      .locator('button')
      .filter({ has: page.locator('.anticon-more') })
      .click();
    await page.getByRole('menuitem', { name: 'Delete' }).click();
    await page
      .getByRole('dialog')
      .filter({ hasText: 'Delete' })
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(page.locator('.ant-card').filter({ hasText: name })).toHaveCount(0);
  });

  test('"Test all" kicks off a connectivity test on every connection', async ({ page }) => {
    const testAll = page.getByRole('button', { name: 'Test all' });
    await expect(testAll).toBeEnabled();
    await testAll.click();

    // With placeholder creds the tests fail-soft — we assert the health signal
    // *appears* (testing → unreachable), not that connectivity succeeds. The
    // settled failure badge reads "unreachable" (HealthBadge, Connections.tsx);
    // "healthy" only if real creds happened to work. A transient "testing…"
    // also counts — any of them proves the per-card health path ran end-to-end.
    await expect(page.getByText(/testing…|unreachable|healthy/).first()).toBeVisible({
      timeout: 20_000,
    });
  });
});
