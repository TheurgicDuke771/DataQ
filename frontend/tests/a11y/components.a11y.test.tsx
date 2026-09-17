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
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  type AxeViolationLike,
  diffNew,
  filterGated,
  formatViolations,
  loadBaseline,
  saveBaseline,
  toRecords,
  type ViolationRecord,
} from '../../scripts/a11y/ratchet';

import { type Connection, getConnection, listConnections } from '../../src/api/connections';
import { ConnectionForm } from '../../src/components/connections/ConnectionForm';
import { NotificationsPanel } from '../../src/components/suites/NotificationsPanel';
import { SuiteForm } from '../../src/components/suites/SuiteForm';
import { getNotifications } from '../../src/api/notifications';
import { listChannels, listSuiteChannels } from '../../src/api/notificationChannels';
import { createCheck, getSuite, type Suite } from '../../src/api/suites';
import { CheckNew } from '../../src/pages/CheckNew';
import { Results } from '../../src/pages/Results';
import { listRuns, listPipelineRuns } from '../../src/api/runs';
import { listSuites } from '../../src/api/suites';

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

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASELINE_PATH = resolve(__dirname, '../../a11y-baseline.json');
const CAPTURE = process.env.A11Y_BASELINE === '1';

const captured: ViolationRecord[] = [];
const baseline = loadBaseline(BASELINE_PATH);

/** Run axe over `container`, keep only serious/critical, diff or capture per `CAPTURE`. */
async function checkA11y(surface: string, container: HTMLElement): Promise<void> {
  const results = await axe.run(container, {
    ancestry: true,
    // jsdom-appropriate: skip rules axe-core itself documents as needing real layout
    // (best-practice, not the WCAG floor this ratchet targets).
    rules: { 'color-contrast': { enabled: false } },
  });
  const gated = filterGated(results.violations as AxeViolationLike[]);
  const records = toRecords(surface, gated);

  if (CAPTURE) {
    captured.push(...records);
    return;
  }

  const relevant = baseline.filter((b) => b.surface === surface);
  const newViolations = diffNew(records, relevant);
  if (newViolations.length > 0) {
    throw new Error(
      `${surface}: ${newViolations.length} NEW serious/critical a11y violation(s) not in the baseline ` +
        `(frontend/a11y-baseline.json). Fix them, or if this is deliberately deferred work, ` +
        `regenerate the baseline with \`pnpm a11y:baseline\` and explain why in the PR:\n` +
        formatViolations(newViolations),
    );
  }
}

afterEach(() => vi.clearAllMocks());

// A single, ordered top-level describe so the CAPTURE writeback (module-scope `captured`
// array, flushed in the final test) sees every surface's records regardless of file/test
// execution order within this file.
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

  // Must run last: flushes the captured records to disk when regenerating the baseline
  // (`A11Y_BASELINE=1 pnpm a11y:baseline`). A no-op otherwise.
  it('writes the captured baseline shard when A11Y_BASELINE=1', () => {
    if (!CAPTURE) return;
    const others = loadBaseline(BASELINE_PATH).filter((r) => !r.surface.startsWith('component:'));
    saveBaseline(BASELINE_PATH, [...others, ...captured]);
  });
});
