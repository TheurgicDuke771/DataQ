import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import type { FixtureBundle } from '../src/demo/fixtures';
import { visit } from './visit';

// Drives the BUILT demo (dist-demo/) from a Pages-like static server with no API behind it
// (#2419). This is what a visitor gets, so it is what has to be proven: every recorded route
// renders from the recording alone, deep links survive the host's 404, and writes are refused.
const bundle = JSON.parse(
  readFileSync(
    resolve(dirname(fileURLToPath(import.meta.url)), '../dist-demo/demo-fixtures.json'),
    'utf8',
  ),
) as FixtureBundle;

/** Collects what must never happen in the demo: a request to an API, or a view that fell
 *  outside the recording. */
async function watch(page: Page): Promise<{ escaped: string[]; notices: () => Promise<string[]> }> {
  const escaped: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.includes('/api/')) escaped.push(request.url());
  });
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __demoNotices: string[] }).__demoNotices = seen;
    window.addEventListener('dataq-demo-notice', (e) => seen.push((e as CustomEvent).detail));
  });
  const notices = () =>
    page.evaluate(() => (window as unknown as { __demoNotices: string[] }).__demoNotices);
  return { escaped, notices };
}

test('every recorded route renders from the recording alone', async ({ page }) => {
  test.setTimeout(15 * 60_000);
  expect(bundle.routes.length).toBeGreaterThan(20);
  const { escaped, notices } = await watch(page);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const outside: string[] = [];
  for (const route of bundle.routes) {
    await visit(page, route);
    await expect(page.locator('#dq-main')).toBeVisible();
    for (const kind of await notices()) outside.push(`${kind} on ${route}`);
  }
  expect(escaped, 'requests that left the page for an API').toEqual([]);
  expect(outside, 'views the recording does not cover').toEqual([]);
  expect(errors, 'uncaught page errors').toEqual([]);
});

test('the dashboard shows the seeded workspace and says it is a demo', async ({ page }) => {
  await page.goto('');
  await expect(page).toHaveURL(/\/demo\/dashboard$/);
  await expect(page.getByRole('heading', { name: 'Dashboard', level: 3 })).toBeVisible();
  await expect(page.getByText('Read-only demo · sample data')).toBeVisible();
});

test('a deep link survives the static host answering 404', async ({ page }) => {
  const suite = bundle.routes.find((r) => /^\/suites\/[0-9a-f-]{36}$/.test(r));
  if (!suite) throw new Error('the recording has no suite route');
  // The host has no such file: it answers 404 with the site's 404 page, which hands the
  // route back to the app.
  const response = await page.goto(suite.slice(1));
  expect(response?.status()).toBe(404);
  await expect(page).toHaveURL(new RegExp(`/demo${suite}$`));
  await expect(page.getByRole('heading', { level: 4 }).first()).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`/demo${suite}$`));
});

test('a write is refused with a pointer to the install guide', async ({ page }) => {
  const { escaped, notices } = await watch(page);
  await page.goto('suites/new');
  await page.getByLabel('Name').first().fill('Demo attempt');
  await page.getByRole('combobox', { name: 'Connection' }).click();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: /create/i }).click();
  await expect(page.getByText('Read-only demo', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Run DataQ yourself in 5 minutes' }).last(),
  ).toHaveAttribute('href', /get-started\/install/);
  expect(await notices()).toContain('read-only');
  expect(escaped).toEqual([]);
});

test('the marketing page links to the demo', async ({ page }) => {
  await page.goto('../');
  await page.getByRole('link', { name: 'Explore the live demo' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard', level: 3 })).toBeVisible();
});
