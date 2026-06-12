import { expect, test } from '@playwright/test';

// The app shell loads, dev-bypass resolves an identity (no login wall), and the
// root redirects to Connections. This is the canary: if the stack or the proxy
// is down, this fails first with a clear message.
test('app shell loads under dev-bypass and redirects to Connections', async ({ page }) => {
  await page.goto('/');

  // Root → /connections (App.tsx Navigate).
  await expect(page).toHaveURL(/\/connections$/);

  // The header brand (yin-yang logo + wordmark) + the dev-bypass identity chip
  // (proves auth resolved, so every /api call carries the dev-bypass user).
  await expect(page.getByRole('img', { name: 'DataQ logo' })).toBeVisible();
  await expect(page.getByText('DataQ', { exact: true })).toBeVisible();
  await expect(page.getByText('DEV BYPASS', { exact: true })).toBeVisible();
});

test('the sider navigates between Connections and Suites', async ({ page }) => {
  await page.goto('/connections');
  await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();

  await page.getByRole('link', { name: 'Suites' }).click();
  await expect(page).toHaveURL(/\/suites$/);
  await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
});
