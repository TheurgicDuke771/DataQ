import AxeBuilder from '@axe-core/playwright';
import { type APIRequestContext, expect, type Page, test } from '@playwright/test';

import { settle } from '../scripts/a11y/settle';

// The seasonal calendar (Admin → Calendar) against the real API, and a per-period anomaly
// check's trend. Runs and results cannot be produced without a warehouse, so the trend's
// history, baseline and detected periods are answered by `page.route`; what is under test is
// the trend drawing them in a real layout.

const API = '/api/v1';

async function suiteId(request: APIRequestContext): Promise<string> {
  const suites = (await (await request.get(`${API}/suites`)).json()) as {
    id: string;
    name: string;
  }[];
  const suite = suites.find((s) => s.name === 'Orders quality');
  if (!suite) throw new Error('seeded suite "Orders quality" not found');
  return suite.id;
}

async function addEntry(request: APIRequestContext, name: string): Promise<string> {
  const res = await request.post(`${API}/admin/seasonal-periods`, {
    data: { name, kind: 'recurring', start_month: 11, start_day: 27, end_month: 11, end_day: 30 },
  });
  expect(res.status()).toBe(201);
  return ((await res.json()) as { id: string }).id;
}

async function addPerPeriodCheck(
  request: APIRequestContext,
  suite: string,
  name: string,
  attach: string[],
): Promise<string> {
  const res = await request.post(`${API}/suites/${suite}/checks`, {
    data: {
      name,
      kind: 'anomaly',
      expectation_type: 'monitor:anomaly',
      config: {
        target_metric: 'period_row_count',
        date_column: 'order_date',
        yearly: true,
        seasonal_periods: attach,
      },
      fail_threshold: 3,
    },
  });
  expect(res.status()).toBe(201);
  return ((await res.json()) as { id: string }).id;
}

/** No serious or critical axe violation inside `selector` (the route scans see neither the
 *  filled calendar table nor the trend drawer). */
