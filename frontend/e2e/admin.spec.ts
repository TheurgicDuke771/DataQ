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

  // Workspace membership (ADR 0043, #1693).
  const membershipTable = (page: import('@playwright/test').Page) =>
    page
      .getByRole('main')
      .locator('table')
      .filter({ has: page.getByRole('columnheader', { name: 'Source', exact: true }) });

  test('admits a member, shows it pending, then removes it', async ({ page }) => {
    await page.goto('/admin/members');
    const email = `e2e-${Date.now()}@dataq.local`;

    await expect(page.getByText('Workspace membership')).toBeVisible();
    await page.getByRole('button', { name: 'Add member' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByPlaceholder('person@example.com').fill(email);
    await dialog.getByRole('button', { name: 'Add', exact: true }).click();

    // Admitted but never signed in — not a failure state, and the table says so.
    const row = membershipTable(page).locator('tr').filter({ hasText: email }).first();
    await expect(row).toBeVisible();
    await expect(row).toContainText('pending first sign-in');

    // Reload proves it PERSISTED, and — the point of this spec — that writing the
    // first managed member did not lock this dev-bypass lane out of its own app.
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Admin', level: 3 })).toBeVisible();
    const reloaded = membershipTable(page).locator('tr').filter({ hasText: email }).first();
    await expect(reloaded).toBeVisible();

    // Put it back, so the spec is re-runnable against a persistent stack.
    await reloaded.getByRole('button', { name: 'Remove' }).click();
    await page.getByRole('button', { name: 'Remove member' }).click();
    await expect(membershipTable(page).locator('tr').filter({ hasText: email })).toHaveCount(0);
  });

  test('the switch-on import is surfaced for review, not left to be discovered', async ({
    page,
  }) => {
    await page.goto('/admin/members');
    // The first add imported every existing user provisionally; the banner names
    // them, and the dev-bypass identity is among them.
    await expect(page.getByText(/Review \d+ imported member/)).toBeVisible();
    await expect(page.getByText('dev-bypass@dataq.local').first()).toBeVisible();
  });
});

// Workspace-admin suite writes (#1698). Everything here acts on a suite the spec
// creates itself over the API — the seeded suites carry the rest of the E2E lane's
// expectations, and two of these three verbs are irreversible.
test.describe('Admin suite writes', () => {
  const ANALYST = 'analyst@dataq.local';

  async function seedSuite(page: import('@playwright/test').Page) {
    const suiteName = `e2e-admin-writes-${Date.now()}`;
    const connections = await (await page.request.get('/api/v1/connections')).json();
    const datasource = connections.find(
      (c: { type: string }) => !['adf', 'airflow', 'dbt'].includes(c.type),
    );
    expect(datasource, 'the seeded stack has a datasource connection').toBeTruthy();
    const created = await page.request.post('/api/v1/suites', {
      data: { name: suiteName, connection_id: datasource.id },
    });
    expect(created.ok()).toBe(true);
    const suite = await created.json();

    const users = await (await page.request.get('/api/v1/admin/users')).json();
    const analyst = users.find((u: { email: string }) => u.email === ANALYST);
    expect(analyst, 'the demo seed provisions the analyst').toBeTruthy();
    return { suiteId: suite.id as string, suiteName, analystId: analyst.id as string };
  }

  test('revokes a per-suite grant the admin never owned', async ({ page }) => {
    const { suiteId, suiteName, analystId } = await seedSuite(page);
    const shared = await page.request.post(`/api/v1/suites/${suiteId}/shares`, {
      data: { user_id: analystId, permission: 'view' },
    });
    expect(shared.ok()).toBe(true);

    await page.goto('/admin/members');
    const row = page
      .getByRole('main')
      .locator('tr')
      .filter({ hasText: suiteName })
      .filter({ hasText: ANALYST });
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Revoke' }).click();
    await page.locator('.ant-popconfirm').getByRole('button', { name: 'Revoke' }).click();

    // Reload to prove the grant is gone from the SERVER, not just this render.
    await page.reload();
    await expect(
      page.getByRole('main').locator('tr').filter({ hasText: suiteName }).filter({
        hasText: ANALYST,
      }),
    ).toHaveCount(0);

    await page.request.delete(`/api/v1/admin/suites/${suiteId}`);
  });

  test('transfers ownership and then deletes the suite behind a typed confirmation', async ({
    page,
  }) => {
    const { suiteId, suiteName } = await seedSuite(page);

    await page.goto('/admin/suites');
    const row = page.getByRole('main').locator('tr').filter({ hasText: suiteName });
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Transfer' }).click();
    // Keyboard selection: rc-virtual-list parks options off-viewport, so clicking
    // an option by role is flaky in this lane (see notifications.spec.ts).
    const picker = page.getByRole('dialog').getByRole('combobox');
    await picker.fill('analyst');
    await expect(page.getByText(new RegExp(ANALYST))).toBeVisible();
    await picker.press('Enter');
    await page.getByRole('dialog').getByRole('button', { name: 'Transfer' }).click();

    await page.reload();
    await expect(page.getByRole('main').locator('tr').filter({ hasText: suiteName })).toContainText(
      ANALYST,
    );

    // Delete — on the suite this spec created, never a seeded one.
    const afterTransfer = page.getByRole('main').locator('tr').filter({ hasText: suiteName });
    await afterTransfer.getByRole('button', { name: 'Delete' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(/This cannot be undone/)).toBeVisible();
    const confirm = dialog.getByRole('button', { name: 'Delete' });
    await expect(confirm).toBeDisabled();
    await dialog.getByLabel('Suite name confirmation').fill(suiteName);
    await expect(confirm).toBeEnabled();
    await confirm.click();

    await page.reload();
    await expect(page.getByRole('main').locator('tr').filter({ hasText: suiteName })).toHaveCount(
      0,
    );
    expect((await page.request.get(`/api/v1/suites/${suiteId}`)).status()).toBe(404);
  });
});
