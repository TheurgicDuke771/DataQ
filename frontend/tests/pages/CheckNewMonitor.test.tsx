import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, getConnection, listConnections } from '../../src/api/connections';
import { type Check, createCheck, getSuite, type Suite } from '../../src/api/suites';
import { CheckNew } from '../../src/pages/CheckNew';

// Monitor authoring (ADR 0012) is SQL-datasource-gated, so this suite mocks a
// Snowflake connection — only then do the Freshness/Volume categories appear.
// listConnections is a real (jsdom-XHR) call unless mocked — jsdom 30 hangs such a
// request against a relative URL with no server instead of rejecting quickly (jsdom 29
// did), so the component's `state` never settles without this mock.
vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, getConnection: vi.fn(), listConnections: vi.fn() };
});
vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, getSuite: vi.fn(), createCheck: vi.fn() };
});

const mockGetSuite = vi.mocked(getSuite);
const mockGetConnection = vi.mocked(getConnection);
const mockCreate = vi.mocked(createCheck);
const mockListConnections = vi.mocked(listConnections);
mockListConnections.mockResolvedValue([]);

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

function renderPage(connectionOverride: Connection = connection) {
  mockGetSuite.mockResolvedValue(suite);
  mockGetConnection.mockResolvedValue(connectionOverride);
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
      engine: 'gx',
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
      engine: 'gx',
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
      engine: 'gx',
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
    // ...and never reach the submitted config, even though antd's Form preserves an unmounted
    // field's last value by default.
    expect(mockCreate).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({
        config: { target_metric: 'row_count', window: 14, min_points: 7, seasonality: false },
      }),
    );
  });

  it('inline-errors when window shrinks below an untouched min_points default (review finding: 3 <= min_points <= window)', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText('Anomaly'));
    await user.click(await screen.findByText('Learns a rolling baseline', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'orders row anomaly');
    await user.click(screen.getByLabelText('Target metric'));
    await user.click(await screen.findByTitle('Row count'));

    // Shrink window to 5 without ever touching min_points (still its untouched
    // default of 7) — 7 > 5 violates the backend's min_points <= window bound.
    const windowField = screen.getByLabelText('Window (observations)', { exact: false });
    await user.clear(windowField);
    await user.type(windowField, '5');
    await user.type(screen.getByLabelText('Fail ≥'), '3');
    await user.click(screen.getByRole('button', { name: 'Create check' }));

    expect(
      await screen.findByText('Minimum points before scoring must be ≤ window'),
    ).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('accepts a shrunk window once min_points is brought back into range', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Anomaly'));
    await user.click(await screen.findByText('Learns a rolling baseline', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'orders row anomaly');
    await user.click(screen.getByLabelText('Target metric'));
    await user.click(await screen.findByTitle('Row count'));

    const windowField = screen.getByLabelText('Window (observations)', { exact: false });
    await user.clear(windowField);
    await user.type(windowField, '5');
    const minPointsField = screen.getByLabelText('Minimum points before scoring', {
      exact: false,
    });
    await user.clear(minPointsField);
    await user.type(minPointsField, '3');
    await user.type(screen.getByLabelText('Fail ≥'), '3');
    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({
        config: { target_metric: 'row_count', window: 5, min_points: 3, seasonality: false },
      }),
    );
  });

  it('rejects a zero fail threshold inline (backend requires a POSITIVE fail/critical threshold, not merely a set one)', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText('Anomaly'));
    await user.click(await screen.findByText('Learns a rolling baseline', { exact: false }));
    await user.type(await screen.findByLabelText('Name'), 'zero threshold');
    await user.click(screen.getByLabelText('Target metric'));
    await user.click(await screen.findByTitle('Row count'));
    await user.type(screen.getByLabelText('Fail ≥'), '0');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    expect(await screen.findByText('Set a fail or critical threshold')).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
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

describe('CheckNew — Snowflake DMF engine (ADR 0036)', () => {
  it('offers a Snowflake DMF category with all four metrics', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByText('Snowflake DMF'));
    expect(screen.getByText('Null count (DMF)')).toBeInTheDocument();
    expect(screen.getByText('Null percent (DMF)')).toBeInTheDocument();
    expect(screen.getByText('Duplicate count (DMF)')).toBeInTheDocument();
    expect(screen.getByText('Unique count (DMF)')).toBeInTheDocument();
  });

  it('authors a dmf:null_count check with engine dmf and a threshold', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Snowflake DMF'));
    await user.click(await screen.findByText('Null count (DMF)'));
    await user.type(await screen.findByLabelText('Name'), 'orders id null count');
    await user.type(screen.getByLabelText('Column'), 'order_id');
    await user.type(screen.getByLabelText('Fail ≥'), '5');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({
        engine: 'dmf',
        expectation_type: 'dmf:null_count',
        config: { column: 'order_id' },
        fail_threshold: 5,
      }),
    );
  });

  it('offers no threshold fields for dmf:unique_count (unbandable) and submits none', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Snowflake DMF'));
    await user.click(await screen.findByText('Unique count (DMF)'));
    expect(screen.queryByLabelText('Fail ≥')).toBeNull();
    expect(screen.queryByText('Severity thresholds', { exact: false })).toBeNull();

    await user.type(await screen.findByLabelText('Name'), 'orders id unique count');
    await user.type(screen.getByLabelText('Column'), 'order_id');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({
        engine: 'dmf',
        expectation_type: 'dmf:unique_count',
        warn_threshold: null,
        fail_threshold: null,
        critical_threshold: null,
      }),
    );
  });

  it('shows an Engine picker on Freshness for a Snowflake connection, defaulting to gx', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText('Freshness'));
    await user.click(await screen.findByText('How stale is the target?', { exact: false }));
    expect(await screen.findByLabelText('Engine')).toBeInTheDocument();
  });

  it('authors a freshness check on the dmf engine when switched via the Engine picker', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await user.click(await screen.findByText('Freshness'));
    await user.click(await screen.findByText('How stale is the target?', { exact: false }));
    await user.click(await screen.findByLabelText('Engine'));
    await user.click(await screen.findByTitle('Snowflake DMF (native)'));
    await user.type(await screen.findByLabelText('Name'), 'orders fresh (dmf)');
    await user.type(screen.getByLabelText('Timestamp column'), 'loaded_at');
    await user.type(screen.getByLabelText('Fail ≥'), '48');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({
        engine: 'dmf',
        kind: 'freshness',
        expectation_type: 'monitor:freshness',
      }),
    );
  });

  it('warns on the DMF engine option when the connection probe found it unavailable (#1867)', async () => {
    const user = userEvent.setup();
    renderPage({
      ...connection,
      engine_capabilities: {
        dmf: {
          available: false,
          reason: "the connection's role cannot invoke Snowflake system data metric functions",
        },
      },
    });

    await user.click(await screen.findByText('Freshness'));
    await user.click(await screen.findByText('How stale is the target?', { exact: false }));

    // The caveat renders up front (not just inside the dropdown) so it's seen before opening it.
    expect(
      await screen.findByText(/DMF was unavailable the last time this connection was tested/),
    ).toBeInTheDocument();

    // Still offered (not disabled — the connection's grants can change after this page loads).
    await user.click(await screen.findByLabelText('Engine'));
    expect(await screen.findByTitle('Snowflake DMF (native) ⚠')).toBeInTheDocument();
  });
});
