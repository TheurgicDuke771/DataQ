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
    await page.getByText('All assets').click();
    const link = page.locator('tr.ant-table-row a.dq-row-link').first();
    await link.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/assets\/[0-9a-f-]+$/);
  });
});
