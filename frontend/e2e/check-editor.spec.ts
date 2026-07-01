import { expect, test } from '@playwright/test';

// The check-editor variants beyond the plain expectation form (already covered
// in suites.spec.ts): the freshness + volume monitor kinds (ADR 0012) and the
// Monaco-backed custom-SQL editor (ADR 0019). All three are SQL-only
// categories, offered here because the seeded suite targets Snowflake. Each
// authoring loop creates → verifies on the suite detail → deletes.
test.describe('Check editor variants', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    await page.getByRole('button', { name: 'Add check' }).click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+\/checks\/new$/);
  });

  /** Step-2 spec cards share their label with the step-1 category card, so
   *  click the spec by its unique description text instead. */
  const deleteCheck = async (page: import('@playwright/test').Page, name: string) => {
    const row = page.locator('[role="listitem"]').filter({ hasText: name });
    await row.getByRole('button', { name: 'Delete' }).click();
    await page
      .getByRole('dialog', { name: /^Delete/ })
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(page.locator('[role="listitem"]').filter({ hasText: name })).toHaveCount(0);
  };

  test('author a freshness monitor (fail threshold required)', async ({ page }) => {
    const name = `e2e freshness ${Date.now()}`;

    await page.getByText('Freshness', { exact: true }).click();
    await page.getByText(/How stale is the table/).click();

    await page.getByLabel('Name').fill(name);
    await page.getByLabel('Timestamp column').fill('created_at');
    // requireFailOrCritical: a freshness check without a fail/critical band
    // can never fail — the form enforces it, so band at 24h stale.
    await page.getByLabel('Fail ≥').fill('24');
    await page.getByRole('button', { name: 'Create check' }).click();

    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const row = page.locator('[role="listitem"]').filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(row.getByText('monitor:freshness')).toBeVisible();
    await deleteCheck(page, name);
  });

  test('author a volume monitor', async ({ page }) => {
    const name = `e2e volume ${Date.now()}`;

    await page.getByText('Volume', { exact: true }).click();
    await page.getByText(/Did the load deliver the expected row count/).click();

    await page.getByLabel('Name').fill(name);
    await page.getByLabel('Minimum rows').fill('100');
    await page.getByLabel('Maximum rows').fill('50000');
    await page.getByRole('button', { name: 'Create check' }).click();

    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const row = page.locator('[role="listitem"]').filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(row.getByText('monitor:volume')).toBeVisible();
    await deleteCheck(page, name);
  });

  test('author a custom-SQL check through the Monaco editor', async ({ page }) => {
    const name = `e2e custom sql ${Date.now()}`;

    await page.getByText('Custom SQL', { exact: true }).click();
    await page.getByText(/A SQL query that should return no rows/).click();

    await page.getByLabel('Name').fill(name);
    // Monaco loads in its own lazy chunk; type into it once mounted. Monaco
    // types-over the auto-inserted closing brace, so the literal text lands.
    const editor = page.locator('.monaco-editor');
    await expect(editor).toBeVisible({ timeout: 15_000 });
    await editor.click();
    await page.keyboard.type('SELECT * FROM {batch} WHERE amount < 0');

    await page.getByRole('button', { name: 'Create check' }).click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const row = page.locator('[role="listitem"]').filter({ hasText: name });
    await expect(row).toBeVisible();
    await deleteCheck(page, name);
  });
});
