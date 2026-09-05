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
    for (const card of ['Members', 'Suites', 'Open incidents', 'Runs today']) {
      await expect(main.getByText(card, { exact: true }).first()).toBeVisible();
    }
    // The seed has no invite record, so this must read as untracked, not as zero.
    await expect(main.getByText('Pending first sign-in is not tracked yet')).toBeVisible();
    await expect(main.getByText(/across \d+ connection\(s\)/)).toBeVisible();
  });

  test('overview states every health signal, including the ones nothing watches', async ({
    page,
  }) => {
    await page.goto('/admin/overview');
    const main = page.getByRole('main');

    await expect(main.getByText('Needs attention')).toBeVisible();
    await expect(main.getByText('Workspace health')).toBeVisible();
    for (const item of [
      'Audit chain',
      'Scheduler & worker',
      'Secret store',
      'Orchestration polling',
    ]) {
      await expect(main.getByText(item, { exact: true })).toBeVisible();
    }
    // Never verified on load — the same rule the compliance card follows.
    await expect(main.getByText('Not verified this session')).toBeVisible();

    // The seeded stack never polls its orchestration connections, so at least one row must
    // be present and it must say "not monitored" rather than being quietly absent.
    const feedRow = main
      .getByText(/polling not monitored|never observed|never run|Queue depth unknown/)
      .first();
    await expect(feedRow).toBeVisible();
    // Every signal carries a verb that goes somewhere.
    await expect(
      main
        .getByRole('link', { name: /View connection|View connections|Details|View suites/ })
        .first(),
    ).toBeVisible();
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
    // `exact`: the data-subject card below also mentions the audit log in prose.
    await expect(page.getByText('Audit log', { exact: true })).toBeVisible();
    await expect(page.getByText('Deployment & data residency')).toBeVisible();
  });

  test('verifies the audit chain on demand, and not before', async ({ page }) => {
    await page.goto('/admin/compliance');
    // The check walks the whole hashed set, so it must not fire on load.
    await expect(page.getByText('Not verified this session')).toBeVisible();

    await page.getByRole('button', { name: 'Verify now' }).click();
    // A seeded stack has hashed rows, so this is 'Intact'; a chain-less one is
    // 'Nothing to verify'. Either is a real answer — 'Not verified' is not.
    await expect(page.getByText(/^(Intact|Nothing to verify|Broken)$/).first()).toBeVisible();
    await expect(page.getByText('Events in chain')).toBeVisible();
  });

  test('answers a data-subject request and gates erasure behind the typed value', async ({
    page,
  }) => {
    await page.goto('/admin/compliance');
    await expect(page.getByText('Data-subject rights (GDPR / CCPA)')).toBeVisible();

    // Both verbs stay disabled until a subject is actually named.
    await expect(page.getByRole('button', { name: 'Export data' })).toBeDisabled();

    const subject = 'nobody-e2e@example.invalid';
    await page.getByPlaceholder('e.g. email').fill('email');
    await page.getByPlaceholder('e.g. alice@example.com').fill(subject);

    await page.getByRole('button', { name: 'Export data' }).click();
    await expect(page.getByText('Export receipt')).toBeVisible();
    // The seed captures no per-column sample rows, so the honest empty answer is
    // the expected one — and it is the one that must not read as a clean bill of
    // health for the warehouse.
    await expect(page.getByText('No captured data matches this subject')).toBeVisible();
    // `.last()`: antd's own dismiss "X" carries aria-label="Close" too, so the
    // footer button is the second match.
    await page.getByRole('button', { name: 'Close' }).last().click();

    // Erasure is only exercised as far as its gate: the seed has no disposable
    // subject, and erasure is irreversible.
    await page.getByRole('button', { name: 'Erase subject' }).click();
    const confirm = page.getByRole('button', { name: 'Erase permanently' });
    await expect(confirm).toBeDisabled();
    await page.getByLabel('Type the subject value to confirm').fill(subject.slice(0, -1));
    await expect(confirm).toBeDisabled();
    await page.getByLabel('Type the subject value to confirm').fill(subject);
    await expect(confirm).toBeEnabled();

    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByText('Erasure receipt')).toBeHidden();
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
