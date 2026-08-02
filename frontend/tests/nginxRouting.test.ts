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

/**
 * X-Forwarded-Proto forwarding (#1138) — a *security* invariant, not a routing one.
 *
 * This container never terminates TLS, so nginx's own `$scheme` is deterministically
 * `http`. `proxy_set_header X-Forwarded-Proto $scheme` therefore REPLACED the edge's
 * correct `https` with `http`, and the backend's `_cookie_secure()` — which infers
 * from exactly that header — dropped `Secure` from the OTP session cookie on a live
 * HTTPS deployment.
 *
 * Like the block above this is a config assertion: it cannot prove nginx behaves,
 * only that the exact mistake cannot come back silently. And it is the *only*
 * automated guard available — Vite serves the E2E lane, so no browser test ever
 * executes this file.
 */
describe('nginx X-Forwarded-Proto (#1138 — OTP session cookie Secure inference)', () => {
  // Block-matching deliberately terminates on a `}` **at the start of a line**, not
  // on any `}`: the proxy bodies contain `${DATAQ_API_UPSTREAM}`, so a `[^}]*` scan
  // stops inside the envsubst placeholder and silently matches nothing. (It did.)
  const proxyBlocks = [...directives.matchAll(/location[^\n]*\{\n.*?\n\s*\}/gs)]
    .map((m) => m[0])
    .filter((b) => b.includes('proxy_pass'));

  it('has the three proxy blocks this file is supposed to have (/api, /healthz, /mcp)', () => {
    // Guards the assertions below from silently vacuously passing if the regex
    // above ever stops matching the blocks.
    expect(proxyBlocks).toHaveLength(3);
  });

  it('never forwards nginx’s own $scheme as the client-facing protocol', () => {
    for (const block of proxyBlocks) {
      expect(block, 'X-Forwarded-Proto $scheme is the #1138 bug').not.toMatch(
        /X-Forwarded-Proto\s+\$scheme\s*;/,
      );
    }
  });

  it('forwards the mapped variable on every proxying location', () => {
    for (const block of proxyBlocks) {
      expect(block).toMatch(/proxy_set_header\s+X-Forwarded-Proto\s+\$dataq_forwarded_proto;/);
    }
  });

  it('maps that variable to the inbound header, falling back to $scheme only when absent', () => {
    const map = directives.match(
      /map\s+\$http_x_forwarded_proto\s+\$dataq_forwarded_proto\s*\{[^}]*\}/,
    );
    expect(
      map,
      'expected a `map $http_x_forwarded_proto $dataq_forwarded_proto` block',
    ).not.toBeNull();
    expect(map?.[0]).toMatch(/default\s+\$http_x_forwarded_proto;/);
    // The empty-string key is what keeps a DIRECT connection (compose, docker run)
    // honest: with no edge header, $scheme genuinely is the client-facing scheme.
    expect(map?.[0]).toMatch(/""\s+\$scheme;/);
  });

  it('leaves X-Forwarded-For on $proxy_add_x_forwarded_for (rate limiting reads it — ADR 0035)', () => {
    // The #1138 fix touches the -Proto header only. -For is a different header
    // with a different trust model (the limiter hops back a configured number of
    // entries), and appending must stay appending.
    for (const block of proxyBlocks) {
      expect(block).toMatch(/proxy_set_header\s+X-Forwarded-For\s+\$proxy_add_x_forwarded_for;/);
    }
  });
});
