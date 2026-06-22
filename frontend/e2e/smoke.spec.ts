import { expect, test } from '@playwright/test';

// The app shell loads, dev-bypass resolves an identity (no login wall), and the
// root redirects to the Dashboard. This is the canary: if the stack or the proxy
// is down, this fails first with a clear message.
test('app shell loads under dev-bypass and redirects to Dashboard', async ({ page }) => {
  await page.goto('/');

  // Root → /dashboard (App.tsx Navigate; the post-login landing as of ADR 0022).
  await expect(page).toHaveURL(/\/dashboard$/);

  // The header brand (yin-yang logo + wordmark) + the resolved identity in the
  // account menu (proves auth resolved, so every /api call carries the user).
  // The DEV BYPASS tag now lives inside the account dropdown, so assert the
  // always-visible name instead.
  await expect(page.getByRole('img', { name: 'DataQ logo' })).toBeVisible();
  await expect(page.getByText('DataQ', { exact: true })).toBeVisible();
  await expect(page.getByText('Dev Bypass User', { exact: true })).toBeVisible();
});

test('the sider navigates between Connections and Suites', async ({ page }) => {
  await page.goto('/connections');
  await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();

  await page.getByRole('link', { name: 'Suites' }).click();
  await expect(page).toHaveURL(/\/suites$/);
  await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
});
