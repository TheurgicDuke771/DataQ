import fs from 'node:fs';
import path from 'node:path';

import { chromium } from '@playwright/test';

// One interactive login per session: launches a HEADED browser at the live
// URL, waits for a human to complete the real OIDC sign-in (MFA and all), then
// serializes sessionStorage to a gitignored file the specs replay via
// addInitScript. sessionStorage (not cookies/localStorage) because
// oidc-client-ts keeps the signed-in user there (authClient.ts
// WebStorageStateStore) — Playwright's built-in storageState can't capture it.
//
// The captured token expires like any AAD token (~1h); the file is reused
// while fresh and re-captured otherwise.

// cwd-relative (pnpm runs from frontend/): __dirname doesn't exist under ESM.
export const SESSION_FILE = path.resolve('e2e-live', '.auth', 'session.json');
const MAX_AGE_MS = 40 * 60 * 1000; // re-login when the capture is older than 40 min

export default async function globalSetup(): Promise<void> {
  const liveBaseURL = process.env.E2E_LIVE_BASE_URL;
  if (!liveBaseURL) {
    throw new Error('live-smoke lane requires E2E_LIVE_BASE_URL');
  }
  if (fs.existsSync(SESSION_FILE)) {
    const age = Date.now() - fs.statSync(SESSION_FILE).mtimeMs;
    if (age < MAX_AGE_MS) {
      return; // fresh enough — reuse
    }
  }

  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage();
  await page.goto(liveBaseURL);
  console.log('\n[live-smoke] Complete the sign-in in the opened browser…');
  // The post-login landing is the Dashboard; give MFA up to 5 minutes.
  await page.getByRole('heading', { name: 'Dashboard', level: 3 }).waitFor({ timeout: 300_000 });
  const session: string = await page.evaluate(() => JSON.stringify({ ...sessionStorage }));
  fs.mkdirSync(path.dirname(SESSION_FILE), { recursive: true });
  fs.writeFileSync(SESSION_FILE, session);
  await browser.close();
  console.log('[live-smoke] Session captured.');
}
