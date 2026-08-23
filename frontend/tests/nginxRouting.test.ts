import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/** nginx SPA-routing invariants (prod-only regression, found live 2026-07-12). */
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

/** X-Forwarded-Proto forwarding (#1138) — a *security* invariant, not a routing one. */
describe('nginx X-Forwarded-Proto (#1138 — OTP session cookie Secure inference)', () => {
  // Block-matching deliberately terminates on a `}` **at the start of a line**, not on any `}`: the
  // proxy bodies contain `${DATAQ_API_UPSTREAM}`.
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
    // The #1138 fix touches the -Proto header only. -For is a different header with a different
    // trust model (the limiter hops back a configured number of entries).
    for (const block of proxyBlocks) {
      expect(block).toMatch(/proxy_set_header\s+X-Forwarded-For\s+\$proxy_add_x_forwarded_for;/);
    }
  });
});

/**
 * Security-header inheritance (#1387). nginx drops every inherited `add_header` at any level that
 * declares one of its own.
 */
describe('nginx security headers (#1387 add_header inheritance)', () => {
  const SNIPPET_INCLUDE = 'include /etc/nginx/nginx-security-headers.conf;';

  /** Every `location … { … }` block, brace-matched (the bodies contain no nested braces). */
  const locationBlocks = [...directives.matchAll(/location\s+[^{]+\{([^}]*)\}/g)].map((m) => ({
    header: m[0].slice(0, m[0].indexOf('{')).trim(),
    body: m[1],
  }));

  it('finds the locations it means to check', () => {
    // Guards the regex itself: if it silently matched nothing, every assertion
    // below would vacuously pass — the failure mode this whole file exists for.
    expect(locationBlocks.length).toBeGreaterThanOrEqual(6);
  });

  it('re-includes the snippet in EVERY location that sets its own add_header', () => {
    const shadowing = locationBlocks.filter((b) => /add_header/.test(b.body));
    // config.js, the /assets file regex, and the SPA fallback.
    expect(shadowing.length).toBe(3);
    for (const block of shadowing) {
      expect(
        block.body,
        `${block.header} sets add_header, which cancels inherited security headers`,
      ).toContain(SNIPPET_INCLUDE);
    }
  });

  it('includes the snippet at server level for locations that set no add_header', () => {
    const serverLevel = directives.slice(
      directives.indexOf('server {'),
      directives.indexOf('location'),
    );
    expect(serverLevel).toContain(SNIPPET_INCLUDE);
  });

  it('builds a CSP whose connect-src is runtime-configurable', () => {
    const map = directives.match(/map\s+\$host\s+\$dataq_csp\s*\{[^}]*\}/);
    expect(map, 'expected a `map $host $dataq_csp` block').not.toBeNull();
    // The directives that do the actual work — pinned so a future edit that
    // loosens them has to say so out loud.
    expect(map?.[0]).toContain("frame-ancestors 'none'");
    expect(map?.[0]).toContain("object-src 'none'");
    expect(map?.[0]).toContain("script-src 'self'");
    expect(map?.[0]).toContain("base-uri 'self'");
    // Substituted per deployment: deriving it from DATAQ_AUTH_AUTHORITY would
    // block Azure AD's token endpoint (it sits outside the authority's path).
    expect(map?.[0]).toContain('${DATAQ_CSP_CONNECT_SRC}');
  });

  it('turns off nginx version disclosure', () => {
    expect(directives).toMatch(/server_tokens\s+off;/);
  });
});
