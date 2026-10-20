import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, getConnection } from '../../src/api/connections';
import {
  type Check,
  getCheck,
  getCheckBaseline,
  getSuite,
  listCheckHistory,
  listCheckVersions,
  listColumns,
  restoreCheckVersion,
  type Suite,
  updateCheck,
} from '../../src/api/suites';
import {
  getDetectedSeasonalPeriods,
  listSeasonalPeriods,
  promoteDetectedPeriod,
  type SeasonalPeriod,
} from '../../src/api/seasonalPeriods';
import { WithMe } from '../support/me';
import { CheckEdit } from '../../src/pages/CheckEdit';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, getConnection: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return {
    ...actual,
    getSuite: vi.fn(),
    getCheck: vi.fn(),
    updateCheck: vi.fn(),
    listCheckVersions: vi.fn(),
    listCheckHistory: vi.fn(),
    getCheckBaseline: vi.fn(),
    restoreCheckVersion: vi.fn(),
    listColumns: vi.fn(),
  };
});

vi.mock('../../src/api/seasonalPeriods', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/seasonalPeriods')>();
  return {
    ...actual,
    listSeasonalPeriods: vi.fn(),
    getDetectedSeasonalPeriods: vi.fn(),
    promoteDetectedPeriod: vi.fn(),
  };
});

const mockGetSuite = vi.mocked(getSuite);
const mockGetCheck = vi.mocked(getCheck);
const mockGetConnection = vi.mocked(getConnection);
const mockUpdate = vi.mocked(updateCheck);
const mockVersions = vi.mocked(listCheckVersions);
const mockHistory = vi.mocked(listCheckHistory);
const mockBaseline = vi.mocked(getCheckBaseline);
const mockRestore = vi.mocked(restoreCheckVersion);
const mockColumns = vi.mocked(listColumns);
const mockCalendar = vi.mocked(listSeasonalPeriods);
const mockDetected = vi.mocked(getDetectedSeasonalPeriods);
const mockPromote = vi.mocked(promoteDetectedPeriod);

