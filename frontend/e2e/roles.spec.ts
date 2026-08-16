import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

/**
 * Per-role browser perspectives — ADR 0033 slice #743.
 *
 * The dev-bypass identity is always a workspace admin (#741) and cannot be
 * demoted, so a browser cannot experience the other two tiers as itself. These
 * specs instead authenticate as REAL seeded users via a PAT: `get_current_user`
 * resolves a `dq_live_` bearer *before* the dev-bypass branch, so a context that
 * sets an Authorization header is that user for every request the Vite proxy
 * forwards.
 *
 * That matters more than the convenience. Mocking `/me` to claim a role would
 * test the UI against a lie — it could not catch a control that is hidden
 * correctly but whose endpoint is ungated, nor a page that 403s for a tier the
 * UI happily renders. Here the server genuinely is deciding.
 *
 * Tokens come from `python -m backend.scripts.seed_dev`, which writes them to a
 * gitignored file; CI seeds before Playwright runs. If the file is missing the
 * specs SKIP with an explanatory message rather than failing — a missing seed is
 * an environment problem, and a red suite that means "you forgot to seed" trains
 * people to ignore red suites.
 */

type Role = 'member' | 'viewer';

const TOKENS_PATH = fileURLToPath(new URL('./.role-tokens.json', import.meta.url));

function tokens(): Record<Role, string> | null {
  try {
    return JSON.parse(readFileSync(TOKENS_PATH, 'utf8')) as Record<Role, string>;
  } catch {
    return null;
  }
}

const ROLE_TOKENS = tokens();

test.describe('Role perspectives', () => {
  test.skip(
    ROLE_TOKENS === null,
    'run `python -m backend.scripts.seed_dev` first — it mints the per-role PATs',
  );

  // ── Admin: the full surface (the ambient dev-bypass identity) ─────────────

  test('admin sees every connection control', async ({ page }) => {
    await page.goto('/connections');
    await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();

    await expect(page.getByRole('button', { name: 'Add connection' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Test all' })).toBeVisible();
    await expect(page.getByText(/managed by workspace admins/i)).toHaveCount(0);
  });

  test('admin sees the authoring controls on suites', async ({ page }) => {
    await page.goto('/suites');
    await expect(page.getByRole('button', { name: /New suite/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Import/ })).toBeVisible();
  });

  // ── Member: authors suites, cannot manage connections ─────────────────────

  test.describe('member', () => {
    test.use({
      extraHTTPHeaders: { Authorization: `Bearer ${ROLE_TOKENS?.member ?? 'unseeded'}` },
    });

    test('cannot mutate connections but can still test and reference them', async ({ page }) => {
      await page.goto('/connections');
      await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();

      // The load-bearing assertion of the whole ADR: no path to delete or
      // re-credential the connection every suite runs on.
      await expect(page.getByRole('button', { name: 'Add connection' })).toHaveCount(0);
      await expect(page.getByText(/managed by workspace admins/i)).toBeVisible();
      // …but connections are still listed and testable — a Member authoring a
      // suite needs both.
      await expect(page.getByRole('button', { name: 'Test all' })).toBeVisible();
    });

    test('can still author suites', async ({ page }) => {
      await page.goto('/suites');
      await expect(page.getByRole('button', { name: /New suite/ })).toBeVisible();
      await expect(page.getByText('Read-only access')).toHaveCount(0);
    });

    test('is not offered the admin page', async ({ page }) => {
      await page.goto('/admin');
      // Server-side 403 rendered as the Forbidden state — the nav item is hidden
      // too, but a deep link must not leak the page.
      await expect(page.getByText(/restricted to workspace admins/i)).toBeVisible();
    });
  });

  // ── Viewer: a read-only shell ─────────────────────────────────────────────

  test.describe('viewer', () => {
    test.use({
      extraHTTPHeaders: { Authorization: `Bearer ${ROLE_TOKENS?.viewer ?? 'unseeded'}` },
    });

    test('gets a read-only connections page', async ({ page }) => {
      await page.goto('/connections');
      await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();

      await expect(page.getByText(/read-only/i)).toBeVisible();
      await expect(page.getByRole('button', { name: 'Add connection' })).toHaveCount(0);
      // No Test either: the probe opens an outbound connection using stored
      // credentials, which is Member+ on the server.
      await expect(page.getByRole('button', { name: 'Test all' })).toHaveCount(0);
    });

    test('cannot author suites', async ({ page }) => {
      await page.goto('/suites');
      await expect(page.getByText('Read-only access')).toBeVisible();
      await expect(page.getByRole('button', { name: /New suite/ })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /Import/ })).toHaveCount(0);
    });

    test('can still SEE a suite shared with them', async ({ page }) => {
      // The tier has to be USABLE, not merely restricted: a viewer who can see
      // nothing is indistinguishable from a broken account. The seed grants a
      // `view` share on the first suite the dev-bypass owner has — the probe
      // suite — precisely so this is observable.
      //
      // It also proves the Viewer cap doesn't over-reach: `effective_permission`
      // clamps a viewer to `view`, and clamping to *nothing* would hide the
      // suite entirely (existence-hiding), which is the failure mode the cap's
      // own docstring rules out.
      await page.goto('/suites');
      await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toBeVisible();
      await expect(page.getByText('probe-snowflake-suite').first()).toBeVisible();
    });
  });
});
