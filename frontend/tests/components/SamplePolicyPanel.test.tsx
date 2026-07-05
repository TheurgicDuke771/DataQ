import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getColumnPolicy, setColumnPolicy, suggestColumnPolicy } from '../../src/api/columnPolicy';
import { listColumns, type Suite } from '../../src/api/suites';
import { SamplePolicyPanel } from '../../src/components/suites/SamplePolicyPanel';

vi.mock('../../src/api/columnPolicy', () => ({
  getColumnPolicy: vi.fn(),
  setColumnPolicy: vi.fn(),
  suggestColumnPolicy: vi.fn(),
}));

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listColumns: vi.fn() }; // keep targetString real
});

const mockGet = vi.mocked(getColumnPolicy);
const mockSet = vi.mocked(setColumnPolicy);
const mockSuggest = vi.mocked(suggestColumnPolicy);
const mockListColumns = vi.mocked(listColumns);

const SUITE = {
  id: 's1',
  name: 'Orders',
  target: { table: 'ORDERS', schema: 'RETAIL' },
} as unknown as Suite;

function renderPanel(canManage = true) {
  return render(
    <AntApp>
      <SamplePolicyPanel suite={SUITE} canManage={canManage} />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('SamplePolicyPanel', () => {
  it('loads and shows the current policy', async () => {
    mockGet.mockResolvedValue({ identifier_column: 'ORDER_NUMBER', pii_columns: ['EMAIL'] });
    renderPanel();
    expect(await screen.findByText('ORDER_NUMBER')).toBeInTheDocument();
    expect(screen.getByText('EMAIL')).toBeInTheDocument();
  });

  it('saves the policy', async () => {
    mockGet.mockResolvedValue({ identifier_column: 'ORDER_NUMBER', pii_columns: [] });
    mockSet.mockResolvedValue({ identifier_column: 'ORDER_NUMBER', pii_columns: [] });
    renderPanel();
    await screen.findByText('ORDER_NUMBER');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockSet).toHaveBeenCalledTimes(1));
    expect(mockSet).toHaveBeenCalledWith('s1', {
      identifier_column: 'ORDER_NUMBER',
      pii_columns: [],
    });
  });

  it('auto-detects by profiling the target', async () => {
    mockGet.mockResolvedValue({ identifier_column: null, pii_columns: [] });
    mockSuggest.mockResolvedValue({ identifier_column: 'SKU', pii_columns: ['PHONE'] });
    renderPanel();
    await screen.findByRole('button', { name: 'Auto-detect' });

    await userEvent.click(screen.getByRole('button', { name: 'Auto-detect' }));

    await waitFor(() =>
      expect(mockSuggest).toHaveBeenCalledWith('s1', {
        table: 'ORDERS',
        schema: 'RETAIL',
        catalog: undefined,
        path: undefined,
        file_format: undefined,
      }),
    );
    expect(await screen.findByText('SKU')).toBeInTheDocument();
    expect(screen.getByText('PHONE')).toBeInTheDocument();
  });

  it('fetches the target columns lazily when a dropdown opens (#635)', async () => {
    mockGet.mockResolvedValue({ identifier_column: null, pii_columns: [] });
    mockListColumns.mockResolvedValue(['ORDER_NUMBER', 'EMAIL', 'SKU']);
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText(/Which column locates a failing row/);

    // Not fetched on mount — only when a dropdown first opens (live round-trip).
    expect(mockListColumns).not.toHaveBeenCalled();

    await user.click(screen.getAllByRole('combobox')[0]); // open the identifier Select
    await waitFor(() =>
      expect(mockListColumns).toHaveBeenCalledWith('s1', {
        table: 'ORDERS',
        schema: 'RETAIL',
        catalog: undefined,
        path: undefined,
        file_format: undefined,
      }),
    );
    // An introspected column the user never typed is offered as an option.
    expect(await screen.findByText('SKU')).toBeInTheDocument();
  });

  it('does not introspect a batch/pattern target (falls back to free entry)', async () => {
    mockGet.mockResolvedValue({ identifier_column: null, pii_columns: [] });
    const user = userEvent.setup();
    render(
      <AntApp>
        <SamplePolicyPanel
          suite={
            { id: 's2', name: 'Batch', target: { pattern: 'orders_*.csv' } } as unknown as Suite
          }
          canManage
        />
      </AntApp>,
    );
    await screen.findByText(/Which column locates a failing row/);
    await user.click(screen.getAllByRole('combobox')[0]);
    // A pattern target has no fixed table/file → never introspects.
    expect(mockListColumns).not.toHaveBeenCalled();
  });

  it('hides the mutation controls without manage rights', async () => {
    mockGet.mockResolvedValue({ identifier_column: null, pii_columns: [] });
    renderPanel(false);
    await screen.findByText(/Which column locates a failing row/);
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });
});
