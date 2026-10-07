import { expect, test } from '@playwright/test';

// Seeded suite + checks (backend/scripts/demo_data.py) read through the real API.
test.describe('Suites page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/suites');
    await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
  });

  test('selecting a seeded suite shows its checks', async ({ page }) => {
    await page.getByText('Orders quality').click();

    // Selection is the route now — the URL carries the suite id (deep-linkable).
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    // Detail panel renders the suite title (h4) and its seeded checks.
    await expect(page.getByRole('heading', { name: 'Orders quality', level: 4 })).toBeVisible();
    await expect(page.getByText('order_id not null')).toBeVisible();
    await expect(page.getByText('amount in range')).toBeVisible();
    // The expectation type is surfaced under each check name.
    await expect(page.getByText('expect_column_values_to_not_be_null').first()).toBeVisible();
  });

  test('add a check via the dedicated page, then delete it', async ({ page }) => {
    const name = `e2e check ${Date.now()}`;

    // Open a seeded suite, then the dedicated check page.
    await page.getByText('Orders quality').click();
    await page.getByRole('button', { name: 'Add check' }).click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+\/checks\/new$/);

    // Step 1: categories (real + reserved) → pick one.
    await expect(page.getByText('Column values', { exact: true })).toBeVisible();
    await expect(page.getByText('Freshness', { exact: true })).toBeVisible();
    await page.getByText('Column values', { exact: true }).click();

    // Step 2: pick an expectation → Step 3: fill config + create.
    await page.getByText('Column values not null', { exact: true }).click();
    await page.getByLabel('Name').fill(name);
    await page.getByLabel('Column', { exact: true }).fill('order_id');
    // The dry-run preview is wired + enabled (the seeded suite has a Snowflake table target).
    await expect(page.getByRole('button', { name: 'Dry-run preview' })).toBeEnabled();
    await page.getByRole('button', { name: 'Create check' }).click();

    // Back on the suite detail, the new check is listed.
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const row = page.locator('[role="listitem"]').filter({ hasText: name });
    await expect(row).toBeVisible();

    // Clean up: the check row's Delete → confirm.
    await row.getByRole('button', { name: 'Delete' }).click();
    await page
      .getByRole('dialog', { name: /^Delete/ })
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(page.locator('[role="listitem"]').filter({ hasText: name })).toHaveCount(0);
  });

  test('bulk snooze, unsnooze, thresholds, disable and delete act on exactly the selected checks', async ({
    page,
  }) => {
    const stamp = Date.now();
    const names = [`e2e bulk a ${stamp}`, `e2e bulk b ${stamp}`];

    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const suiteId = page.url().split('/').pop();
    // Two throwaway checks through the real API, so the seeded ones are never touched.
    for (const name of names) {
      const created = await page.request.post(`/api/v1/suites/${suiteId}/checks`, {
        data: {
          name,
          expectation_type: 'expect_column_values_to_not_be_null',
          config: { column: 'order_id' },
        },
      });
      expect(created.status()).toBe(201);
    }
    await page.reload();
    const rows = names.map((name) => page.locator('[role="listitem"]').filter({ hasText: name }));
    const seeded = page.locator('[role="listitem"]').filter({ hasText: 'order_id not null' });
    for (const row of rows) await expect(row).toBeVisible();

    const selectBoth = async () => {
      for (const name of names)
        await page.getByRole('checkbox', { name: `Select ${name}` }).check();
      await expect(page.getByText('2 selected')).toBeVisible();
    };

    // Snooze: both selected rows get the badge, the seeded row does not.
    await selectBoth();
    await page.getByRole('button', { name: 'Snooze selected', exact: true }).click();
    await page.getByText('1 hour', { exact: true }).click();
    for (const row of rows) await expect(row.getByText(/Snoozed until/)).toBeVisible();
    await expect(seeded.getByText(/Snoozed until/)).toHaveCount(0);
    await expect(page.getByText('2 selected')).toHaveCount(0);

    // Unsnooze: the badges go.
    await selectBoth();
    await page.getByRole('button', { name: 'Unsnooze selected', exact: true }).click();
    for (const row of rows) await expect(row.getByText(/Snoozed until/)).toHaveCount(0);

    // Thresholds: one value on both, shown on each row; the seeded row keeps its own.
    await selectBoth();
    await page.getByRole('button', { name: 'Set thresholds', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Set thresholds on 2 checks' });
    await dialog.getByRole('combobox', { name: 'Fail' }).click();
    await page.locator('.ant-select-dropdown').last().getByText('Set to', { exact: true }).click();
    await dialog.getByRole('spinbutton', { name: 'Fail threshold' }).fill('5');
    await dialog.getByRole('button', { name: 'Apply to 2 checks' }).click();
    for (const row of rows) await expect(row.getByText('· fail 5')).toBeVisible();
    await expect(dialog).toHaveCount(0);

    // Disable: both rows are badged and offer Enable; the seeded row is untouched.
    await selectBoth();
    await page.getByRole('button', { name: 'Disable selected', exact: true }).click();
    for (const row of rows) await expect(row.getByText('Disabled', { exact: true })).toBeVisible();
    await expect(seeded.getByText('Disabled', { exact: true })).toHaveCount(0);
    // Re-enable one from its own row; the other stays off.
    await rows[0].getByRole('button', { name: 'Enable', exact: true }).click();
    await expect(rows[0].getByText('Disabled', { exact: true })).toHaveCount(0);
    await expect(rows[1].getByText('Disabled', { exact: true })).toBeVisible();

    // Delete: confirm names the count; only the two throwaway checks go.
    await selectBoth();
    await page.getByRole('button', { name: 'Delete selected', exact: true }).click();
    await page
      .getByRole('dialog', { name: 'Delete 2 checks?' })
      .getByRole('button', { name: 'Delete 2 checks' })
      .click();
    for (const row of rows) await expect(row).toHaveCount(0);
    await expect(seeded).toBeVisible();
  });

  test('create a suite, see it in the list, then delete it', async ({ page }) => {
    const name = `e2e suite ${Date.now()}`;

    // Creating a suite is now a dedicated page (/suites/new), not a drawer.
    await page.getByRole('button', { name: 'New suite' }).click();
    await expect(page).toHaveURL(/\/suites\/new$/);
    await page.getByLabel('Name').fill(name);

    // antd Select (not searchable): focus the combobox, wait for the dropdown, then Enter accepts
    // the auto-highlighted FIRST option.
    const combo = page.getByRole('combobox');
    await combo.click();
    await expect(page.locator('.ant-select-dropdown').last()).toBeVisible();
    await combo.press('Enter');

    // Create continues to the Add Check page (the suite now exists, untargeted).
    await page.getByRole('button', { name: /Create & add checks/ }).click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+\/checks\/new$/);

    // Back on the list, the new suite is there; select it.
    await page.goto('/suites');
    const item = page.getByText(name, { exact: true });
    await expect(item).toBeVisible();
    await item.click();
    await expect(page.getByRole('heading', { name, level: 4 })).toBeVisible();

    // Delete it via the detail action → confirm modal → the list row disappears.
    await page.getByRole('button', { name: 'Delete' }).click();
    const confirm = page.getByRole('dialog', { name: /^Delete/ });
    await confirm.getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('[role="listitem"]').filter({ hasText: name })).toHaveCount(0);
  });
});