const suite: Suite = {
  id: 's1',
  name: 'orders-suite',
  description: null,
  connection_id: 'conn1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

const connection: Connection = {
  id: 'conn1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

const existing: Check = {
  id: 'chk1',
  suite_id: 's1',
  name: 'amount range',
  kind: 'expectation',
  expectation_type: 'expect_column_values_to_be_between',
  config: { column: 'amount', min_value: 0, max_value: 100 },
  // Deliberately an OVERRIDE: this type derives to 'validity', so a prefill bug
  // that seeded the derived default instead of the stored value would show up.
  dimension: 'accuracy',
  warn_threshold: 5,
  fail_threshold: 10,
  critical_threshold: null,
  alert_snoozed_until: null,
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites/s1/checks/chk1/edit']}>
      <AntApp>
        <Routes>
          <Route path="/suites/:suiteId/checks/:checkId/edit" element={<CheckEdit />} />
          <Route path="/suites/:suiteId" element={<div>Suite detail</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('CheckEdit', () => {
  it('prefills config + thresholds, updates, and navigates back', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(existing);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
    expect(screen.getByLabelText('Warn ≥')).toHaveValue('5');

    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'amount range v2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    // `kind` is immutable on update, so the PATCH omits it.
    expect(mockUpdate).toHaveBeenCalledWith('s1', 'chk1', {
      name: 'amount range v2',
      engine: 'gx',
      expectation_type: 'expect_column_values_to_be_between',
      config: { column: 'amount', min_value: 0, max_value: 100 },
      // The stored override survives an edit that never touches the field —
      // otherwise every rename would silently revert it to the derived guess.
      dimension: 'accuracy',
      warn_threshold: 5,
      fail_threshold: 10,
      critical_threshold: null,
    });
    // The switch was not moved, so the save says nothing about it: a disable made
    // elsewhere while this page was open is not undone.
    expect(mockUpdate.mock.calls[0][2]).not.toHaveProperty('enabled');
    expect(await screen.findByText('Suite detail')).toBeInTheDocument();
  });

  it.each([
    { flip: false, sent: undefined },
    { flip: true, sent: true },
  ])(
    'saves a switched-off check sending enabled=$sent when the switch is flipped=$flip',
    async ({ flip, sent }) => {
      const user = userEvent.setup();
      mockGetSuite.mockResolvedValue(suite);
      mockGetCheck.mockResolvedValue({ ...existing, enabled: false });
      mockGetConnection.mockResolvedValue(connection);
      mockUpdate.mockResolvedValue({ ...existing, enabled: sent ?? false });
      renderPage();

      const toggle = await screen.findByRole('switch', { name: 'Enabled' });
      await waitFor(() => expect(toggle).not.toBeChecked());
      if (flip) await user.click(toggle);
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
      expect((mockUpdate.mock.calls[0][2] as { enabled?: boolean }).enabled).toBe(sent);
    },
  );

  it('edits a custom SQL check in the plain editor, with no generate panel', async () => {
    // The panel belongs to the "Generate from a description" entry point on the add page.
    // An existing check already has its SQL; offering to regenerate over it is not an edit.
    mockGetSuite.mockResolvedValue(suite);
    mockGetConnection.mockResolvedValue(connection);
    mockGetCheck.mockResolvedValue({
      ...existing,
      name: 'no negative totals',
      expectation_type: 'unexpected_rows_expectation',
      config: { unexpected_rows_query: 'SELECT * FROM {batch} WHERE total < 0' },
      dimension: null,
      warn_threshold: null,
      fail_threshold: null,
    });
    renderPage();

    expect(await screen.findByDisplayValue('no negative totals')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Generate SQL/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Describe the rule in plain language/)).not.toBeInTheDocument();
  });

  it('surfaces a 422 whose field matches no form input instead of silently no-opping', async () => {
    const { AxiosError, AxiosHeaders } = await import('axios');
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockResolvedValue(connection);
    const err = new AxiosError('refused');
    err.response = {
      status: 422,
      statusText: '',
      data: {
        error: {
          code: 'check_config_invalid',
          message: 'thresholds can never fire on this type',
          detail: { field: 'thresholds' },
        },
      },
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    };
    mockUpdate.mockRejectedValue(err);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('thresholds can never fire on this type')).toBeInTheDocument();
  });

  it('opens the version-history drawer from the History button (#280)', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockResolvedValue(connection);
    mockVersions.mockResolvedValue([
      {
        version_no: 1,
        name: 'amount range',
        kind: 'expectation',
        expectation_type: 'expect_column_values_to_be_between',
        config: { column: 'amount' },
        warn_threshold: null,
        fail_threshold: null,
        critical_threshold: null,
        changed_by: 'u1',
        changed_by_name: 'Ed Editor',
        created_at: '2026-06-15T10:00:00Z',
      },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));

    await user.click(screen.getByRole('button', { name: /History/ }));

    expect(await screen.findByText(/History — /)).toBeInTheDocument();
    await waitFor(() => expect(mockVersions).toHaveBeenCalledWith('s1', 'chk1'));
    expect(await screen.findByText('v1')).toBeInTheDocument();
    // The fixture suite carries no `my_permission` (absent = not edit-capable,
    // same as a viewer) — #283's Restore action must not appear.
    expect(screen.queryByRole('button', { name: /Restore this version/ })).not.toBeInTheDocument();
  });

  it('opens the metric-trend drawer from the Trend button, mounting CheckTrend only while open (#594)', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockResolvedValue(connection);
    mockHistory.mockResolvedValue([
      { run_id: 'r1', status: 'warn', metric_value: 6, created_at: '2026-06-15T10:00:00Z' },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));

    // Before the drawer opens, CheckTrend hasn't mounted — no fetch yet.
    expect(mockHistory).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /Trend/ }));

    expect(await screen.findByText('amount range — trend')).toBeInTheDocument();
    await waitFor(() => expect(mockHistory).toHaveBeenCalledWith('s1', 'chk1', 90));
    // A non-anomaly check never fetches a baseline.
    expect(mockBaseline).not.toHaveBeenCalled();
    // The check's own thresholds (Warn ≥ 5, Fail ≥ 10) drive the trend's bands.
    expect(await screen.findByText('Thresholds: Warn ≥ 5 · Fail ≥ 10')).toBeInTheDocument();
  });

  it('still loads when the connection is unreadable (shared suite)', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockRejectedValue(new Error('forbidden'));
    renderPage();

    // The form renders from the check even though the connection 403s.
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
  });
});

