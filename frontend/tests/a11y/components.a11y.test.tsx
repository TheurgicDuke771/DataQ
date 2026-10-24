// Component a11y floor (#1670 item 2). Runs real axe-core (not a maintained vitest
// binding — `vitest-axe` on npm is a single-maintainer fork last published over a
// year ago pinning axe-core ^4.4.2 and never promoted past 0.1.0, so per CONTRIBUTING
// rule 40's supply-chain bar we call axe-core directly instead) over each key
// component's rendered jsdom output, and ratchets against the committed baseline
// (frontend/a11y-baseline.json) shared with the Playwright route lane
// (frontend/e2e/a11y.spec.ts) via scripts/a11y/ratchet.ts.
//
// jsdom performs no layout, so layout-dependent rules (color-contrast chief among
// them) don't fire here the way they would in a real browser — that's exactly why
// item 1 also runs axe in the Playwright lane against a rendered page.
import { App as AntApp } from 'antd';
import axe from 'axe-core';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  type AxeViolationLike,
  filterGated,
  newViolationsMessage,
  ratchet,
  toRecords,
} from '../../scripts/a11y/ratchet';

import {
  browseCatalog,
  browseFiles,
  type Connection,
  getConnection,
  listConnections,
} from '../../src/api/connections';
import { ConnectionForm } from '../../src/components/connections/ConnectionForm';
import {
  CatalogBrowserButton,
  FileBrowserButton,
} from '../../src/components/suites/DatasourceBrowser';
import { NotificationsPanel } from '../../src/components/suites/NotificationsPanel';
import { SuiteForm } from '../../src/components/suites/SuiteForm';
import { getNotifications } from '../../src/api/notifications';
import { listChannels, listSuiteChannels } from '../../src/api/notificationChannels';
import { createCheck, getSuite, type Suite } from '../../src/api/suites';
import { CheckNew } from '../../src/pages/CheckNew';
import { Results } from '../../src/pages/Results';
import { listRuns, listPipelineRuns } from '../../src/api/runs';
import { search } from '../../src/api/search';
import { CommandPalette } from '../../src/components/shared/CommandPalette';
import { listSuites } from '../../src/api/suites';
import { listSeasonalPeriods, type SeasonalPeriod } from '../../src/api/seasonalPeriods';
import { getTelemetrySettings } from '../../src/api/telemetry';
import { DetectedMaskingPanel } from '../../src/components/assets/DetectedMaskingPanel';
import { DetectedPeriodsList } from '../../src/components/checks/SeasonalTrend';
import { CoverageSettingsForm } from '../../src/components/coverage-settings/CoverageSettingsForm';
import { SeasonalResultSummary } from '../../src/components/results/SeasonalResultSummary';
import { AdminCalendar } from '../../src/pages/admin/AdminCalendar';
import { TelemetryPanel } from '../../src/pages/admin/TelemetryPanel';
import { WithMe } from '../support/me';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return {
    ...actual,
    createConnection: vi.fn(),
    updateConnection: vi.fn(),
    testDraftConnection: vi.fn(),
    testConnection: vi.fn(),
    getConnection: vi.fn(),
    listConnections: vi.fn(),
    browseCatalog: vi.fn(),
    browseFiles: vi.fn(),
  };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return {
    ...actual,
    createSuite: vi.fn(),
    updateSuite: vi.fn(),
    previewBatchTarget: vi.fn(),
    createCheck: vi.fn(),
    getSuite: vi.fn(),
    listSuites: vi.fn(),
  };
});

vi.mock('../../src/api/notifications', () => ({
  getNotifications: vi.fn(),
  putNotifications: vi.fn(),
  deleteNotifications: vi.fn(),
}));

vi.mock('../../src/api/notificationChannels', () => ({
  listChannels: vi.fn(),
  listSuiteChannels: vi.fn(),
  linkSuiteChannel: vi.fn(),
  unlinkSuiteChannel: vi.fn(),
}));

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, listRuns: vi.fn(), listPipelineRuns: vi.fn() };
});

vi.mock('../../src/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/search')>();
  return { ...actual, search: vi.fn() };
});

vi.mock('../../src/api/seasonalPeriods', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/seasonalPeriods')>();
  return { ...actual, listSeasonalPeriods: vi.fn() };
});

vi.mock('../../src/api/telemetry', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/telemetry')>();
  return { ...actual, getTelemetrySettings: vi.fn() };
});

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASELINE_PATH = resolve(__dirname, '../../a11y-baseline.json');
const CAPTURE = process.env.A11Y_BASELINE === '1';

