import AxeBuilder from '@axe-core/playwright';
import { expect, type Locator, type Page, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
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

/** The Member PAT `seed_dev` mints (same file `roles.spec.ts` reads) — the ambient dev-bypass
 *  identity is an admin, so it can never reach the Forbidden page. */
function memberToken(): string | null {
  try {
    const raw = readFileSync(resolve(__dirname, '.role-tokens.json'), 'utf8');
    return (JSON.parse(raw) as { member?: string }).member ?? null;
  } catch {
    return null;
  }
}
const MEMBER_TOKEN = memberToken();

/** Run axe over the current page, ratchet against (or capture into) the baseline for
 * `surface`. `CAPTURE` mode load-modify-saves the shared file per call, so the regen
 * invocation must run this spec with `--workers=1` (documented in the `a11y:baseline`
 * script) to avoid a write race across routes. */
type Theme = 'light' | 'dark';

/** What proves a route's data arrived: a locator for content that only renders once it has.
 * A heading is not enough (it renders before the data), and neither is `settle`, which a page
 * stuck on a fallback or a failed-fetch state passes too. `STATIC` is for a route that fetches
 * nothing its markup depends on. */
const STATIC = 'static' as const;
type Loaded = Locator | typeof STATIC;

async function checkRoute(theme: Theme, page: Page, route: string, loaded: Loaded): Promise<void> {
  if (loaded !== STATIC) await expect(loaded.first()).toBeVisible();
  await settle(page);
  // A dark scan that silently rendered light would pass for the wrong reason.
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  const surface = theme === 'dark' ? `${route}@dark` : route;
  const results = await new AxeBuilder({ page }).options({ ancestry: true }).analyze();
  const gated = filterGated(results.violations as AxeViolationLike[]);
  const records = toRecords(surface, gated);
  const { newViolations } = ratchet(surface, records, { path: BASELINE_PATH, capture: CAPTURE });
  expect(newViolations, newViolationsMessage(surface, newViolations)).toEqual([]);
}

for (const mode of ['light', 'dark'] as const) defineScans(mode);

function defineScans(mode: Theme): void {
  test.describe(`Accessibility floor (axe-core, serious/critical, ratcheted) [${mode}]`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((m) => localStorage.setItem('dq-theme-mode', m), mode);
    });

    test('dashboard', async ({ page }) => {
      await page.goto('/dashboard');
      await expect(page.getByRole('heading', { name: 'Dashboard', level: 3 })).toBeVisible();
      const suitePerformance = page.locator('.ant-card').filter({ hasText: /Suite Performance/i });
      await checkRoute(
        mode,
        page,
        'route:/dashboard',
        suitePerformance.getByText('Orders quality'),
      );
    });

    test('connections', async ({ page }) => {
      await page.goto('/connections');
      await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();
      await checkRoute(mode, page, 'route:/connections', page.getByText('snowflake-analytics'));
    });

    test('suites list', async ({ page }) => {
      await page.goto('/suites');
      await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
      await checkRoute(mode, page, 'route:/suites', page.getByText('Orders quality'));
    });

    test('suite detail', async ({ page }) => {
      await page.goto('/suites');
      await page.getByText('Orders quality').click();
      await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
      await expect(page.getByRole('heading', { name: 'Orders quality', level: 4 })).toBeVisible();
      await checkRoute(mode, page, 'route:/suites/:id', page.getByText('order_id not null'));
    });

    test('results', async ({ page }) => {
      await page.goto('/results');
      await expect(page.getByRole('heading', { name: 'Results', level: 3 })).toBeVisible();
      await expect(page.locator('tr.ant-table-row').first()).toBeVisible();
      await checkRoute(mode, page, 'route:/results', page.locator('tr.ant-table-row'));
    });

    test('run detail', async ({ page }) => {
      await page.goto('/results');
      await page.locator('tr.ant-table-row').first().click();
      await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
      await expect(page.getByTestId('rd-screen')).toBeVisible();
      await checkRoute(
        mode,
        page,
        'route:/results/:id',
        page.getByTestId('rd-screen').getByText('order_id not null'),
      );
    });

    test('assets', async ({ page }) => {
      await page.goto('/assets');
      await expect(page.getByRole('heading', { name: 'Assets', level: 3 })).toBeVisible();
      await checkRoute(mode, page, 'route:/assets', page.getByRole('treeitem', { name: /ORDERS/ }));
    });

    test('contracts', async ({ page }) => {
      await page.goto('/contracts');
      await expect(page.getByRole('heading', { name: 'Data contracts', level: 3 })).toBeVisible();
      await checkRoute(mode, page, 'route:/contracts', page.getByText('Orders delivery'));
    });

    // Every admin sub-page is its own route; `/admin` redirects to Overview.
    // Each sub-page's seeded content is judged order-independently: another spec may add or
    // remove a channel or a seasonal period, so those two accept an empty list or a row.
    const adminLoaded: Record<string, (page: Page) => Loaded> = {
      overview: (page) => page.getByText(/across \d+ connection\(s\)/),
      members: (page) => page.getByText('dev-bypass@dataq.local'),
      suites: (page) => page.getByRole('main').getByText('Orders quality'),
      integrations: (page) => page.getByText('adf-orchestrator'),
      settings: (page) =>
        page.getByText('No channels yet.').or(page.locator('[role="listitem"], tr.ant-table-row')),
      calendar: (page) =>
        page.getByText('No seasonal periods declared.').or(page.locator('tr.ant-table-row')),
      compliance: (page) => page.getByText('api_key.mint'),
    };
    for (const sub of Object.keys(adminLoaded)) {
      test(`admin ${sub}`, async ({ page }) => {
        // dev-bypass is workspace-admin in this lane (CI + compose parity, see e2e/README.md).
        await page.goto(`/admin/${sub}`);
        await expect(page.getByRole('heading', { name: 'Admin', level: 3 })).toBeVisible();
        await checkRoute(mode, page, `route:/admin/${sub}`, adminLoaded[sub](page));
      });
    }

    test('profile', async ({ page }) => {
      await page.goto('/profile');
      await expect(page.getByRole('heading', { name: 'Profile', level: 3 })).toBeVisible();
      await checkRoute(mode, page, 'route:/profile', page.getByLabel('Suites by access level'));
    });

    // What a non-admin sees at every admin URL, and at the retired /settings.
    test.describe('as a member', () => {
      // Skippable on a laptop that has not seeded; never in CI, where a silent skip would leave
      // the coverage test satisfied by a scan that did not run.
      test.skip(
        MEMBER_TOKEN === null && !process.env.CI,
        'run `python -m backend.scripts.seed_dev` first',
      );
      test.use({ extraHTTPHeaders: { Authorization: `Bearer ${MEMBER_TOKEN ?? 'unseeded'}` } });

      test('forbidden', async ({ page }) => {
        await page.goto('/settings');
        await expect(page.getByText(/restricted to workspace admins/i)).toBeVisible();
        // The forbidden page is the loaded state: it renders once `/me` has resolved the role.
        await checkRoute(
          mode,
          page,
          'route:/403',
          page.getByText(/restricted to workspace admins/i),
        );
      });
    });

    test('not found', async ({ page }) => {
      await page.goto('/no-such-route');
      await expect(page.getByText('404')).toBeVisible();
      await checkRoute(mode, page, 'route:/404', STATIC);
    });

    // The authoring + detail routes are id-addressed; resolve the seeded ids through the API
    // rather than clicking through list pages the tests above already cover.
    test.describe('id-addressed routes', () => {
      let suiteId: string;
      let checkId: string;
      let assetId: string;
      let connectionId: string;
      let contractId: string;
      let compareUrl: string;
      // Names the seed gave them, which each page shows only once its own fetch has returned.
      let checkName: string;
      let assetName: string;
      let connectionName: string;
      let contractName: string;

      test.beforeAll(async ({ request }) => {
        const suites: { id: string; name: string }[] = await (
          await request.get('/api/v1/suites')
        ).json();
        const orders = suites.find((s) => s.name === 'Orders quality');
        expect(orders, 'seeded "Orders quality" suite').toBeDefined();
        suiteId = orders?.id ?? '';
        const checks: { id: string; name: string }[] = await (
          await request.get(`/api/v1/suites/${suiteId}/checks`)
        ).json();
        checkId = checks[0].id;
        checkName = checks[0].name;
        const assets: { id: string; name: string }[] = await (
          await request.get('/api/v1/assets')
        ).json();
        assetId = assets[0].id;
        assetName = assets[0].name;
        const connections: { id: string; name: string }[] = await (
          await request.get('/api/v1/connections')
        ).json();
        connectionId = connections[0].id;
        connectionName = connections[0].name;
        // The demo seed imports one data contract (backend/scripts/demo_data.py).
        const contracts: { id: string; name: string }[] = await (
          await request.get('/api/v1/contracts')
        ).json();
        expect(contracts.length, 'the seeded data contract').toBeGreaterThan(0);
        contractId = contracts[0].id;
        contractName = contracts[0].name;
        // The seeded suite's two most recent runs, the older one as the base.
        const runs: { id: string }[] = await (
          await request.get(`/api/v1/runs?suite_id=${suiteId}&limit=2`)
        ).json();
        expect(runs.length, 'two seeded runs of "Orders quality"').toBe(2);
        compareUrl = `/results/${runs[1].id}/compare/${runs[0].id}`;
      });

      const routes: [string, () => string, (page: Page) => Loaded][] = [
        [
          'route:/results/:id/compare/:id',
          () => compareUrl,
          (page) => page.getByText(/Showing \d+ of \d+ checks/),
        ],
        // The connection catalogue is the page's own markup: nothing is fetched for it.
        ['route:/connections/new', () => '/connections/new', () => STATIC],
        [
          'route:/connections/:id/edit',
          () => `/connections/${connectionId}/edit`,
          (page) => page.getByLabel('Name').and(page.locator(`[value="${connectionName}"]`)),
        ],
        // The blank suite form: its connection list is fetched, but the fields are the content.
        ['route:/suites/new', () => '/suites/new', () => STATIC],
        [
          'route:/suites/:id/edit',
          () => `/suites/${suiteId}/edit`,
          (page) => page.getByRole('heading', { name: 'Edit “Orders quality”' }),
        ],
        [
          'route:/suites/:id/checks/new',
          () => `/suites/${suiteId}/checks/new`,
          // Offered per the suite's connection engine: Snowflake's native engine appears only once
          // the suite and its connection have loaded.
          (page) => page.getByText('Snowflake DMF', { exact: true }),
        ],
        [
          'route:/suites/:id/checks/:id/edit',
          () => `/suites/${suiteId}/checks/${checkId}/edit`,
          (page) => page.getByRole('heading', { name: `Edit “${checkName}”` }),
        ],
        [
          'route:/assets/:id',
          () => `/assets/${assetId}`,
          (page) => page.getByRole('heading', { name: assetName }),
        ],
        [
          'route:/contracts/:id',
          () => `/contracts/${contractId}`,
          (page) => page.getByRole('heading', { name: new RegExp(contractName) }),
        ],
      ];
      for (const [surface, url, loaded] of routes) {
        test(surface, async ({ page }) => {
          await page.goto(url());
          await expect(page.getByRole('heading').first()).toBeVisible();
          await checkRoute(mode, page, surface, loaded(page));
        });
      }
    });
  });
}
