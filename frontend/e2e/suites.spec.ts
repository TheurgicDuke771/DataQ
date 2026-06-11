import { expect, test } from '@playwright/test';

// Seeded suite + checks (backend/scripts/demo_data.py) read through the real API,
// then a full browser authoring round-trip (create → verify → delete) that
// mirrors the httpx API smoke but through the React UI a user actually drives.
test.describe('Suites page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/suites');
    await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
  });

  test('selecting a seeded suite shows its checks', async ({ page }) => {
    await page.getByText('Orders quality').click();

    // Detail panel renders the suite title (h4) and its seeded checks.
    await expect(page.getByRole('heading', { name: 'Orders quality', level: 4 })).toBeVisible();
    await expect(page.getByText('order_id not null')).toBeVisible();
    await expect(page.getByText('amount in range')).toBeVisible();
    // The expectation type is surfaced under each check name.
    await expect(page.getByText('expect_column_values_to_not_be_null').first()).toBeVisible();
  });

  test('create a suite, see it in the list, then delete it', async ({ page }) => {
    const name = `e2e suite ${Date.now()}`;

    await page.getByRole('button', { name: 'New suite' }).click();
    const drawer = page.getByRole('dialog', { name: 'New suite' });
    await drawer.getByLabel('Name').fill(name);

    // antd Select (not searchable): focus the combobox, wait for the dropdown,
    // then keyboard-select the first option. Keyboard on the focused combobox is
    // more stable than clicking a virtual-list option (rc-virtual-list keeps
    // items visibility:hidden during measurement).
    const combo = drawer.getByRole('combobox');
    await combo.click();
    await expect(page.locator('.ant-select-dropdown').last()).toBeVisible();
    await combo.press('ArrowDown');
    await combo.press('Enter');

    await drawer.getByRole('button', { name: 'Create' }).click();
    await expect(drawer).toBeHidden();

    // The new suite appears in the left list; select it.
    const item = page.getByText(name, { exact: true });
    await expect(item).toBeVisible();
    await item.click();
    await expect(page.getByRole('heading', { name, level: 4 })).toBeVisible();

    // Delete it via the detail action → confirm modal → the list row disappears.
    await page.getByRole('button', { name: 'Delete' }).click();
    const confirm = page.getByRole('dialog').filter({ hasText: 'Delete' });
    await confirm.getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('.ant-list-item').filter({ hasText: name })).toHaveCount(0);
  });
});
