import AxeBuilder from '@axe-core/playwright';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from './fixtures';
import {
  type AxeViolationLike,
  filterGated,
  newViolationsMessage,
  ratchet,
  toRecords,
} from '../scripts/a11y/ratchet';
import { settle } from '../scripts/a11y/settle';

// The sign-in screen (#1670 item 1) only exists under this lane — dev-bypass (e2e/) has
// no sign-in wall, so it's the one route this baseline scans here rather than in
// e2e/a11y.spec.ts. Shares the same committed baseline (frontend/a11y-baseline.json) and
// ratchet module as the dev-bypass routes and the Vitest component lane; see
// e2e/a11y.spec.ts for the full rationale.
const __dirname = dirname(fileURLToPath(import.meta.url));
const BASELINE_PATH = resolve(__dirname, '../a11y-baseline.json');
const CAPTURE = process.env.A11Y_BASELINE === '1';

test('sign-in screen', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByLabel('Email address')).toBeVisible();

  const surface = 'route:/sign-in';
  await settle(page);
  const results = await new AxeBuilder({ page }).options({ ancestry: true }).analyze();
  const gated = filterGated(results.violations as AxeViolationLike[]);
  const records = toRecords(surface, gated);
  const { newViolations } = ratchet(surface, records, { path: BASELINE_PATH, capture: CAPTURE });
  expect(newViolations, newViolationsMessage(surface, newViolations)).toEqual([]);
});