/** Run axe over `container`, keep only serious/critical, diff or capture per `CAPTURE`.
 * Each call does its own load-modify-save when capturing (see `ratchet()`), so per-test
 * ordering within this file doesn't matter — no batching/deferred-write needed. */
async function checkA11y(surface: string, container: HTMLElement): Promise<void> {
  const results = await axe.run(container, {
    ancestry: true,
    // jsdom-appropriate: skip rules axe-core itself documents as needing real layout
    // (best-practice, not the WCAG floor this ratchet targets).
    rules: { 'color-contrast': { enabled: false } },
  });
  const gated = filterGated(results.violations as AxeViolationLike[]);
  const records = toRecords(surface, gated);
  const { newViolations } = ratchet(surface, records, { path: BASELINE_PATH, capture: CAPTURE });
  const message = newViolationsMessage(surface, newViolations);
  if (message) throw new Error(message);
}

afterEach(() => vi.clearAllMocks());

describe('component a11y floor (#1670)', () => {
  it('ConnectionForm (snowflake) has no new serious/critical violations', async () => {
    const { container } = render(
      <AntApp>
        <ConnectionForm type="snowflake" onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );
    await screen.findByLabelText('Name');
    await checkA11y('component:ConnectionForm', container);
  });

  it('SuiteForm has no new serious/critical violations', async () => {
    const adlsConnection: Connection = {
      id: 'conn-adls',
      name: 'adls-prod',
      type: 'adls_gen2',
      env: 'prod',
      config: {},
      has_secret: true,
      created_by: 'u1',
    };
    const { container } = render(
      <AntApp>
        <SuiteForm connections={[adlsConnection]} onSaved={vi.fn()} onCancel={vi.fn()} />
      </AntApp>,
    );
    await screen.findByLabelText('Name');
    await checkA11y('component:SuiteForm', container);
  });

  it('NotificationsPanel has no new serious/critical violations', async () => {
    vi.mocked(getNotifications).mockResolvedValue({
      configured: true,
      enabled: true,
      alert_on: 'fail',
      has_webhook: false,
      has_slack_webhook: false,
      email_recipients: null,
    });
    vi.mocked(listChannels).mockResolvedValue([]);
    vi.mocked(listSuiteChannels).mockResolvedValue([]);
    const { container } = render(
      <MemoryRouter>
        <AntApp>
          <NotificationsPanel suiteId="s1" canManage />
        </AntApp>
      </MemoryRouter>,
    );
    await screen.findByText('Send alerts for this suite');
    await checkA11y('component:NotificationsPanel', container);
  });

  it('CheckNew (check editor) has no new serious/critical violations', async () => {
    const suite: Suite = {
      id: 's1',
      name: 'orders-suite',
      description: null,
      connection_id: 'conn1',
      target: { table: 'ORDERS' },
      created_by: 'u1',
    };
    const snowflakeConnection: Connection = {
      id: 'conn1',
      name: 'sf-dev',
      type: 'snowflake',
      env: 'dev',
      config: {},
      has_secret: true,
      created_by: 'u1',
    };
    vi.mocked(getSuite).mockResolvedValue(suite);
    vi.mocked(getConnection).mockResolvedValue(snowflakeConnection);
    vi.mocked(listConnections).mockResolvedValue([]);
    vi.mocked(createCheck).mockResolvedValue({} as never);

    const { container } = render(
      <MemoryRouter initialEntries={['/suites/s1/checks/new']}>
        <AntApp>
          <Routes>
            <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
            <Route path="/suites/:suiteId" element={<div>Suite detail</div>} />
          </Routes>
        </AntApp>
      </MemoryRouter>,
    );

    // Category chooser screen.
    await screen.findByText('Column values');
    await checkA11y('component:CheckNew.categoryChooser', container);

    // Drill into an expectation's config form — a distinct rendered state (Inputs,
    // Selects, threshold fields) the chooser screen doesn't exercise.
    const user = userEvent.setup();
    await user.click(screen.getByText('Column values'));
    await user.click(await screen.findByText('Column values in set'));
    await screen.findByLabelText('Name');
    await checkA11y('component:CheckNew.expectationConfig', container);
  });

  it('Results (runs table) has no new serious/critical violations', async () => {
    vi.mocked(listRuns).mockResolvedValue({
      items: [
        {
          id: 'r1',
          suite_id: 's1',
          status: 'succeeded',
          triggered_by: 'manual:u1',
          started_at: '2026-06-11T00:00:00Z',
          finished_at: '2026-06-11T00:00:30Z',
          checks_total: 3,
          checks_passed: 3,
          checks_failed: 0,
          worst_severity: null,
        } as never,
      ],
      total: 1,
    });
    vi.mocked(listPipelineRuns).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(listSuites).mockResolvedValue([
      {
        id: 's1',
        name: 'Orders quality',
        description: null,
        connection_id: 'c1',
        target: { table: 'ORDERS' },
        created_by: 'u1',
      },
    ]);
    vi.mocked(listConnections).mockResolvedValue([
      {
        id: 'c1',
        name: 'Snowflake DEV',
        type: 'snowflake',
        env: 'dev',
        config: {},
        has_secret: true,
        created_by: 'u1',
      },
    ]);

    const { container } = render(
      <MemoryRouter initialEntries={['/results']}>
        <Routes>
          <Route path="/results" element={<Results />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(document.querySelectorAll('tr.ant-table-row').length).toBeGreaterThan(0),
    );
    await checkA11y('component:Results', container);
  });

  it('the catalog browser modal has no new serious/critical violations', async () => {
    vi.mocked(browseCatalog).mockResolvedValue({
      level: 'catalog',
      catalog: null,
      schema: null,
      entries: [
        { name: 'dataq_retail', selectable: true },
        { name: 'odd-name', selectable: false },
      ],
      truncated: true,
      limit: 2,
    });
    render(
      <AntApp>
        <CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />
      </AntApp>,
    );
    await userEvent.setup().click(screen.getByRole('button', { name: /Browse catalog/ }));
    await screen.findByRole('button', { name: /dataq_retail/ });
    // The modal renders in a portal, outside the render container.
    await checkA11y('component:CatalogBrowser', document.body);
  });

  it('the file browser modal has no new serious/critical violations', async () => {
    vi.mocked(browseFiles).mockResolvedValue({
      root: 'landing',
      prefix: '',
      folders: ['raw/'],
      files: [{ path: 'orders.csv', size: 10, last_modified: null }],
      truncated: false,
      limit: 200,
    });
    render(
      <AntApp>
        <FileBrowserButton connectionId="c1" rootLabel="landing" mode="folder" onPick={vi.fn()} />
      </AntApp>,
    );
    await userEvent.setup().click(screen.getByRole('button', { name: /Browse folders/ }));
    await screen.findByRole('button', { name: /raw\// });
    await checkA11y('component:FileBrowser', document.body);
  });

  it('the command palette, open with results, has no new serious/critical violations', async () => {
    vi.mocked(search).mockResolvedValue({
      query: 'orders',
      groups: {
        suites: [{ id: 's1', label: 'Orders quality', sublabel: null, href: '/suites/s1' }],
        checks: [
          { id: 'c1', label: 'orders id not null', sublabel: 'Orders quality', href: '/suites/s1' },
        ],
        connections: [
          { id: 'k1', label: 'warehouse', sublabel: 'postgres · dev', href: '/connections' },
        ],
        runs: [
          {
            id: 'r1',
            label: 'Orders quality',
            sublabel: 'succeeded',
            href: '/results/r1',
            when: '2026-10-24T10:00:00Z',
          },
        ],
      },
      truncated_groups: ['checks'],
    });
    render(
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Search/ }));
    await user.type(screen.getByRole('combobox', { name: 'Search DataQ' }), 'orders');
    await screen.findAllByRole('option');
    await checkA11y('component:CommandPalette', document.body);
  });
});

/** Every column header and button in `root` has an accessible name (axe's empty-header rule is
 *  minor, so the ratchet would not catch an unnamed actions column). */
function expectNamedControls(root: HTMLElement) {
  const blank = { name: (name: string) => name.trim() === '' };
  const unnamed = [
    ...within(root).queryAllByRole('columnheader', blank),
    ...within(root).queryAllByRole('button', blank),
    ...within(root).queryAllByRole('spinbutton', blank),
    ...within(root).queryAllByRole('switch', blank),
  ];
  expect(unnamed.map((el) => el.outerHTML.slice(0, 80))).toEqual([]);
}

const ENTRY: SeasonalPeriod = {
  id: 'p1',
  name: 'Black Friday',
  kind: 'recurring',
  start_month: 11,
  start_day: 27,
  end_month: 11,
  end_day: 30,
  start_date: null,
  end_date: null,
  series: null,
  note: null,
  attached_checks: 0,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
};

const EFFECTIVE = {
  sensitivity: {
    volume: { warn: 3, fail: 4, critical: 6, source: 'default' as const },
    freshness: { warn: 3, fail: 4, critical: 6, source: 'default' as const },
    profile: { warn: 3, fail: 4, critical: 6, source: 'default' as const },
  },
  baseline: {
    window: 30,
    min_points: 7,
    seasonality: false,
    yearly: false,
    source: 'default' as const,
  },
};

describe('component a11y floor — v1.3 surfaces (CONTRIBUTING rule 44)', () => {
  it('Admin → Calendar and its entry dialog name every control', async () => {
    vi.mocked(listSeasonalPeriods).mockResolvedValue([ENTRY]);
    render(
      <MemoryRouter>
        <AntApp>
          <AdminCalendar />
        </AntApp>
      </MemoryRouter>,
    );
    await screen.findByText('Black Friday');
    expectNamedControls(document.body);
    await userEvent.setup().click(screen.getByRole('button', { name: /New entry/ }));
    const dialog = await screen.findByRole('dialog', { name: 'New calendar entry' });
    expect(within(dialog).getByRole('radiogroup', { name: 'Repeats' })).toBeInTheDocument();
    await checkA11y('component:AdminCalendar', document.body);
  });

  it('the detected-personal-data panel names its actions column and each row action', async () => {
    const { container } = render(
      <AntApp>
        <DetectedMaskingPanel
          assetId="a1"
          entries={[
            {
              column: 'contact',
              detector: 'email',
              effect: 'masks',
              first_detected_at: '2026-10-19T08:00:00Z',
              last_detected_at: '2026-10-20T08:00:00Z',
              dismissed: false,
              dismissed_at: null,
            },
          ]}
          refreshedAt="2026-10-20T08:00:00Z"
          canDismiss
          onChanged={vi.fn()}
        />
      </AntApp>,
    );
    expect(screen.getByRole('columnheader', { name: 'Actions' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss: contact' })).toBeInTheDocument();
    expectNamedControls(container);
    await checkA11y('component:DetectedMaskingPanel', container);
  });

  it('each detected period has its own promote action name', async () => {
    const occurrence = (start: string, end: string) => ({
      kind: 'seen_once' as const,
      start,
      end,
      peak_ratio: 5,
      years_seen: 1,
      range: null,
      occurrences: [{ year: 2026, start, end, peak_ratio: 5, periods: 3, backfilled_periods: 0 }],
      declared: [],
    });
    const { container } = render(
      <WithMe>
        <AntApp>
          <DetectedPeriodsList
            detected={{
              reason: null,
              periods: [
                occurrence('2026-07-01', '2026-07-03'),
                occurrence('2026-11-27', '2026-11-30'),
              ],
              thresholds: { ratio: 1.5, min_consecutive: 2, same_time_days: 7, level: 'median' },
              history: {
                unit: 'day',
                points: 200,
                backfilled: 0,
                evaluated: 200,
                first: '2026-01-01',
                last: '2026-12-01',
                years: 0,
              },
            }}
            suiteId="s1"
            checkId="c1"
            yearly
            onPromoted={vi.fn()}
          />
        </AntApp>
      </WithMe>,
    );
    expect(
      screen.getByRole('button', { name: 'Promote to calendar: 2026-07-01 – 2026-07-03' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Promote to calendar: 2026-11-27 – 2026-11-30' }),
    ).toBeInTheDocument();
    await checkA11y('component:DetectedPeriodsList', container);
  });

  it('the coverage settings form names every control', async () => {
    const { container } = render(
      <AntApp>
        <CoverageSettingsForm
          stored={null}
          below={EFFECTIVE}
          current={EFFECTIVE}
          scope="Set for the workspace"
          clearLabel="Reset to defaults"
          saving={false}
          onSave={vi.fn()}
          onClear={vi.fn()}
        />
      </AntApp>,
    );
    expectNamedControls(container);
    await checkA11y('component:CoverageSettingsForm', container);
  });

  it('the usage-telemetry panel has no new serious/critical violations', async () => {
    vi.mocked(getTelemetrySettings).mockResolvedValue({
      enabled: false,
      stored: true,
      sink: 'off',
      sink_ready: false,
      events: [{ name: 'page_view', attributes: { route: ['dashboard'] } }],
      updated_by: null,
      updated_at: null,
    });
    const { container } = render(
      <AntApp>
        <TelemetryPanel />
      </AntApp>,
    );
    await screen.findByRole('switch', { name: 'Usage telemetry' });
    expectNamedControls(container);
    await checkA11y('component:TelemetryPanel', container);
  });

  it('"How this period was judged" has no new serious/critical violations', async () => {
    const { container } = render(
      <SeasonalResultSummary
        observed={{
          period: { start: '2026-11-27', end: '2026-11-27', unit: 'day' },
          z_score: 0.4,
          mean: 5050,
          stddev: 125,
          yearly: { applied: false, reason: 'no_earlier_year' },
          declared: { inside: null, verdict_changed: false, missing: [] },
        }}
      />,
    );
    await checkA11y('component:SeasonalResultSummary', container);
  });
});
