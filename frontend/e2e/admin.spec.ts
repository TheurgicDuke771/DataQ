import { expect, test } from '@playwright/test';

// Workspace-admin surfaces (#289 + ADR 0027): the dev-bypass identity is in WORKSPACE_ADMIN_EMAILS
// (compose + CI), so /admin is reachable and the admin-only footer nav renders.
test.describe('Admin control centre', () => {
  test('lands on the overview tab with the workspace counts', async ({ page }) => {
    await page.goto('/admin');
    await expect(page.getByRole('heading', { name: 'Admin', level: 3 })).toBeVisible();
    // /admin redirects to /admin/overview.
    await expect(page).toHaveURL(/\/admin\/overview$/);

    const main = page.getByRole('main');
    for (const card of ['Suites', 'Members', 'Access grants']) {
      await expect(main.getByText(card, { exact: true }).first()).toBeVisible();
    }
  });

  test('deep-links straight into a sub-page and switches tabs by URL', async ({ page }) => {
    await page.goto('/admin/suites');
    await expect(page.getByRole('tab', { name: 'Suites' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // Unscoped visibility: the seeded suite.
    await expect(page.getByRole('main').getByText('Orders quality').first()).toBeVisible();

    await page.getByRole('tab', { name: 'Members' }).click();
    await expect(page).toHaveURL(/\/admin\/members$/);
    await expect(page.getByRole('main').getByText('dev-bypass@dataq.local').first()).toBeVisible();
  });

  test('the retired /settings URL redirects into the admin settings tab', async ({ page }) => {
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/admin\/settings$/);
    await expect(page.getByRole('button', { name: 'Send test email' })).toBeVisible();
  });

  test('integrations exposes the inbound orchestration webhook config', async ({ page }) => {
    await page.goto('/admin/integrations');
    await expect(page.getByText('Inbound webhooks (orchestration)')).toBeVisible();
    // One row per seeded orchestration provider; the ready-to-paste URL lives in a readonly input
    // (getByText can't see input values), ADF's token masked behind the reveal toggle.
    await expect(page.getByText('Azure Data Factory', { exact: true })).toBeVisible();
    await expect(page.getByText('Apache Airflow', { exact: true })).toBeVisible();
    await expect(page.locator('input[readonly]').first()).toHaveValue(/orchestration\/events\//);
  });

  test('compliance exposes the audit log and the deployment posture', async ({ page }) => {
    await page.goto('/admin/compliance');
    await expect(page.getByText('Audit log')).toBeVisible();
    await expect(page.getByText('Deployment & data residency')).toBeVisible();
  });

  // Role management (ADR 0033, #742).
  const membersTable = (page: import('@playwright/test').Page) =>
    page
      .getByRole('main')
      .locator('table')
      .filter({ has: page.getByRole('columnheader', { name: 'Role', exact: true }) });

  const memberRow = (page: import('@playwright/test').Page, email: string) =>
    membersTable(page).locator('tr').filter({ hasText: email }).first();

  async function chooseRole(page: import('@playwright/test').Page, role: string) {
    await page
      .locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')
      .locator('.ant-select-item-option-content')
      .filter({ hasText: new RegExp(`^${role}$`) })
      .click();
  }

  test('edits a member’s workspace role and reflects the server’s answer', async ({ page }) => {
    await page.goto('/admin/members');
    // `analyst@dataq.local` — the demo seed's non-admin member
    // (`scripts/demo_data.py`).
    const row = memberRow(page, 'analyst@dataq.local');
    await expect(row).toBeVisible();

    await row.getByRole('combobox').click();
    await chooseRole(page, 'viewer');
    await expect(page.getByText(/is now viewer/i)).toBeVisible();

    // Reload to prove it PERSISTED, rather than merely rendering an optimistic
    // local update — and that the deep link comes back to the same tab.
    await page.reload();
    const reloaded = memberRow(page, 'analyst@dataq.local');
    await expect(reloaded).toContainText('viewer');

    // Put it back, so the spec is re-runnable against a persistent stack.
    await reloaded.getByRole('combobox').click();
    await chooseRole(page, 'member');
    await expect(page.getByText(/is now member/i)).toBeVisible();
  });

  test('refuses to change the dev-bypass identity’s role, with a reason', async ({ page }) => {
    // It is force-written to admin on every request (#741), so accepting the change would 200 and
    // silently revert.
    await page.goto('/admin/members');
    const row = memberRow(page, 'dev-bypass@dataq.local');
    await expect(row).toBeVisible();

    await row.getByRole('combobox').click();
    await chooseRole(page, 'viewer');

    await expect(page.getByText(/dev-bypass identity/i)).toBeVisible();
    await expect(row).toContainText('admin');
  });
});
