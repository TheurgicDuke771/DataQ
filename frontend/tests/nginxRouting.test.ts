import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * nginx SPA-routing invariants (prod-only regression, found live 2026-07-12).
 *
 * In production the SPA is served by nginx, not Vite. Vite's dev server has its own
 * SPA fallback, so **the Playwright e2e suite structurally cannot catch an nginx
 * routing bug** — which is exactly how this shipped: in-app navigation to /assets
 * worked perfectly, while every deep link, bookmark and browser-refresh 404'd.
 *
 * The collision: Vite emits its bundle to `dist/assets/`, which shares a path prefix
 * with the app's own `/assets` route (the ADR 0034 asset browse). Two mistakes each
 * break it independently, so both are pinned here:
 *
 *   1. A prefix `location /assets/ { try_files $uri =404; }` swallows the whole path
 *      space — `/assets/<uuid>` matched it and 404'd instead of reaching the SPA.
 *   2. `try_files $uri $uri/ /index.html` in `location /` makes nginx find the on-disk
 *      `assets/` DIRECTORY and issue its own 301 to `/assets/`, rebuilt from the
 *      INTERNAL scheme/port — handing the browser `http://…:8080/assets/`, which is
 *      unroutable, so the tab just hangs.
 *
 * This is a config assertion, not a semantic one: it cannot prove nginx behaves: for
 * that, serve `dist/` behind the real template and curl the routes. It does pin the
 * two exact mistakes so they cannot come back silently.
 */
// vitest runs with cwd = frontend/ (see the workspace root in vite config).
const template = readFileSync(resolve(process.cwd(), 'nginx.conf.template'), 'utf8');

/** Strip comments — we're asserting on directives, not on the prose explaining them. */
const directives = template
  .split('\n')
  .filter((l) => !l.trim().startsWith('#'))
  .join('\n');

describe('nginx SPA routing (#802 /assets deep-link regression)', () => {
  it('does NOT claim the whole /assets/ prefix for static files', () => {
    // A bare prefix location would shadow the SPA fallback for /assets/<assetId>.
    expect(directives).not.toMatch(/location\s+\/assets\/\s*\{/);
  });

  it('matches the fingerprinted bundle by FILE EXTENSION, so app routes fall through', () => {
    const staticLoc = /location\s+~\*?\s+\^\/assets\/.+\\\.\(\?:[^)]*js[^)]*\)\$/;
    expect(directives).toMatch(staticLoc);
  });

  it('keeps the immutable long-cache on the real bundle', () => {
    expect(directives).toMatch(/expires\s+1y;/);
    expect(directives).toMatch(/Cache-Control\s+"public,\s*immutable"/);
  });

  it('SPA fallback does not use $uri/ (no directory-index 301 to the internal port)', () => {
    const spa = directives.match(/location\s+\/\s*\{[^}]*\}/);
    expect(spa, 'expected a `location / { … }` SPA fallback block').not.toBeNull();
    expect(spa?.[0]).toMatch(/try_files\s+\$uri\s+\/index\.html;/);
    expect(spa?.[0], '`$uri/` re-introduces the directory-redirect bug').not.toMatch(/\$uri\/\s/);
  });
});