describe('CheckEdit — Snowflake DMF engine (ADR 0036)', () => {
  const existingFreshnessDmf: Check = {
    id: 'chk2',
    suite_id: 's1',
    name: 'orders fresh',
    kind: 'freshness',
    engine: 'dmf',
    expectation_type: 'monitor:freshness',
    config: { column: 'loaded_at' },
    dimension: 'timeliness',
    warn_threshold: null,
    fail_threshold: 48,
    critical_threshold: null,
    alert_snoozed_until: null,
  };

  it('prefills the Engine picker from the stored engine on a freshness check', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existingFreshnessDmf);
    mockGetConnection.mockResolvedValue(connection);
    renderPage();

    await waitFor(() => expect(screen.getByTitle('Snowflake DMF (native)')).toBeInTheDocument());
  });

  it('switches an existing freshness check from dmf to gx and saves it', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existingFreshnessDmf);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(existingFreshnessDmf);
    renderPage();

    await waitFor(() => expect(screen.getByTitle('Snowflake DMF (native)')).toBeInTheDocument());
    await user.click(screen.getByLabelText('Engine'));
    await user.click(await screen.findByTitle('Great Expectations (gx)'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith(
      's1',
      'chk2',
      expect.objectContaining({ engine: 'gx', expectation_type: 'monitor:freshness' }),
    );
  });

  const existingDmfNullCount: Check = {
    id: 'chk3',
    suite_id: 's1',
    name: 'order_id nulls',
    kind: 'expectation',
    engine: 'dmf',
    expectation_type: 'dmf:null_count',
    config: { column: 'order_id' },
    dimension: 'completeness',
    warn_threshold: null,
    fail_threshold: 5,
    critical_threshold: null,
    alert_snoozed_until: null,
  };

  it('resolves the engine to gx (not left blank) when switching type into Freshness', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existingDmfNullCount);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(existingDmfNullCount);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('order_id'));
    await user.click(screen.getByLabelText('Expectation'));
    await user.click(await screen.findByRole('option', { name: 'Freshness' }));
    await waitFor(() => expect(screen.getByTitle('Great Expectations (gx)')).toBeInTheDocument());
    await user.type(await screen.findByLabelText('Timestamp column'), 'loaded_at');
    await user.type(screen.getByLabelText('Fail ≥'), '48');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith(
      's1',
      'chk3',
      expect.objectContaining({ engine: 'gx', expectation_type: 'monitor:freshness' }),
    );
  });

  it('clears stale thresholds when switching to dmf:unique_count (unbandable)', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existingDmfNullCount);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(existingDmfNullCount);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Fail ≥')).toHaveValue('5'));
    await user.click(screen.getByLabelText('Expectation'));
    await user.click(await screen.findByTitle('Unique count (DMF)'));
    expect(screen.queryByLabelText('Fail ≥')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith(
      's1',
      'chk3',
      expect.objectContaining({
        expectation_type: 'dmf:unique_count',
        warn_threshold: null,
        fail_threshold: null,
        critical_threshold: null,
      }),
    );
  });
});

