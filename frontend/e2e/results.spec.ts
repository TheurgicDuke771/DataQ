import { expect, test } from '@playwright/test';

// Seeded runs / results / pipeline-runs (backend/scripts/demo_data.py) read
// through the real API and rendered on the in-app Results page (ADR 0018 — the
// suite-scoped, redaction-aware surface, not Grafana). The seed lands, on the
// "Orders quality" suite, two succeeded runs — a pass/pass/warn/fail severity
// spread (seed:run:succeeded) and an operational-spectrum run with
// critical/error/skip (seed:run:mixed) — plus a terminal-failed run, and three
// monitored pipeline runs (the third carrying a full-length ADF error).
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
    // Scoped to the interactive region (#345): the print-only PDF report
    // renders a parallel copy of the check names/statuses (hidden outside a
    // print context, but still in the DOM), which makes an unscoped
    // page.getByText ambiguous under Playwright's strict-mode locators.
    const runDetail = page.getByTestId('rd-screen');
    await expect(runDetail.getByText('order_id not null')).toBeVisible();
    await expect(runDetail.getByText('amount in range')).toBeVisible();
    await expect(runDetail.getByText('expect_column_values_to_be_between').first()).toBeVisible();
    // The warn + fail severity tiers from the seeded spread are visible.
    await expect(runDetail.getByText('warn').first()).toBeVisible();
    await expect(runDetail.getByText('fail').first()).toBeVisible();
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
    // Scoped to the interactive region (#345) — see the comment in the
    // previous test.
    const runDetail = page.getByTestId('rd-screen');
    await expect(runDetail.getByText('status in set')).toBeVisible();
    await expect(runDetail.getByText('critical').first()).toBeVisible();
    await expect(runDetail.getByText('error').first()).toBeVisible();
    await expect(runDetail.getByText('skip').first()).toBeVisible();
  });

  test('shows the orchestration pipeline-runs monitoring feed', async ({ page }) => {
    await page.getByRole('tab', { name: 'Pipeline runs' }).click();

    // All three seeded pipeline runs (ADF succeeded, Airflow failed, ADF failed
    // with a full-length provider error) are listed.
    await expect(page.getByText('daily_orders_load')).toBeVisible();
    await expect(page.getByText('events_streaming')).toBeVisible();
    await expect(page.getByText('hourly_payments_load')).toBeVisible();
    await expect(page.getByText('upstream source timed out')).toBeVisible();
  });

  // #1282: the column `width` + `ellipsis` that #1185/#1208 shipped were inert
  // — `scroll={{ x: 'max-content' }}` sizes the table from its content, which
  // neuters `table-layout: fixed` and demotes the colgroup width to a hint, so
  // a long failure reason rendered at ~1900px with no ellipsis in production.
  //
  // This assertion has to live in Playwright: jsdom performs no layout, so the
  // Vitest suite happily confirmed the ellipsis CLASS was applied while the
  // bound did nothing. Measure the rendered box, not the props.
  test('bounds a long failure reason instead of stretching the table', async ({ page }) => {
    await page.getByRole('tab', { name: 'Pipeline runs' }).click();

    const row = page.locator('tr.ant-table-row').filter({ hasText: 'hourly_payments_load' });
    await expect(row).toHaveCount(1);
    const text = row.getByText(/Operation on target LoadPaymentsToSnowflake failed/);
    await expect(text).toBeVisible();

    const box = await text.evaluate((el) => ({
      rendered: el.getBoundingClientRect().width,
      // Overflowing content is what makes text-overflow: ellipsis actually
      // paint an ellipsis; equal widths mean the text fit, i.e. no bound.
      full: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));

    // `boundedTextStyle(260)` from ellipsisColumn('Failure reason', …, 260).
    expect(box.rendered).toBeLessThanOrEqual(260);
    expect(box.full).toBeGreaterThan(box.clientWidth);

    // …and the table itself stays near the viewport rather than being dragged
    // out to the length of the error string (the user-visible symptom: an
    // unusably long horizontal scrollbar). Identified by its own column header
    // rather than by position: `.ant-table table` unscoped matches the hidden
    // Runs table first and measures 0, which passes this assertion no matter
    // what the pipeline table does — a check that cannot fail is worse than no
    // check.
    const table = page
      .locator('.ant-table table')
      .filter({ has: page.getByRole('columnheader', { name: 'Failure reason' }) });
    const tableWidth = await table.evaluate((el) => el.getBoundingClientRect().width);
    expect(tableWidth).toBeGreaterThan(0);
    const viewport = page.viewportSize();
    expect(tableWidth).toBeLessThan((viewport?.width ?? 1280) * 1.5);
  });

  // The run-detail Observed column is the other half of #1282, and the half the
  // fix's own argument says only a browser can validate — so it gets its own
  // measurement rather than riding on the pipeline-runs one. The seeded `error`
  // result carries a full-length connector error for exactly this.
  test('bounds a long observed_value on the run-detail page', async ({ page }) => {
    await page.locator('tr.ant-table-row').filter({ hasText: 'seed:run:mixed' }).first().click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);

    // Scoped to the interactive region (#345): the print-only RunReport renders
    // a parallel, unbounded copy of the same payload.
    //
    // Targets the BOUNDED span, not the text: `getByText` resolves to the
    // innermost match, which here is ScalarValue's inline `<code>` — its
    // scrollWidth/clientWidth are both 0 and its rect is the full text width, so
    // measuring it would fail even with the bound working. Matching on the bound
    // itself also means removing the bound makes this test fail (no match)
    // rather than silently measure something else.
    const observed = page
      .getByTestId('rd-screen')
      .locator('td span[style*="max-width"]')
      .filter({ hasText: /snowflake\.connector\.errors\.ProgrammingError/ });
    await expect(observed).toBeVisible();

    const box = await observed.evaluate((el) => ({
      rendered: el.getBoundingClientRect().width,
      full: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));

    // `OBSERVED_COLUMN_WIDTH` in src/pages/RunDetail.tsx — restated rather than
    // imported, to keep the React page out of this process.
    expect(box.rendered).toBeLessThanOrEqual(220);
    expect(box.full).toBeGreaterThan(box.clientWidth);
  });
});
