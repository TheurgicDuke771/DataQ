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
    await page.getByRole('button', { name: 'Create check' }).click();

    // Back on the suite detail, the new check is listed.
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const row = page.locator('.ant-list-item').filter({ hasText: name });
    await expect(row).toBeVisible();

    // Clean up: the check row's Delete → confirm.
    await row.getByRole('button', { name: 'Delete' }).click();
    await page
      .getByRole('dialog', { name: /^Delete/ })
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(page.locator('.ant-list-item').filter({ hasText: name })).toHaveCount(0);
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
    // Anchor the confirm to the Modal by its title (`Delete "<name>"?`) — antd
    // renders role=dialog for the (now-hidden) Drawer too, so filter by name.
    await page.getByRole('button', { name: 'Delete' }).click();
    const confirm = page.getByRole('dialog', { name: /^Delete/ });
    await confirm.getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('.ant-list-item').filter({ hasText: name })).toHaveCount(0);
  });
});
