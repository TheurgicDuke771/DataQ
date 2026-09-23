const APP = 'DataQ';

const ADMIN_PAGES: Record<string, string> = {
  overview: 'Overview',
  members: 'Members',
  suites: 'Suites',
  integrations: 'Integrations',
  settings: 'Settings',
  compliance: 'Compliance',
};

// First match wins, so the specific patterns sit above the general ones.
const ROUTES: [RegExp, string][] = [
  [/^\/dashboard$/, 'Dashboard'],
  [/^\/connections\/new$/, 'New connection'],
  [/^\/connections\/[^/]+\/edit$/, 'Edit connection'],
  [/^\/connections$/, 'Connections'],
  [/^\/suites\/new$/, 'New suite'],
  [/^\/suites\/[^/]+\/checks\/new$/, 'New check'],
  [/^\/suites\/[^/]+\/checks\/[^/]+\/edit$/, 'Edit check'],
  [/^\/suites\/[^/]+\/edit$/, 'Edit suite'],
  [/^\/suites\/[^/]+$/, 'Suite'],
  [/^\/suites$/, 'Suites'],
  [/^\/assets\/[^/]+$/, 'Asset'],
  [/^\/assets$/, 'Assets'],
  [/^\/results\/[^/]+$/, 'Run'],
  [/^\/results$/, 'Results'],
  [/^\/profile$/, 'Profile'],
  // Redirects admins to /admin/settings; a non-admin stays here on the Forbidden page.
  [/^\/settings$/, 'Settings'],
];

/** The document title for a path: `Results · DataQ`. Every route gets a distinct one, so a
 *  screen reader announces where a navigation landed and history entries can be told apart. A
 *  page that knows something more specific (a run's suite name) may still set its own after. */
export function pageTitleFor(pathname: string): string {
  const path = pathname.replace(/\/+$/, '') || '/';
  const admin = /^\/admin(?:\/([^/]+))?$/.exec(path);
  if (admin) {
    const page = ADMIN_PAGES[admin[1] ?? 'overview'];
    return page ? `${page} · Admin · ${APP}` : `Admin · ${APP}`;
  }
  const hit = ROUTES.find(([pattern]) => pattern.test(path));
  if (hit) return `${hit[1]} · ${APP}`;
  return path === '/' ? APP : `Not found · ${APP}`;
}
