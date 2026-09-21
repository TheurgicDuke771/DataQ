import { expect, test } from '@playwright/test';

// Keyboard traversal, in a real browser: jsdom has no focus order, no layout and no scrolling,
// so none of this can be asserted there.
test.describe('Keyboard navigation', () => {
  test('the first Tab stop is a skip link that lands focus on the page content', async ({
    page,
  }) => {
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Monitoring Dashboard' })).toBeVisible();
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.locator('main#dq-main')).toBeFocused();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('a run is reachable and openable from the Results table without a mouse', async ({
    page,
  }) => {
    await page.goto('/results');
    const link = page.locator('tr.ant-table-row a.dq-row-link').first();
    await expect(link).toBeVisible();
    await link.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await expect(page.getByTestId('rd-screen')).toBeVisible();
    // Focus followed the navigation instead of staying on a link that no longer exists.
    await expect(page.locator('main#dq-main')).toBeFocused();
  });

  test('clicking a row still opens the run, once — Back returns to the list', async ({ page }) => {
    await page.goto('/results');
    await page.locator('tr.ant-table-row a.dq-row-link').first().click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/results$/);
  });

  test('an asset is openable from the Assets table by keyboard', async ({ page }) => {
    await page.goto('/assets');
    await page.getByText('All assets', { exact: true }).click();
    const link = page.locator('tr.ant-table-row a.dq-row-link').first();
    await link.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/assets\/[0-9a-f-]+$/);
  });

  test('clicking a cell that is not the link still opens the run, once', async ({ page }) => {
    await page.goto('/results');
    await page.locator('tr.ant-table-row').first().locator('td').nth(3).click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/results$/);
  });

  test('a suite opens from the master list by keyboard, and focus stays in the list', async ({
    page,
  }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').first().click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const other = page.locator('.dq-suite-row a.dq-row-link', { hasText: 'Orders volume' });
    await other.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Orders volume', level: 4 })).toBeVisible();
    // Selecting a suite navigates without going anywhere: the list is still there, so focus
    // must not be taken off it.
    await expect(other).toBeFocused();
    await expect(other).toHaveAttribute('aria-current', 'page');
  });

  test('switching admin tabs keeps focus on the tab bar', async ({ page }) => {
    await page.goto('/admin/overview');
    const members = page.getByRole('tab', { name: 'Members' });
    await members.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/admin\/members$/);
    await expect(members).toBeFocused();
  });

  test('a truncated asset name keeps a whole focus ring', async ({ page }) => {
    await page.goto('/assets');
    await page.getByText('All assets', { exact: true }).click();
    const link = page.locator('tr.ant-table-row a.dq-row-link').first();
    await link.focus();
    // An `overflow: hidden` ancestor clips the ring; the link must not sit inside one.
    const clipped = await link.evaluate((el) => {
      const cell = el.closest('td');
      for (let a = el.parentElement; a && a !== cell; a = a.parentElement) {
        if (getComputedStyle(a).overflow !== 'visible') return a.className || a.tagName;
      }
      return null;
    });
    expect(clipped).toBeNull();
  });
});