describe('CheckEdit — custom Snowflake DMF', () => {
  const existingCustom: Check = {
    id: 'chk4',
    suite_id: 's1',
    name: 'negative amounts',
    kind: 'expectation',
    engine: 'dmf',
    expectation_type: 'dmf:custom',
    config: { function: 'DATAQ_DB.QUALITY.NEG_AMOUNT', columns: ['AMOUNT', 'DISCOUNT'] },
    dimension: null,
    warn_threshold: null,
    fail_threshold: 1,
    critical_threshold: null,
    alert_snoozed_until: null,
  };
  const withListing = (dmf: Connection['engine_capabilities']): Connection => ({
    ...connection,
    engine_capabilities: dmf,
  });

  it('prefills the function and columns and round-trips them on save', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existingCustom);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(existingCustom);
    renderPage();

    await waitFor(() =>
      expect(screen.getByLabelText('Data metric function')).toHaveValue(
        'DATAQ_DB.QUALITY.NEG_AMOUNT',
      ),
    );
    expect(screen.getByLabelText('Columns')).toHaveValue('AMOUNT, DISCOUNT');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith(
      's1',
      'chk4',
      expect.objectContaining({
        engine: 'dmf',
        expectation_type: 'dmf:custom',
        config: { function: 'DATAQ_DB.QUALITY.NEG_AMOUNT', columns: ['AMOUNT', 'DISCOUNT'] },
      }),
    );
  });

  it('suggests the DMFs the connection’s role could use at its last test', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existingCustom);
    mockGetConnection.mockResolvedValue(
      withListing({
        dmf: {
          available: true,
          custom_functions: [
            { name: 'DATAQ_DB.QUALITY.GAP', signature: '(TABLE(NUMBER, NUMBER)) RETURN NUMBER' },
          ],
          custom_functions_truncated: false,
        },
      }),
    );
    renderPage();

    const field = await screen.findByLabelText('Data metric function');
    expect(screen.getByTestId('custom-dmf-listing')).toHaveTextContent(
      'Suggestions are the 1 custom DMFs the role could use',
    );
    await user.clear(field);
    await user.type(field, 'gap');
    expect(
      await screen.findByText('DATAQ_DB.QUALITY.GAP (TABLE(NUMBER, NUMBER)) RETURN NUMBER'),
    ).toBeInTheDocument();
  });

  it('says plainly when the role could see no custom DMFs', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existingCustom);
    mockGetConnection.mockResolvedValue(
      withListing({ dmf: { available: true, custom_functions: [] } }),
    );
    renderPage();

    expect(await screen.findByTestId('custom-dmf-listing')).toHaveTextContent(
      'No custom DMFs were visible to this connection’s role at its last test',
    );
  });
});

describe('customDmfListingNote', () => {
  it('distinguishes never listed, listing failed, and truncated', async () => {
    const { customDmfListingNote } = await import('../../src/components/checks/customDmf');
    expect(customDmfListingNote(undefined)).toMatch(/haven’t been listed/);
    expect(customDmfListingNote({ available: true })).toMatch(/haven’t been listed/);
    expect(
      customDmfListingNote({
        available: true,
        custom_functions: null,
        custom_functions_reason: 'could not list',
      }),
    ).toBe('could not list');
    expect(
      customDmfListingNote({
        available: true,
        custom_functions: [{ name: 'D.S.F', signature: '' }],
        custom_functions_truncated: true,
        custom_functions_unlisted: 2,
      }),
    ).toMatch(/\(the first 200 only\).*2 more have quoted lower-case names/);
  });
});

describe('CheckEdit — restore a version (#283)', () => {
  const editableSuite: Suite = { ...suite, my_permission: 'edit' };
  const versionRow = {
    version_no: 1,
    name: 'amount range v0',
    kind: 'expectation',
    expectation_type: 'expect_column_values_to_be_between',
    config: { column: 'amount' },
    warn_threshold: null,
    fail_threshold: null,
    critical_threshold: null,
    changed_by: 'u1',
    changed_by_name: 'Ed Editor',
    created_at: '2026-06-15T10:00:00Z',
  };

  it('offers Restore on a non-current row when the caller can edit', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(editableSuite);
    mockGetCheck.mockResolvedValue(existing);
    mockGetConnection.mockResolvedValue(connection);
    mockVersions.mockResolvedValue([{ ...versionRow, version_no: 2 }, versionRow]);
    renderPage();
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));

    await user.click(screen.getByRole('button', { name: /History/ }));
    await screen.findByText('v2');

    expect(screen.getAllByRole('button', { name: /Restore this version/ })).toHaveLength(1);
  });

  it('restoring reloads the suite/check so the form re-seeds from the restored state', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(editableSuite);
    mockGetCheck.mockResolvedValueOnce(existing);
    mockGetConnection.mockResolvedValue(connection);
    mockVersions.mockResolvedValue([{ ...versionRow, version_no: 2 }, versionRow]);
    mockRestore.mockResolvedValue({ ...existing, name: 'amount range v0' });
    // The reload after restore refetches the check — return the restored name.
    mockGetCheck.mockResolvedValueOnce({ ...existing, name: 'amount range v0' });
    renderPage();
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));

    await user.click(screen.getByRole('button', { name: /History/ }));
    await user.click(await screen.findByRole('button', { name: /Restore this version/ }));
    await user.click(await screen.findByRole('button', { name: 'Restore' }));

    await waitFor(() => expect(mockRestore).toHaveBeenCalledWith('s1', 'chk1', 1));
    // The page's own fetch re-ran (suite + check + connection), not just the
    // drawer's own version list.
    await waitFor(() => expect(mockGetCheck).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Edit “amount range v0”')).toBeInTheDocument();
  });
});

