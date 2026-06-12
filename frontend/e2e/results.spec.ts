import { expect, test } from '@playwright/test';

// Seeded runs / results / pipeline-runs (backend/scripts/demo_data.py) read
// through the real API and rendered on the in-app Results page (ADR 0018 — the
// suite-scoped, redaction-aware surface, not Grafana). The seed lands a
// succeeded run with a pass/pass/warn/fail spread plus a failed run on the
// "Orders quality" suite, and two monitored pipeline runs.
test.describe('Results page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/results');
    await expect(page.getByRole('heading', { name: 'Results', level: 3 })).toBeVisible();
  });

  test('lists seeded runs and drills into a run’s results', async ({ page }) => {
    // Both seeded runs resolve to the suite name; the status tags render.
    await expect(page.getByText('Orders quality').first()).toBeVisible();
    await expect(page.getByText('succeeded').first()).toBeVisible();
    await expect(page.getByText('failed').first()).toBeVisible();

    // Open the succeeded run → its detail drawer shows per-check results,
    // mapping check ids to names with severity tags (pass/warn/fail).
    await page.locator('tr.ant-table-row').filter({ hasText: 'succeeded' }).first().click();
    const drawer = page.getByRole('dialog');
    await expect(drawer.getByText('order_id not null')).toBeVisible();
    await expect(drawer.getByText('amount in range')).toBeVisible();
    await expect(drawer.getByText('expect_column_values_to_be_between').first()).toBeVisible();
    // The warn + fail severity tiers from the seeded spread are visible.
    await expect(drawer.getByText('warn').first()).toBeVisible();
    await expect(drawer.getByText('fail').first()).toBeVisible();
  });

  test('shows the orchestration pipeline-runs monitoring feed', async ({ page }) => {
    await page.getByRole('tab', { name: 'Pipeline runs' }).click();

    // Both seeded pipeline runs (ADF succeeded, Airflow failed) are listed.
    await expect(page.getByText('daily_orders_load')).toBeVisible();
    await expect(page.getByText('events_streaming')).toBeVisible();
    await expect(page.getByText('upstream source timed out')).toBeVisible();
  });
});
