// The pages the demo records and then replays. No Playwright import: the Vitest coverage test
// (tests/demo/routeCoverage.test.ts) reads ROUTE_PATTERNS to hold this list to the routers.

/** Every `<Route path>` the demo covers, parameters as `:id`. */
export const ROUTE_PATTERNS = [
  '/dashboard',
  '/assets',
  '/assets/:id',
  '/connections',
  '/connections/new',
  '/connections/:id/edit',
  '/suites',
  '/suites/new',
  '/suites/:id',
  '/suites/:id/edit',
  '/suites/:id/checks/new',
  '/suites/:id/checks/:id/edit',
  '/results',
  '/results/:id',
  '/results/:id/compare/:id',
  '/profile',
  '/admin/overview',
  '/admin/members',
  '/admin/suites',
  '/admin/settings',
  '/admin/calendar',
  '/admin/compliance',
  '/admin/integrations',
] as const;

type Row = { id: string; suite_id?: string };
/** Reads one API list (path under /api/v1) — live while recording, from the recording after. */
export type ListReader = (path: string) => Promise<Row[]>;

/** Expand the patterns into concrete URLs for every seeded suite, check, asset, run and connection. */
export async function discoverRoutes(list: ListReader): Promise<string[]> {
  const [suites, connections, assets, runs] = await Promise.all([
    list('/suites'),
    list('/connections'),
    list('/assets'),
    list('/runs'),
  ]);
  const checks = await Promise.all(
    suites.map(async (s) => ({ suite: s.id, checks: await list(`/suites/${s.id}/checks`) })),
  );
  const expand = (pattern: string): string[] => {
    switch (pattern) {
      case '/assets/:id':
        return assets.map((a) => `/assets/${a.id}`);
      case '/connections/:id/edit':
        return connections.map((c) => `/connections/${c.id}/edit`);
      case '/suites/:id':
      case '/suites/:id/edit':
      case '/suites/:id/checks/new':
        return suites.map((s) => pattern.replace(':id', s.id));
      case '/suites/:id/checks/:id/edit':
        return checks.flatMap((c) =>
          c.checks.map((check) => `/suites/${c.suite}/checks/${check.id}/edit`),
        );
      case '/results/:id':
        return runs.map((r) => `/results/${r.id}`);
      case '/results/:id/compare/:id': {
        // One comparison per suite: its two most recent runs, the older one as the base.
        const bySuite = new Map<string, Row[]>();
        for (const r of runs) {
          if (!r.suite_id) continue;
          bySuite.set(r.suite_id, [...(bySuite.get(r.suite_id) ?? []), r]);
        }
        return [...bySuite.values()]
          .filter((list) => list.length >= 2)
          .map(([newest, older]) => `/results/${older.id}/compare/${newest.id}`);
      }
      default:
        if (pattern.includes(':id')) throw new Error(`no expansion for ${pattern}`);
        return [pattern];
    }
  };
  return ROUTE_PATTERNS.flatMap(expand);
}