// Issue #768 — expect_column_values_to_be_of_type's `type_` field needs a datasource-tailored hint
// (GX compares against different type vocabularies per execution engine).
describe('CheckEdit — type_ hint (issue #768)', () => {
  const typeCheck: Check = {
    id: 'chk2',
    suite_id: 's1',
    name: 'amount is decimal',
    kind: 'expectation',
    expectation_type: 'expect_column_values_to_be_of_type',
    config: { column: 'amount', type_: 'DECIMAL(38, 0)' },
    warn_threshold: null,
    fail_threshold: null,
    critical_threshold: null,
    alert_snoozed_until: null,
  };

  it('shows the SQL-dialect hint for a Snowflake suite', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(typeCheck);
    mockGetConnection.mockResolvedValue(connection); // type: 'snowflake'
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Type')).toHaveValue('DECIMAL(38, 0)'));
    expect(screen.getByText(/DECIMAL\(38, 0\)/)).toBeInTheDocument();
    expect(screen.getByText(/observed_value shows the exact/i)).toBeInTheDocument();
  });

  it('shows the pandas-dtype hint for a Unity Catalog suite', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(typeCheck);
    mockGetConnection.mockResolvedValue({ ...connection, type: 'unity_catalog' });
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Type')).toHaveValue('DECIMAL(38, 0)'));
    expect(screen.getByText(/int64/)).toBeInTheDocument();
    // Stable substring of the pandas-path wording (PR-#781 review: UC/CSV string
    // columns are object dtype — object or str both pass).
    expect(screen.getByText(/`object` or `str` both pass/)).toBeInTheDocument();
  });

  it('does not render the type_ hint for other expectations', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(existing); // expect_column_values_to_be_between
    mockGetConnection.mockResolvedValue(connection);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
    expect(screen.queryByLabelText('Type')).not.toBeInTheDocument();
  });
});

describe('CheckEdit — DQ dimension (ADR 0038, #124)', () => {
  it('prefills the STORED dimension, not the type-derived default', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetConnection.mockResolvedValue(connection);
    mockGetCheck.mockResolvedValue(existing); // stored 'accuracy'; type derives 'validity'
    renderPage();

    // If the create-page default leaked into edit mode, this would read Validity.
    expect(await screen.findByText(/Accuracy —/)).toBeInTheDocument();
  });

  it('does not silently classify a check saved as unclassified', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetConnection.mockResolvedValue(connection);
    // A derivable TYPE whose stored dimension is null — someone deliberately
    // cleared it. Merely opening the editor and renaming must not reclassify it.
    mockGetCheck.mockResolvedValue({ ...existing, dimension: null });
    mockUpdate.mockResolvedValue({ ...existing, dimension: null });
    renderPage();

    const name = await screen.findByLabelText('Name');
    await user.clear(name);
    await user.type(name, 'renamed');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][2]).toMatchObject({ dimension: undefined });
  });
});

describe('CheckEdit — re-deriving on an expectation-type change (#124 review)', () => {
  it('re-derives the dimension when the expectation type changes', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetConnection.mockResolvedValue(connection);
    // Stored 'accuracy' on a between-check.
    mockGetCheck.mockResolvedValue(existing);
    renderPage();

    const typeSelect = await screen.findByLabelText('Expectation');
    await user.click(typeSelect);
    await user.click(await screen.findByTitle('Column values unique'));

    expect(await screen.findByText(/Uniqueness —/)).toBeInTheDocument();
  });
});

