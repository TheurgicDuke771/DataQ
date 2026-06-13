import { expect, test } from '@playwright/test';

// Seeded runs / results / pipeline-runs (backend/scripts/demo_data.py) read
// through the real API and rendered on the in-app Results page (ADR 0018 — the
// suite-scoped, redaction-aware surface, not Grafana). The seed lands, on the
// "Orders quality" suite, two succeeded runs — a pass/pass/warn/fail severity
// spread (seed:run:succeeded) and an operational-spectrum run with
// critical/error/skip (seed:run:mixed) — plus a terminal-failed run, and two
// monitored pipeline runs.
test.describe('Results page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/results');
    await expect(page.getByRole('heading', { name: 'Results', level: 3 })).toBeVisible();
  });

  test('lists seeded runs and drills into the severity-spread run (pass/warn/fail)', async ({
    page,
  }) => {
    // The seeded runs resolve to the suite name; the run status tags render.
    await expect(page.getByText('Orders quality').first()).toBeVisible();
    await expect(page.getByText('succeeded').first()).toBeVisible();
    await expect(page.getByText('failed').first()).toBeVisible();

    // Target the severity-spread run by its "Triggered by" marker (there are two
    // succeeded runs now — this one and the operational-spectrum run).
    await page
      .locator('tr.ant-table-row')
      .filter({ hasText: 'seed:run:succeeded' })
      .first()
      .click();
    const drawer = page.getByRole('dialog');
    await expect(drawer.getByText('order_id not null')).toBeVisible();
    await expect(drawer.getByText('amount in range')).toBeVisible();
    await expect(drawer.getByText('expect_column_values_to_be_between').first()).toBeVisible();
    // The warn + fail severity tiers from the seeded spread are visible.
    await expect(drawer.getByText('warn').first()).toBeVisible();
    await expect(drawer.getByText('fail').first()).toBeVisible();
  });

  test('drills into the operational-spectrum run (critical / error / skip)', async ({ page }) => {
    // The second succeeded run carries the operational vocabulary the first
    // doesn't: a critical breach, an error (evaluation threw), and a skip.
    await page.locator('tr.ant-table-row').filter({ hasText: 'seed:run:mixed' }).first().click();
    const drawer = page.getByRole('dialog');
    await expect(drawer.getByText('status in set')).toBeVisible();
    await expect(drawer.getByText('critical').first()).toBeVisible();
    await expect(drawer.getByText('error').first()).toBeVisible();
    await expect(drawer.getByText('skip').first()).toBeVisible();
  });

  test('shows the orchestration pipeline-runs monitoring feed', async ({ page }) => {
    await page.getByRole('tab', { name: 'Pipeline runs' }).click();

    // Both seeded pipeline runs (ADF succeeded, Airflow failed) are listed.
    await expect(page.getByText('daily_orders_load')).toBeVisible();
    await expect(page.getByText('events_streaming')).toBeVisible();
    await expect(page.getByText('upstream source timed out')).toBeVisible();
  });
});
