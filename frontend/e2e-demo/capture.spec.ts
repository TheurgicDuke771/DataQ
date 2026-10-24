import { expect, test, type Page, type Response } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { fixtureKey, type FixtureBundle, type RecordedResponse } from '../src/demo/fixtures';
import { discoverRoutes } from './routes';
import { visit } from './visit';

// Records what the app asks the API for on every route of the seeded workspace (#2419). The
// output, demo-fixtures.json, is never committed: CI records it from the stack it just seeded.
const OUT = resolve(dirname(fileURLToPath(import.meta.url)), '../demo-fixtures.json');
const API = '/api/v1';
// A body read that never settles (a response the page abandoned mid-flight) would hold the
// final `Promise.all(pending)` until the test-wide timeout; the recording was already complete.
const BODY_BUDGET_MS = 10_000;

async function readBody(response: Response, key: string): Promise<unknown> {
  let timer: NodeJS.Timeout | undefined;
  const stalled = new Promise<undefined>((resolve) => {
    timer = setTimeout(() => {
      console.log(
        `[capture] body of ${key.split('?')[0]} not read within ${BODY_BUDGET_MS / 1000}s`,
      );
      resolve(undefined);
    }, BODY_BUDGET_MS);
  });
  try {
    return await Promise.race([response.json().catch(() => undefined), stalled]);
  } finally {
    clearTimeout(timer);
  }
}

test('record the seeded workspace', async ({ page }) => {
  test.setTimeout(8 * 60_000);
  const entries: Record<string, RecordedResponse> = {};
  const failures: string[] = [];
  const pending: Promise<void>[] = [];

  page.on('response', (response) => {
    const url = new URL(response.url());
    if (!url.pathname.startsWith(`${API}/`) || response.request().method() !== 'GET') return;
    const key = fixtureKey('GET', url.pathname + url.search);
    if (response.status() >= 500) failures.push(`${response.status()} ${key}`);
    pending.push(
      (async () => {
        const all = response.headers();
        if (!(all['content-type'] ?? '').includes('json')) return;
        const headers = Object.fromEntries(
          Object.entries(all).filter(([k]) => k.startsWith('x-') && k !== 'x-request-id'),
        );
        // A response whose page navigated away before its body was read is recorded on the
        // page that waits for it; losing this copy is fine.
        const data = await readBody(response, key);
        if (data === undefined) return;
        entries[key] = { status: response.status(), data, headers };
      })(),
    );
  });

  const routes = await discoverRoutes(async (path) => {
    const response = await page.request.get(`${API}${path}`);
    expect(response.ok(), `GET ${path}`).toBe(true);
    return response.json();
  });
  for (const route of routes) await visit(page, route);
  // The feed behind the header bell is fetched only when the drawer opens, which no route
  // visit does; without this step the published demo's bell always errors.
  await openNotificationFeed(page);
  await Promise.all(pending);

  expect(failures, 'the API answered 5xx while recording').toEqual([]);
  expect(Object.keys(entries).length).toBeGreaterThan(routes.length / 2);
  const bundle: FixtureBundle = { capturedAt: new Date().toISOString(), routes, entries };
  writeFileSync(OUT, JSON.stringify(bundle));
  console.log(`recorded ${Object.keys(entries).length} responses over ${routes.length} routes`);
});

/** Open the bell's drawer on the dashboard so `GET /notifications` is recorded, then close it. */
async function openNotificationFeed(page: Page): Promise<void> {
  await visit(page, '/dashboard');
  const bell = page.getByRole('button', { name: /^Notifications/ });
  await bell.click();
  await page.getByRole('dialog', { name: 'Notifications' }).waitFor();
  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined);
  await page.keyboard.press('Escape');
}
