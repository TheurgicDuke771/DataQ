import { expect, test } from '@playwright/test';

// SchedulesPanel (A7) on the seeded suite's detail page: full authoring
// round-trip (add → pause → delete) plus the server-side cron validation
// path (422 → error toast, no row). The dispatcher itself (60s beat) is
// backend-tested; this proves the UI a user actually drives.
test.describe('Suite schedules panel', () => {
  // Distinctive cron so leftovers from an aborted local run can't collide
  // with the seeded data; the test deletes it on the way out.
  const CRON = '7 3 * * 2';

  const card = (page: import('@playwright/test').Page) =>
    page.locator('.ant-card').filter({ hasText: 'cron cadence' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    await expect(card(page).getByText('Schedules', { exact: true })).toBeVisible();
  });

  test('rejects an invalid cron with an error toast and no row', async ({ page }) => {
    const panel = card(page);
    await panel.getByLabel('Cron expression').fill('not a cron');
    await panel.getByRole('button', { name: 'Add' }).click();

    // Server-side validation (422) surfaces as the panel's error toast.
    await expect(page.getByText(/Add failed/)).toBeVisible();
    await expect(panel.locator('tr.ant-table-row').filter({ hasText: 'not a cron' })).toHaveCount(
      0,
    );
  });

  test('add a schedule, pause it, then delete it', async ({ page }) => {
    const panel = card(page);

    // Add (timezone stays the UTC default).
    await panel.getByLabel('Cron expression').fill(CRON);
    await panel.getByRole('button', { name: 'Add' }).click();
    const row = panel.locator('tr.ant-table-row').filter({ hasText: CRON });
    await expect(row).toHaveCount(1);
    // The dispatcher precomputed the next run; the cell is populated.
    await expect(row.getByText(/\d{4}|\d{2}:\d{2}/).first()).toBeVisible();

    // Pause via the status switch — toast carries the cron (timezone) label.
    await row.getByRole('switch').click();
    await expect(page.getByText(`${CRON} (UTC): paused`)).toBeVisible();

    // Delete → confirm modal → the row disappears.
    await row.getByRole('button', { name: `Remove ${CRON} (UTC)` }).click();
    const confirm = page.getByRole('dialog', { name: /^Delete schedule/ });
    await confirm.getByRole('button', { name: 'Delete' }).click();
    await expect(panel.locator('tr.ant-table-row').filter({ hasText: CRON })).toHaveCount(0);
  });
});
