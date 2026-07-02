import fs from 'node:fs';

import { test as base } from '@playwright/test';

import { SESSION_FILE } from './global-setup';

/** `test` for live-smoke specs: replays the captured OIDC sessionStorage into
 *  every new document before any app code runs, so the SPA boots signed-in. */
export const test = base.extend({
  // Playwright fixture; the continuation is named `run` (not the conventional
  // `use`) so eslint's react-hooks rule doesn't mistake it for a React hook.
  context: async ({ context }, run) => {
    const entries: Record<string, string> = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'));
    await context.addInitScript((stored) => {
      for (const [key, value] of Object.entries(stored)) {
        sessionStorage.setItem(key, value);
      }
    }, entries);
    await run(context);
  },
});

export { expect } from '@playwright/test';
