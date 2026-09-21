import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// "Every route is scanned" was claimed twice and was one route short both times. This holds the
// claim to the routers: a `<Route path>` with no matching surface in e2e/a11y.spec.ts fails here.
const read = (f: string) => readFileSync(resolve(__dirname, '../..', f), 'utf8');

const paramsToId = (p: string) => p.replace(/:[A-Za-z]+/g, ':id');

function declaredRoutes(): string[] {
  const paths = (src: string) => [...src.matchAll(/path=["']([^"']+)["']/g)].map((m) => m[1]);
  const top = paths(read('src/App.tsx')).filter((p) => p !== '/' && p !== '*');
  const admin = paths(read('src/pages/admin/routes.tsx'))
    .filter((p) => p !== '*')
    .map((p) => (p.startsWith('/') ? p : `/admin/${p}`));
  return [...new Set([...top, ...admin].map(paramsToId))];
}

function scannedSurfaces(): Set<string> {
  const spec = read('e2e/a11y.spec.ts');
  const literal = [...spec.matchAll(/'route:([^'$]+)'/g)].map((m) => m[1]);
  // The admin sub-pages are scanned from one loop over a literal array.
  const loop = /for \(const sub of \[([^\]]+)\]\)/.exec(spec);
  const subs = loop ? [...loop[1].matchAll(/'([^']+)'/g)].map((m) => `/admin/${m[1]}`) : [];
  return new Set([...literal, ...subs]);
}

// Declared, deliberately not scanned — each needs a reason.
const EXEMPT: Record<string, string> = {
  '/admin': 'layout parent of the admin sub-routes; its index redirects to /admin/overview',
  '/settings':
    'an admin is redirected to /admin/settings; a non-admin gets the Forbidden page — both scanned, as /admin/settings and /403',
};

describe('a11y scan coverage', () => {
  it('scans every route the routers declare', () => {
    const declared = declaredRoutes();
    expect(declared.length).toBeGreaterThan(15);
    const scanned = scannedSurfaces();
    const missing = declared.filter((r) => !scanned.has(r) && !(r in EXEMPT));
    expect(missing).toEqual([]);
  });

  it('keeps no exemption for a route that is gone or is now scanned', () => {
    const declared = new Set(declaredRoutes());
    const scanned = scannedSurfaces();
    const stale = Object.keys(EXEMPT).filter((r) => !declared.has(r) || scanned.has(r));
    expect(stale).toEqual([]);
  });

  it('scans the states that have no path of their own', () => {
    // Not found, and what a non-admin gets at every admin-gated URL.
    expect(scannedSurfaces().has('/404')).toBe(true);
    expect(scannedSurfaces().has('/403')).toBe(true);
  });
});