// #593 — the anomaly monitor kind.
describe('CheckEdit — anomaly monitor (#593)', () => {
  const rowCountAnomaly: Check = {
    id: 'chk3',
    suite_id: 's1',
    name: 'orders row-count anomaly',
    kind: 'anomaly',
    expectation_type: 'monitor:anomaly',
    config: { target_metric: 'row_count', window: 21, min_points: 10, seasonality: true },
    warn_threshold: null,
    fail_threshold: 3,
    critical_threshold: null,
    alert_snoozed_until: null,
  };

  const freshnessAnomaly: Check = {
    id: 'chk4',
    suite_id: 's1',
    name: 'orders freshness anomaly',
    kind: 'anomaly',
    expectation_type: 'monitor:anomaly',
    config: {
      target_metric: 'freshness_age_hours',
      column: 'loaded_at',
      window: 14,
      min_points: 7,
      seasonality: false,
    },
    warn_threshold: null,
    fail_threshold: 4,
    critical_threshold: null,
    alert_snoozed_until: null,
  };

  it('locks the monitor type (kind immutable, like freshness/volume) and prefills a row_count anomaly’s fields', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(rowCountAnomaly);
    mockGetConnection.mockResolvedValue(connection);
    renderPage();

    const monitorSelect = await screen.findByLabelText('Monitor');
    expect(monitorSelect).toBeDisabled();
    expect(await screen.findByText('Row count')).toBeInTheDocument();
    // Both are `optional: true` fields, so the label renders with the
    // " (optional)" suffix ConfigFieldItem appends.
    await waitFor(() =>
      expect(screen.getByLabelText('Window (observations)', { exact: false })).toHaveValue('21'),
    );
    expect(screen.getByLabelText('Minimum points before scoring', { exact: false })).toHaveValue(
      '10',
    );
    // row_count is not freshness_age_hours — no column field.
    expect(screen.queryByLabelText('Timestamp column')).not.toBeInTheDocument();
  });

  it('prefills a freshness_age_hours anomaly’s conditional column field', async () => {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(freshnessAnomaly);
    mockGetConnection.mockResolvedValue(connection);
    renderPage();

    expect(await screen.findByText('Freshness age (hours)')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('Timestamp column')).toHaveValue('loaded_at'));
  });

  it('round-trips config intact on save with no edits (the pre-catalog-entry 422/silent-wipe bug)', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(rowCountAnomaly);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(rowCountAnomaly);
    renderPage();

    await waitFor(() =>
      expect(screen.getByLabelText('Window (observations)', { exact: false })).toHaveValue('21'),
    );
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', 'chk3', {
      name: 'orders row-count anomaly',
      engine: 'gx',
      expectation_type: 'monitor:anomaly',
      config: { target_metric: 'row_count', window: 21, min_points: 10, seasonality: true },
      dimension: undefined,
      warn_threshold: null,
      fail_threshold: 3,
      critical_threshold: null,
    });
  });
});

