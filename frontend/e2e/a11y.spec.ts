import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  type AxeViolationLike,
  filterGated,
  newViolationsMessage,
  ratchet,
  toRecords,
} from '../scripts/a11y/ratchet';
import { settle } from '../scripts/a11y/settle';

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
  await settle(page);
  const results = await new AxeBuilder({ page }).options({ ancestry: true }).analyze();
  const gated = filterGated(results.violations as AxeViolationLike[]);
  const records = toRecords(surface, gated);
  const { newViolations } = ratchet(surface, records, { path: BASELINE_PATH, capture: CAPTURE });
  expect(newViolations, newViolationsMessage(surface, newViolations)).toEqual([]);
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

  // Every admin sub-page is its own route; `/admin` redirects to Overview.
  for (const sub of ['overview', 'members', 'suites', 'integrations', 'settings', 'compliance']) {
    test(`admin ${sub}`, async ({ page }) => {
      // dev-bypass is workspace-admin in this lane (CI + compose parity, see e2e/README.md).
      await page.goto(`/admin/${sub}`);
      await expect(page.getByRole('heading', { name: 'Admin', level: 3 })).toBeVisible();
      await checkRoute(page, `route:/admin/${sub}`);
    });
  }

  test('profile', async ({ page }) => {
    await page.goto('/profile');
    await expect(page.getByRole('heading', { name: 'Profile', level: 3 })).toBeVisible();
    await checkRoute(page, 'route:/profile');
  });

  test('not found', async ({ page }) => {
    await page.goto('/no-such-route');
    await expect(page.getByText('404')).toBeVisible();
    await checkRoute(page, 'route:/404');
  });

  // The authoring + detail routes are id-addressed; resolve the seeded ids through the API
  // rather than clicking through list pages the tests above already cover.
  test.describe('id-addressed routes', () => {
    let suiteId: string;
    let checkId: string;
    let assetId: string;
    let connectionId: string;

    test.beforeAll(async ({ request }) => {
      const suites: { id: string; name: string }[] = await (
        await request.get('/api/v1/suites')
      ).json();
      const orders = suites.find((s) => s.name === 'Orders quality');
      expect(orders, 'seeded "Orders quality" suite').toBeDefined();
      suiteId = orders?.id ?? '';
      const checks: { id: string }[] = await (
        await request.get(`/api/v1/suites/${suiteId}/checks`)
      ).json();
      checkId = checks[0].id;
      const assets: { id: string }[] = await (await request.get('/api/v1/assets')).json();
      assetId = assets[0].id;
      const connections: { id: string }[] = await (await request.get('/api/v1/connections')).json();
      connectionId = connections[0].id;
    });

    const routes: [string, () => string][] = [
      ['route:/connections/new', () => '/connections/new'],
      ['route:/connections/:id/edit', () => `/connections/${connectionId}/edit`],
      ['route:/suites/new', () => '/suites/new'],
      ['route:/suites/:id/edit', () => `/suites/${suiteId}/edit`],
      ['route:/suites/:id/checks/new', () => `/suites/${suiteId}/checks/new`],
      ['route:/suites/:id/checks/:id/edit', () => `/suites/${suiteId}/checks/${checkId}/edit`],
      ['route:/assets/:id', () => `/assets/${assetId}`],
    ];
    for (const [surface, url] of routes) {
      test(surface, async ({ page }) => {
        await page.goto(url());
        await expect(page.getByRole('heading').first()).toBeVisible();
        await checkRoute(page, surface);
      });
    }
  });
});
