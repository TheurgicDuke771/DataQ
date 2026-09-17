import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  type AxeViolationLike,
  diffNew,
  filterGated,
  formatViolations,
  loadBaseline,
  saveBaseline,
  toRecords,
} from '../scripts/a11y/ratchet';

// Automated a11y floor, Playwright half (#1670 item 1): scans the app's main routes with
// axe-core (via @axe-core/playwright) under the real dev-bypass stack this lane already
// runs against (see e2e/README.md), and ratchets against the committed baseline
// (frontend/a11y-baseline.json, shared with the Vitest component lane in
// tests/a11y/components.a11y.test.tsx) rather than requiring every existing violation to
// be fixed first. Fixing what the baseline records is tracked separately — see the
// follow-up issue filed alongside this PR.
//
// Regenerate deliberately with `A11Y_BASELINE=1 pnpm a11y:baseline` (never by hand-editing
// the JSON) when a route's rendered markup changes on purpose.

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASELINE_PATH = resolve(__dirname, '../a11y-baseline.json');
const CAPTURE = process.env.A11Y_BASELINE === '1';

/** Run axe over the current page, ratchet against (or capture into) the baseline for
 * `surface`. `CAPTURE` mode load-modify-saves the shared file per call, so the regen
 * invocation must run this spec with `--workers=1` (documented in the `a11y:baseline`
 * script) to avoid a write race across routes. */
async function checkRoute(page: import('@playwright/test').Page, surface: string): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const gated = filterGated(results.violations as AxeViolationLike[]);
  const records = toRecords(surface, gated);

  if (CAPTURE) {
    const rest = loadBaseline(BASELINE_PATH).filter((r) => r.surface !== surface);
    saveBaseline(BASELINE_PATH, [...rest, ...records]);
    return;
  }

  const baseline = loadBaseline(BASELINE_PATH).filter((r) => r.surface === surface);
  const newViolations = diffNew(records, baseline);
  expect(
    newViolations,
    newViolations.length > 0
      ? `${surface}: NEW serious/critical a11y violation(s) not in frontend/a11y-baseline.json:\n` +
          formatViolations(newViolations)
      : undefined,
  ).toEqual([]);
}

test.describe('Accessibility floor (axe-core, serious/critical, ratcheted)', () => {
  test('dashboard', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard', level: 3 })).toBeVisible();
    await checkRoute(page, 'route:/dashboard');
  });

  test('connections', async ({ page }) => {
    await page.goto('/connections');
    await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();
    await checkRoute(page, 'route:/connections');
  });

  test('suites list', async ({ page }) => {
    await page.goto('/suites');
    await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
    await checkRoute(page, 'route:/suites');
  });

  test('suite detail', async ({ page }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    await expect(page.getByRole('heading', { name: 'Orders quality', level: 4 })).toBeVisible();
    await checkRoute(page, 'route:/suites/:id');
  });

  test('results', async ({ page }) => {
    await page.goto('/results');
    await expect(page.getByRole('heading', { name: 'Results', level: 3 })).toBeVisible();
    await expect(page.locator('tr.ant-table-row').first()).toBeVisible();
    await checkRoute(page, 'route:/results');
  });

  test('run detail', async ({ page }) => {
    await page.goto('/results');
    await page.locator('tr.ant-table-row').first().click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await expect(page.getByTestId('rd-screen')).toBeVisible();
    await checkRoute(page, 'route:/results/:id');
  });

  test('assets', async ({ page }) => {
    await page.goto('/assets');
    await expect(page.getByRole('heading', { name: 'Assets', level: 3 })).toBeVisible();
    await checkRoute(page, 'route:/assets');
  });

  test('admin', async ({ page }) => {
    // dev-bypass is workspace-admin in this lane (CI + compose parity, see e2e/README.md).
    await page.goto('/admin');
    await expect(page.getByRole('heading', { name: 'Admin', level: 3 })).toBeVisible();
    await checkRoute(page, 'route:/admin');
  });
});
