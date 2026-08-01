import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, getConnection } from '../../src/api/connections';
import { type Check, createCheck, getSuite, type Suite } from '../../src/api/suites';
import { CheckNew } from '../../src/pages/CheckNew';

// Monitor authoring (ADR 0012) is SQL-datasource-gated, so this suite mocks a
// Snowflake connection — only then do the Freshness/Volume categories appear.
vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, getConnection: vi.fn() };
});
vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, getSuite: vi.fn(), createCheck: vi.fn() };
});

const mockGetSuite = vi.mocked(getSuite);
const mockGetConnection = vi.mocked(getConnection);
const mockCreate = vi.mocked(createCheck);

const suite: Suite = {
  id: 's1',
  name: 'orders-suite',
  description: null,
  connection_id: 'conn1',
  target: { table: 'ORDERS', schema: 'RETAIL' },
  created_by: 'u1',
};

const connection: Connection = {
  id: 'conn1',
  name: 'retail-sf',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

function renderPage() {
  mockGetSuite.mockResolvedValue(suite);
  mockGetConnection.mockResolvedValue(connection);
  return render(
    <MemoryRouter initialEntries={['/suites/s1/checks/new']}>
      <AntApp>
        <Routes>
          <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
          <Route path="/suites/:suiteId" element={<div>Suite detail</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('CheckNew — monitor authoring (ADR 0012)', () => {
  it('offers Freshness + Volume categories on a SQL datasource', async () => {
    renderPage();
    expect(await screen.findByText('Freshness')).toBeInTheDocument();
    expect(screen.getByText('Volume')).toBeInTheDocument();
  });

  it('authors a freshness monitor with kind + config + threshold', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Freshness'));
    // Step 2 → the Freshness spec card (label appears as both category + card).
    await user.click(await screen.findByText('How stale is the target?', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'orders fresh');
    await user.type(screen.getByLabelText('Timestamp column'), 'loaded_at');
    await user.type(screen.getByLabelText('Fail ≥'), '48');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'orders fresh',
      kind: 'freshness',
      expectation_type: 'monitor:freshness',
      config: { column: 'loaded_at' },
      dimension: 'timeliness',
      warn_threshold: null,
      fail_threshold: 48,
      critical_threshold: null,
    });
  });

  it('blocks a freshness monitor with no fail/critical threshold (the #426 guard)', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText('Freshness'));
    await user.click(await screen.findByText('How stale is the target?', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'no threshold');
    await user.type(screen.getByLabelText('Timestamp column'), 'loaded_at');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    expect(await screen.findByText('Set a fail or critical threshold')).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('authors a volume monitor with min/max rows (no threshold required)', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Volume'));
    await user.click(
      await screen.findByText('Did the load deliver the expected row count?', { exact: false }),
    );
    await user.type(await screen.findByLabelText('Name'), 'orders volume');
    await user.type(screen.getByLabelText('Minimum rows'), '1000');
    await user.type(screen.getByLabelText('Maximum rows'), '5000');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'orders volume',
      kind: 'volume',
      expectation_type: 'monitor:volume',
      config: { min_rows: 1000, max_rows: 5000 },
      dimension: 'completeness',
      warn_threshold: null,
      fail_threshold: null,
      critical_threshold: null,
    });
  });
});

describe('CheckNew — anomaly authoring (#593, stricter SQL-only gating than freshness/volume)', () => {
  it('offers Anomaly on a SQL datasource', async () => {
    renderPage();
    expect(await screen.findByText('Anomaly')).toBeInTheDocument();
  });

  it('authors a row_count anomaly with defaults + threshold, and never renders the column field', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Anomaly'));
    // Step 2 → the Anomaly spec card, picked by its description (the label
    // 'Anomaly' collides with the category name and the page header).
    await user.click(await screen.findByText('Learns a rolling baseline', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'orders row anomaly');

    await user.click(screen.getByLabelText('Target metric'));
    await user.click(await screen.findByTitle('Row count'));

    // row_count is not freshness_age_hours — the conditional column field must
    // never mount (ConfigField.showWhen).
    expect(screen.queryByLabelText('Timestamp column')).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Fail ≥'), '3');
    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'orders row anomaly',
      kind: 'anomaly',
      expectation_type: 'monitor:anomaly',
      // window/min_points/seasonality submit their catalog defaults (14/7/false)
      // even though the author never touched them.
      config: { target_metric: 'row_count', window: 14, min_points: 7, seasonality: false },
      dimension: undefined,
      warn_threshold: null,
      fail_threshold: 3,
      critical_threshold: null,
    });
  });

  it('authors a freshness_age_hours anomaly WITH a column, and never leaks a stale column after switching back to row_count', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Anomaly'));
    await user.click(await screen.findByText('Learns a rolling baseline', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'orders freshness anomaly');

    await user.click(screen.getByLabelText('Target metric'));
    await user.click(await screen.findByTitle('Freshness age (hours)'));
    await user.type(await screen.findByLabelText('Timestamp column'), 'loaded_at');

    // Switch back to row_count — the column field must vanish from the form...
    await user.click(screen.getByLabelText('Target metric'));
    await user.click(await screen.findByTitle('Row count'));
    expect(screen.queryByLabelText('Timestamp column')).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Fail ≥'), '3');
    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    // ...and never reach the submitted config, even though antd's Form
    // preserves an unmounted field's last value by default — the backend
    // REJECTS `column` when target_metric is row_count (#593).
    expect(mockCreate).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({
        config: { target_metric: 'row_count', window: 14, min_points: 7, seasonality: false },
      }),
    );
  });

  it('blocks an anomaly check with no fail/critical threshold (the #426 guard, reused)', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText('Anomaly'));
    await user.click(await screen.findByText('Learns a rolling baseline', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'no threshold');
    await user.click(screen.getByLabelText('Target metric'));
    await user.click(await screen.findByTitle('Row count'));

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    expect(await screen.findByText('Set a fail or critical threshold')).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });
});
