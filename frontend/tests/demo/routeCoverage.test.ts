import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { discoverRoutes, ROUTE_PATTERNS } from '../../e2e-demo/routes';

// The demo replays only what was recorded, and only routes in ROUTE_PATTERNS are recorded
// (#2419). A `<Route path>` added to the routers and not to that list would ship as a page
// with no data, so it fails here first.
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

// Declared, deliberately not recorded — each needs a reason.
const EXEMPT: Record<string, string> = {
  '/admin': 'layout parent; its index redirects to /admin/overview',
  '/settings': 'redirects to /admin/settings',
};

describe('demo route coverage', () => {
  it('records every route the routers declare', () => {
    const declared = declaredRoutes();
    expect(declared.length).toBeGreaterThan(15);
    const recorded = new Set<string>(ROUTE_PATTERNS);
    expect(declared.filter((r) => !recorded.has(r) && !(r in EXEMPT))).toEqual([]);
  });

  it('keeps no pattern or exemption for a route that is gone', () => {
    const declared = new Set(declaredRoutes());
    expect(ROUTE_PATTERNS.filter((r) => !declared.has(r))).toEqual([]);
    expect(Object.keys(EXEMPT).filter((r) => !declared.has(r))).toEqual([]);
  });

  it('expands every pattern into the seeded rows', async () => {
    const lists: Record<string, { id: string }[]> = {
      '/suites': [{ id: 's1' }, { id: 's2' }],
      '/connections': [{ id: 'c1' }],
      '/assets': [{ id: 'a1' }],
      '/runs': [{ id: 'r1' }],
      '/suites/s1/checks': [{ id: 'k1' }],
      '/suites/s2/checks': [],
    };
    const routes = await discoverRoutes((path) => Promise.resolve(lists[path]));
    expect(routes).toContain('/suites/s2/edit');
    expect(routes).toContain('/suites/s1/checks/k1/edit');
    expect(routes).toContain('/connections/c1/edit');
    expect(routes).toContain('/assets/a1');
    expect(routes).toContain('/results/r1');
    expect(routes.some((r) => r.includes(':id'))).toBe(false);
    // 14 static + 2×3 suite pages + 1 check + 1 connection + 1 asset + 1 run.
    expect(routes).toHaveLength(24);
  });
});
