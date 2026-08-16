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
    await expect(page.getByText('Apache Airflow', { exact: true })).toBeVisible();
    await expect(page.locator('input[readonly]').first()).toHaveValue(/orchestration\/events\//);
  });

  // Role management (ADR 0033, #742). Runs against the real backend, so the
  // last-admin guard and the dev-bypass refusal are exercised for real rather
  // than mocked — which is the point: both are server-side decisions the UI
  // deliberately does not attempt to predict.
  test('edits a member’s workspace role and reflects the server’s answer', async ({ page }) => {
    await page.goto('/admin');
    const main = page.getByRole('main');
    await expect(main.getByText('Members', { exact: true }).first()).toBeVisible();

    // `analyst@dataq.local` — the demo seed's non-admin member
    // (`scripts/demo_data.py`). Picked by its email cell, then the role select
    // within that row; indexing by position would silently follow a reorder.
    const row = main.locator('tr', { hasText: 'analyst@dataq.local' }).first();
    await expect(row).toBeVisible();

    const select = row.getByRole('combobox');
    await select.click();
    await page.getByTitle('viewer', { exact: true }).click();

    await expect(page.getByText(/is now viewer/i)).toBeVisible();
    // The row shows the SERVER's value — reload to prove it persisted rather
    // than merely rendering an optimistic local update.
    await page.reload();
    const reloaded = main.locator('tr', { hasText: 'analyst@dataq.local' }).first();
    await expect(reloaded.getByRole('combobox')).toContainText('viewer');

    // Put it back so the spec is re-runnable against a persistent stack.
    await reloaded.getByRole('combobox').click();
    await page.getByTitle('member', { exact: true }).click();
    await expect(page.getByText(/is now member/i)).toBeVisible();
  });

  test('refuses to change the dev-bypass identity’s role, with a reason', async ({ page }) => {
    // It is force-written to admin on every request (#741), so accepting the
    // change would 200 and silently revert. The server refuses; the UI must
    // surface WHY rather than a generic failure.
    await page.goto('/admin');
    const main = page.getByRole('main');
    const row = main.locator('tr', { hasText: 'dev-bypass@dataq.local' }).first();
    await expect(row).toBeVisible();

    await row.getByRole('combobox').click();
    await page.getByTitle('viewer', { exact: true }).click();

    await expect(page.getByText(/dev-bypass identity/i)).toBeVisible();
    await expect(row.getByRole('combobox')).toContainText('admin');
  });
});