describe('CheckEdit — per-period anomaly with the seasonal calendar', () => {
  const calendar: SeasonalPeriod[] = [
    {
      id: 'bf',
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
      attached_checks: 1,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ];
  const stored = {
    target_metric: 'period_row_count',
    date_column: 'ordered_at',
    period: 'day',
    timezone: 'UTC',
    settle_hours: 6,
    backfill: true,
    window: 14,
    min_points: 7,
    seasonality: false,
    yearly: true,
    seasonal_periods: ['bf'],
  };
  const perPeriod: Check = {
    id: 'chk5',
    suite_id: 's1',
    name: 'orders per day',
    kind: 'anomaly',
    expectation_type: 'monitor:anomaly',
    config: stored,
    warn_threshold: null,
    fail_threshold: 3,
    critical_threshold: null,
    alert_snoozed_until: null,
  };

  function load(check: Check) {
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(check);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(check);
    mockCalendar.mockResolvedValue(calendar);
    renderPage();
  }

  it('shows the attached entries by name and saves them unchanged', async () => {
    const user = userEvent.setup();
    load(perPeriod);
    expect(
      await screen.findByText('Black Friday (27 Nov – 30 Nov, every year)'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate.mock.calls[0][2].config).toEqual(stored);
  });

  it('adds nothing to a check saved before the calendar existed', async () => {
    const user = userEvent.setup();
    const before = Object.fromEntries(
      Object.entries(stored).filter(([k]) => k !== 'seasonal_periods' && k !== 'yearly'),
    );
    load({ ...perPeriod, config: before });
    await screen.findByText('Turn on yearly seasonality to attach calendar entries.');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const config = mockUpdate.mock.calls[0][2].config ?? {};
    expect(config).not.toHaveProperty('seasonal_periods');
    // The switch shows its default, but a key the check never had is not added on save.
    expect(config).not.toHaveProperty('yearly');
  });

  it('warns before a save that turns yearly off detaches the entries', async () => {
    const user = userEvent.setup();
    load(perPeriod);
    await screen.findByText('Black Friday (27 Nov – 30 Nov, every year)');
    await user.click(screen.getByRole('switch', { name: /Yearly seasonality/ }));
    expect(await screen.findByTestId('calendar-detach-warning')).toHaveTextContent(
      'Saving removes 1 attached calendar entry',
    );
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const config = mockUpdate.mock.calls[0][2].config ?? {};
    expect(config.yearly).toBe(false);
    expect(config).not.toHaveProperty('seasonal_periods');
  });

  it('suggests the target’s columns for the date column', async () => {
    const user = userEvent.setup();
    mockColumns.mockResolvedValue(['ordered_at', 'shipped_at', 'amount']);
    load(perPeriod);
    const input = await screen.findByLabelText('Date column');
    await user.click(input);
    await waitFor(() =>
      expect(mockColumns).toHaveBeenCalledWith('s1', expect.objectContaining({ table: 'ORDERS' })),
    );
    await user.clear(input);
    await user.type(input, 'ship');
    expect((await screen.findAllByText('shipped_at')).length).toBeGreaterThan(0);
  });

  it('keeps an entry attached from the trend, and the unsaved edits, on the next save', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetCheck.mockResolvedValue(perPeriod);
    mockGetConnection.mockResolvedValue(connection);
    mockUpdate.mockResolvedValue(perPeriod);
    mockCalendar.mockResolvedValue(calendar);
    mockHistory.mockResolvedValue([]);
    mockBaseline.mockResolvedValue(null);
    mockDetected.mockResolvedValue({
      reason: null,
      periods: [
        {
          kind: 'seen_once',
          start: '2026-03-02',
          end: '2026-03-04',
          peak_ratio: 3,
          years_seen: 1,
          range: null,
          occurrences: [],
          declared: [],
        },
      ],
      thresholds: { ratio: 1.5, min_consecutive: 2, same_time_days: 7, level: 'median' },
      history: {
        unit: 'day',
        points: 10,
        backfilled: 0,
        evaluated: 5,
        first: null,
        last: null,
        years: 0,
      },
    });
    mockPromote.mockResolvedValue({ ...calendar[0], id: 'spring', name: 'Spring' });
    render(
      <MemoryRouter initialEntries={['/suites/s1/checks/chk5/edit']}>
        <AntApp>
          <WithMe role="admin">
            <Routes>
              <Route path="/suites/:suiteId/checks/:checkId/edit" element={<CheckEdit />} />
              <Route path="/suites/:suiteId" element={<div>Suite detail</div>} />
            </Routes>
          </WithMe>
        </AntApp>
      </MemoryRouter>,
    );
    await screen.findByText('Black Friday (27 Nov – 30 Nov, every year)');
    // An unsaved edit the attach must not wipe.
    const fail = screen.getByLabelText('Fail ≥');
    await user.clear(fail);
    await user.type(fail, '4');

    await user.click(screen.getByRole('button', { name: /Trend/ }));
    await user.click(await screen.findByRole('button', { name: 'Promote to calendar' }));
    const dialog = (await screen.findByText('Add this period to the calendar')).closest(
      '.ant-modal',
    ) as HTMLElement;
    await user.type(within(dialog).getByLabelText('Name'), 'Spring');
    await user.click(within(dialog).getByRole('button', { name: 'Add to calendar' }));
    await waitFor(() =>
      expect(mockPromote).toHaveBeenCalledWith(expect.objectContaining({ attach: true })),
    );

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const payload = mockUpdate.mock.calls[0][2];
    expect(payload.config?.seasonal_periods).toEqual(['bf', 'spring']);
    expect(payload.fail_threshold).toBe(4);
  });
});
