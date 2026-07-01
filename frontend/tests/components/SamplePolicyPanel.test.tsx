import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getColumnPolicy, setColumnPolicy, suggestColumnPolicy } from '../../src/api/columnPolicy';
import type { Suite } from '../../src/api/suites';
import { SamplePolicyPanel } from '../../src/components/suites/SamplePolicyPanel';

vi.mock('../../src/api/columnPolicy', () => ({
  getColumnPolicy: vi.fn(),
  setColumnPolicy: vi.fn(),
  suggestColumnPolicy: vi.fn(),
}));

const mockGet = vi.mocked(getColumnPolicy);
const mockSet = vi.mocked(setColumnPolicy);
const mockSuggest = vi.mocked(suggestColumnPolicy);

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

  it('hides the mutation controls without manage rights', async () => {
    mockGet.mockResolvedValue({ identifier_column: null, pii_columns: [] });
    renderPanel(false);
    await screen.findByText(/Which column locates a failing row/);
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });
});
