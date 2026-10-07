import { expect, test } from '@playwright/test';
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

test('record the seeded workspace', async ({ page }) => {
  test.setTimeout(15 * 60_000);
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
        const data: unknown = await response.json().catch(() => undefined);
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
  await Promise.all(pending);

  expect(failures, 'the API answered 5xx while recording').toEqual([]);
  expect(Object.keys(entries).length).toBeGreaterThan(routes.length / 2);
  const bundle: FixtureBundle = { capturedAt: new Date().toISOString(), routes, entries };
  writeFileSync(OUT, JSON.stringify(bundle));
  console.log(`recorded ${Object.keys(entries).length} responses over ${routes.length} routes`);
});
