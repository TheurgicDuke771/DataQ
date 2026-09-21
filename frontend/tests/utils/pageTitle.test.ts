import { describe, expect, it } from 'vitest';

import { pageTitleFor } from '../../src/utils/pageTitle';

const ID = '3f2b9c1e-0000-4000-8000-000000000001';

// One entry per route declared in App.tsx + pages/admin/routes.tsx.
const ROUTES: [string, string][] = [
  ['/dashboard', 'Dashboard · DataQ'],
  ['/connections', 'Connections · DataQ'],
  ['/connections/new', 'New connection · DataQ'],
  [`/connections/${ID}/edit`, 'Edit connection · DataQ'],
  ['/suites', 'Suites · DataQ'],
  ['/suites/new', 'New suite · DataQ'],
  [`/suites/${ID}`, 'Suite · DataQ'],
  [`/suites/${ID}/edit`, 'Edit suite · DataQ'],
  [`/suites/${ID}/checks/new`, 'New check · DataQ'],
  [`/suites/${ID}/checks/${ID}/edit`, 'Edit check · DataQ'],
  ['/assets', 'Assets · DataQ'],
  [`/assets/${ID}`, 'Asset · DataQ'],
  ['/results', 'Results · DataQ'],
  [`/results/${ID}`, 'Run · DataQ'],
  ['/profile', 'Profile · DataQ'],
  ['/admin', 'Overview · Admin · DataQ'],
  ['/admin/overview', 'Overview · Admin · DataQ'],
  ['/admin/members', 'Members · Admin · DataQ'],
  ['/admin/suites', 'Suites · Admin · DataQ'],
  ['/admin/integrations', 'Integrations · Admin · DataQ'],
  ['/admin/settings', 'Settings · Admin · DataQ'],
  ['/admin/compliance', 'Compliance · Admin · DataQ'],
];

describe('pageTitleFor', () => {
  it.each(ROUTES)('%s → %s', (path, title) => {
    expect(pageTitleFor(path)).toBe(title);
  });

  it('gives every route a title no other route shares, except the /admin alias', () => {
    const titles = ROUTES.filter(([p]) => p !== '/admin').map(([, t]) => t);
    expect(new Set(titles).size).toBe(titles.length);
  });

  it('does not mistake "new" for an id — the specific pattern wins', () => {
    expect(pageTitleFor('/suites/new')).not.toBe(pageTitleFor(`/suites/${ID}`));
    expect(pageTitleFor('/connections/new')).toBe('New connection · DataQ');
  });

  it('names an unknown path, and an unknown admin page, without throwing', () => {
    expect(pageTitleFor('/no-such-route')).toBe('Not found · DataQ');
    expect(pageTitleFor('/admin/whatever')).toBe('Admin · DataQ');
    expect(pageTitleFor('/results/')).toBe('Results · DataQ');
  });
});
