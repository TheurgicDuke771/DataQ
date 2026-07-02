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
    // succeeded runs now — this one and the operational-spectrum run). The row
    // deep-links to the routed run-detail page (ADR 0022 — the drawer is gone).
    await page
      .locator('tr.ant-table-row')
      .filter({ hasText: 'seed:run:succeeded' })
      .first()
      .click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await expect(page.getByText('order_id not null')).toBeVisible();
    await expect(page.getByText('amount in range')).toBeVisible();
    await expect(page.getByText('expect_column_values_to_be_between').first()).toBeVisible();
    // The warn + fail severity tiers from the seeded spread are visible.
    await expect(page.getByText('warn').first()).toBeVisible();
    await expect(page.getByText('fail').first()).toBeVisible();
  });

  test('expands the failed check to its redacted failing-value sample', async ({ page }) => {
    await page
      .locator('tr.ant-table-row')
      .filter({ hasText: 'seed:run:succeeded' })
      .first()
      .click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);

    // The seeded fail ("status in set") carries sample_failures; its tested
    // column (`status`) is not PII, so the redactor surfaces the raw failing
    // values (#226/#415/#417) instead of masking them.
    const row = page.locator('tr.ant-table-row').filter({ hasText: 'status in set' });
    await row.getByRole('button', { name: /expand/i }).click();
    await expect(page.getByText('unknwon')).toBeVisible();
    await expect(page.getByText('REFNDED')).toBeVisible();
  });

  test('drills into the operational-spectrum run (critical / error / skip)', async ({ page }) => {
    // The second succeeded run carries the operational vocabulary the first
    // doesn't: a critical breach, an error (evaluation threw), and a skip.
    await page.locator('tr.ant-table-row').filter({ hasText: 'seed:run:mixed' }).first().click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await expect(page.getByText('status in set')).toBeVisible();
    await expect(page.getByText('critical').first()).toBeVisible();
    await expect(page.getByText('error').first()).toBeVisible();
    await expect(page.getByText('skip').first()).toBeVisible();
  });

  test('shows the orchestration pipeline-runs monitoring feed', async ({ page }) => {
    await page.getByRole('tab', { name: 'Pipeline runs' }).click();

    // Both seeded pipeline runs (ADF succeeded, Airflow failed) are listed.
    await expect(page.getByText('daily_orders_load')).toBeVisible();
    await expect(page.getByText('events_streaming')).toBeVisible();
    await expect(page.getByText('upstream source timed out')).toBeVisible();
  });
});
