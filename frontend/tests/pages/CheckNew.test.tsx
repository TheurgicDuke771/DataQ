import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, getConnection } from '../../src/api/connections';
import { type Check, createCheck, getSuite, type Suite } from '../../src/api/suites';
import { CheckNew } from '../../src/pages/CheckNew';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, getConnection: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, createCheck: vi.fn(), getSuite: vi.fn() };
});

const mockCreate = vi.mocked(createCheck);
const mockGetSuite = vi.mocked(getSuite);
const mockGetConnection = vi.mocked(getConnection);

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

// Render the page at /suites/s1/checks/new with a stub suite-detail route so the
// post-create navigation has somewhere to land.
function renderPage() {
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

describe('CheckNew', () => {
  it('shows the datasource-agnostic categories plus the reserved (disabled) one', () => {
    renderPage();
    expect(screen.getByText('Column values')).toBeInTheDocument();
    expect(screen.getByText('Table shape')).toBeInTheDocument();
    // Schema drift is still a reserved roadmap marker.
    expect(screen.getByText('Schema drift')).toBeInTheDocument();
    // Freshness/Volume are now real categories, but SQL-datasource-gated — with no
    // connection loaded here they're hidden (no longer reserved cards either).
    expect(screen.queryByText('Freshness')).not.toBeInTheDocument();
    expect(screen.queryByText('Volume')).not.toBeInTheDocument();
  });

  it('walks category → expectation → config, creates, and navigates back', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    // Step 1 → pick a category.
    await user.click(screen.getByText('Column values'));
    // Step 2 → pick an expectation within it.
    await user.click(await screen.findByText('Column values in set'));
    // Step 3 → fill config (value-set list is split/trimmed) + name.
    await user.type(await screen.findByLabelText('Name'), 'status in set');
    await user.type(screen.getByLabelText('Column'), 'status');
    await user.type(screen.getByLabelText('Allowed values'), 'active, closed , pending');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'status in set',
      kind: 'expectation',
      expectation_type: 'expect_column_values_to_be_in_set',
      config: { column: 'status', value_set: ['active', 'closed', 'pending'] },
      warn_threshold: null,
      fail_threshold: null,
      critical_threshold: null,
    });
    expect(await screen.findByText('Suite detail')).toBeInTheDocument();
  });

  it('rejects a delimiter-only value-set instead of saving an empty config', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByText('Column values'));
    await user.click(await screen.findByText('Column values in set'));
    await user.type(await screen.findByLabelText('Name'), 'bad set');
    await user.type(screen.getByLabelText('Column'), 'status');
    await user.type(screen.getByLabelText('Allowed values'), ' , ');

    await user.click(screen.getByRole('button', { name: 'Create check' }));

    expect(await screen.findByText('Enter at least one value')).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });
});

// Issue #768 — expect_column_values_to_be_of_type's `type_` field needs a
// datasource-tailored hint (GX compares against different type vocabularies per
// execution engine): the dialect's fully-qualified type on SQL-backed Snowflake,
// vs the Python value type name on every pandas-backed runner (UC, flat files,
// Iceberg).
describe('CheckNew — type_ hint (issue #768)', () => {
  const pickTypeExpectation = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByText('Column values'));
    await user.click(await screen.findByText('Column values are of type'));
  };

  it('shows the SQL-dialect hint for a Snowflake suite and creates with type_', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetConnection.mockResolvedValue(snowflakeConnection);
    mockCreate.mockResolvedValue({} as Check);
    renderPage();

    await pickTypeExpectation(user);
    expect(await screen.findByText(/dialect/i)).toBeInTheDocument();
    expect(screen.getByText(/DECIMAL\(38, 0\)/)).toBeInTheDocument();

    await user.type(screen.getByLabelText('Name'), 'amount is decimal');
    await user.type(screen.getByLabelText('Column'), 'amount');
    await user.type(screen.getByLabelText('Type'), 'DECIMAL(38, 0)');
    await user.click(screen.getByRole('button', { name: 'Create check' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'amount is decimal',
      kind: 'expectation',
      expectation_type: 'expect_column_values_to_be_of_type',
      config: { column: 'amount', type_: 'DECIMAL(38, 0)' },
      warn_threshold: null,
      fail_threshold: null,
      critical_threshold: null,
    });
  });

  it('shows the pandas-dtype hint for a flat-file (S3) suite', async () => {
    const user = userEvent.setup();
    mockGetSuite.mockResolvedValue(suite);
    mockGetConnection.mockResolvedValue({ ...snowflakeConnection, type: 's3' });
    renderPage();

    await pickTypeExpectation(user);
    expect(await screen.findByText(/int64/)).toBeInTheDocument();
    // Stable substring of the pandas-path wording (PR-#781 review: UC/CSV string
    // columns are object dtype — object or str both pass).
    expect(screen.getByText(/`object` or `str` both pass/)).toBeInTheDocument();
    // Nullable-integer upcast caveat is present too.
    expect(screen.getByText(/NULLs report `float64`/)).toBeInTheDocument();
  });

  it('shows the generic fallback hint before the connection has loaded', async () => {
    const user = userEvent.setup();
    // getSuite/getConnection never resolve within the test, so the hint renders
    // from the catalog's static default the whole time.
    mockGetSuite.mockReturnValue(new Promise(() => {}));
    renderPage();

    await pickTypeExpectation(user);
    expect(await screen.findByText(/execution engine/i)).toBeInTheDocument();
  });
});
