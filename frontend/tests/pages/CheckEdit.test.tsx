import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, getConnection } from '../../src/api/connections';
import {
  type Check,
  getCheck,
  getSuite,
  listCheckVersions,
  type Suite,
  updateCheck,
} from '../../src/api/suites';
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
  };
});

const mockGetSuite = vi.mocked(getSuite);
const mockGetCheck = vi.mocked(getCheck);
const mockGetConnection = vi.mocked(getConnection);
const mockUpdate = vi.mocked(updateCheck);
const mockVersions = vi.mocked(listCheckVersions);

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
      expectation_type: 'expect_column_values_to_be_between',
      config: { column: 'amount', min_value: 0, max_value: 100 },
      // The stored override survives an edit that never touches the field —
      // otherwise every rename would silently revert it to the derived guess.
      dimension: 'accuracy',
      warn_threshold: 5,
      fail_threshold: 10,
      critical_threshold: null,
    });
    expect(await screen.findByText('Suite detail')).toBeInTheDocument();
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

// Issue #768 — expect_column_values_to_be_of_type's `type_` field needs a
// datasource-tailored hint (GX compares against different type vocabularies per
// execution engine), rendered only for this expectation.
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
