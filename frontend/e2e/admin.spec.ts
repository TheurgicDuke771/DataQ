import { expect, test } from '@playwright/test';

// Workspace-admin surfaces (#289 + ADR 0027): the dev-bypass identity is in
// WORKSPACE_ADMIN_EMAILS (compose + CI), so /admin and /settings are reachable
// and the admin-only footer nav renders. The webhook-config surface (#493) —
// the supported way to obtain the ADF/Airflow inbound webhook URLs instead of
// hand-assembling them (#92) — lives on Settings → Webhooks.
test.describe('Admin control centre', () => {
  test('renders the unscoped suites / members / access tables', async ({ page }) => {
    await page.goto('/admin');
    await expect(page.getByRole('heading', { name: 'Admin', level: 3 })).toBeVisible();

    // Scope to the content area — the sidebar nav also carries a 'Suites'
    // link, which would satisfy an unscoped getByText even with Admin broken.
    const main = page.getByRole('main');
    for (const section of ['Suites', 'Members', 'Access grants']) {
      await expect(main.getByText(section, { exact: true }).first()).toBeVisible();
    }
    // Unscoped visibility: the seeded suite and the resolved admin identity.
    await expect(main.getByText('Orders quality').first()).toBeVisible();
    await expect(main.getByText('dev-bypass@dataq.local').first()).toBeVisible();
  });

  test('settings exposes the inbound orchestration webhook config', async ({ page }) => {
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'Settings', level: 3 })).toBeVisible();

    await page.getByRole('tab', { name: 'Webhooks' }).click();
    await expect(page.getByText('Inbound webhooks (orchestration)')).toBeVisible();
    // One row per seeded orchestration provider; the ready-to-paste URL lives
    // in a readonly input (getByText can't see input values), ADF's token
    // masked behind the reveal toggle.
    await expect(page.getByText('Azure Data Factory', { exact: true })).toBeVisible();
    await expect(page.getByText('Airflow', { exact: true })).toBeVisible();
    await expect(page.locator('input[readonly]').first()).toHaveValue(/orchestration\/events\//);
  });
});