async function expectAccessible(page: Page, selector: string) {
  await settle(page);
  const results = await new AxeBuilder({ page }).include(selector).analyze();
  const bad = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(', ')}`)).toEqual([]);
}

/** antd Select: pick the option `steps` below the first, by keyboard (see e2e/README.md). */
async function pickByKeyboard(page: Page, label: string, steps: number) {
  await page.getByLabel(label).click();
  for (let i = 0; i < steps; i += 1) await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
}

test.describe('Seasonal calendar', () => {
  test('add, edit and delete an entry', async ({ page }) => {
    const name = `e2e season ${Date.now()}`;
    await page.goto('/admin/calendar');
    await expect(page.getByRole('tab', { name: 'Calendar' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await page.getByRole('button', { name: /New entry/ }).click();
    const dialog = page.getByRole('dialog', { name: 'New calendar entry' });
    await dialog.getByLabel('Name').fill(name);
    await pickByKeyboard(page, 'From month', 0);
    await dialog.getByLabel('From day').fill('5');
    await pickByKeyboard(page, 'To month', 0);
    await dialog.getByLabel('To day').fill('10');
    await dialog.getByRole('button', { name: 'Add' }).click();

    const row = page.locator('tr').filter({ hasText: name });
    await expect(row).toContainText('5 Jan – 10 Jan, every year');

    await page.getByRole('button', { name: `Edit ${name}` }).click();
    const edit = page.getByRole('dialog', { name: `Edit “${name}”` });
    await edit.getByLabel('Note (optional)').fill('winter clearance');
    await edit.getByRole('button', { name: 'Save' }).click();
    await expect(row).toContainText('winter clearance');

    await page.getByRole('button', { name: `Delete ${name}` }).click();
    await page
      .getByRole('dialog', { name: /^Delete/ })
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(page.locator('tr').filter({ hasText: name })).toHaveCount(0);
  });

  test('a refused delete names the check that still attaches the entry', async ({
    page,
    request,
  }) => {
    const stamp = Date.now();
    const entry = await addEntry(request, `e2e attached ${stamp}`);
    const suite = await suiteId(request);
    const check = await addPerPeriodCheck(request, suite, `e2e per day ${stamp}`, [entry]);
    try {
      await page.goto('/admin/calendar');
      await page.getByRole('button', { name: `Delete e2e attached ${stamp}` }).click();
      await page
        .getByRole('dialog', { name: /^Delete/ })
        .getByRole('button', { name: 'Delete' })
        .click();
      const blocked = page.getByTestId('calendar-delete-blocked');
      await expect(blocked).toContainText('is still attached to 1 check');
      await expectAccessible(page, 'main');
      await expect(blocked.getByRole('link', { name: `e2e per day ${stamp}` })).toHaveAttribute(
        'href',
        `/suites/${suite}/checks/${check}/edit`,
      );
    } finally {
      await request.delete(`${API}/suites/${suite}/checks/${check}`);
      await request.delete(`${API}/admin/seasonal-periods/${entry}`);
    }
  });

  test('the trend draws the expected value, the calendar and detected periods', async ({
    page,
    request,
  }) => {
    const stamp = Date.now();
    const entryName = `e2e black friday ${stamp}`;
    const entry = await addEntry(request, entryName);
    const suite = await suiteId(request);
    const check = await addPerPeriodCheck(request, suite, `e2e trend ${stamp}`, [entry]);
    const day = (i: number) => new Date(Date.UTC(2026, 10, 20 + i)).toISOString();
    try {
      await page.route(`**${API}/suites/*/checks/${check}/baseline`, (route) =>
        route.fulfill({
          json: {
            kind: 'anomaly',
            captured_at: day(0),
            baseline: {
              version: 1,
              target_metric: 'period_row_count',
              window: 14,
              observations: Array.from({ length: 12 }, (_, i) => ({
                ts: day(i),
                value: i >= 7 && i <= 10 ? 5000 + i : 1000 + i,
                ...(i < 6 ? { source: 'backfilled' } : {}),
              })),
            },
          },
        }),
      );
      await page.route(`**${API}/suites/*/checks/${check}/history**`, (route) =>
        route.fulfill({
          json: [6, 7, 8].map((i) => ({
            run_id: `00000000-0000-4000-8000-00000000000${i}`,
            status: 'pass',
            metric_value: 0.3,
            created_at: day(i + 1),
            seasonal: {
              period_start: day(i).slice(0, 10),
              mean: i === 6 ? 1003 : 5050,
              stddev: i === 6 ? 10 : 120,
              yearly_applied: i !== 6,
              yearly_reason: i === 6 ? 'no_earlier_year' : null,
              expected: i === 6 ? null : 5000,
              declared_name: i === 6 ? null : entryName,
              banded: true,
              verdict_changed: i === 7,
            },
          })),
        }),
      );
      await page.route(`**${API}/suites/*/checks/${check}/seasonal-periods/detected`, (route) =>
        route.fulfill({
          json: {
            reason: null,
            periods: [
              {
                kind: 'seen_once',
                start: day(7).slice(0, 10),
                end: day(10).slice(0, 10),
                peak_ratio: 5,
                years_seen: 1,
                range: null,
                occurrences: [
                  {
                    year: 2026,
                    start: day(7).slice(0, 10),
                    end: day(10).slice(0, 10),
                    peak_ratio: 5,
                    periods: 4,
                    backfilled_periods: 0,
                  },
                ],
                declared: [],
              },
            ],
            thresholds: { ratio: 1.5, min_consecutive: 2, same_time_days: 7, level: 'median' },
            history: {
              unit: 'day',
              points: 12,
              backfilled: 6,
              evaluated: 5,
              first: day(0).slice(0, 10),
              last: day(11).slice(0, 10),
              years: 0,
            },
          },
        }),
      );

      await page.goto(`/suites/${suite}/checks/${check}/edit`);
      await page.getByRole('button', { name: 'Trend' }).click();
      const drawer = page.getByRole('dialog', { name: `e2e trend ${stamp} — trend` });
      const caption = drawer.getByTestId('seasonal-caption');
      await expect(caption).toContainText('Where the yearly comparison applied (2 of 3)');
      await expect(caption).toContainText('no earlier year in the history (1)');
      await expect(caption).toContainText(entryName);
      // The expected value is its own dashed line, and the attached entry is shaded and named.
      await expect(drawer.locator('.recharts-line path[stroke-dasharray="6 3"]')).toHaveCount(1);
      await expect(drawer.locator('.recharts-reference-area')).not.toHaveCount(0);
      await expect(drawer.getByTestId('detected-periods')).toContainText('Seen once');
      await expect(drawer.getByRole('button', { name: 'Promote to calendar' })).toBeVisible();
      await expectAccessible(page, '.ant-drawer [role="dialog"]');

      await drawer.getByText('Table', { exact: true }).click();
      await expect(drawer.getByRole('columnheader', { name: 'Expected' })).toBeVisible();
      await expect(drawer.getByText('5050.00 ± 120.00').first()).toBeVisible();
      await expectAccessible(page, '.ant-drawer [role="dialog"]');
    } finally {
      await request.delete(`${API}/suites/${suite}/checks/${check}`);
      await request.delete(`${API}/admin/seasonal-periods/${entry}`);
    }
  });
});
