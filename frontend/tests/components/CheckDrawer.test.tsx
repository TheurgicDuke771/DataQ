import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Check, listCheckVersions, updateCheck } from '../../src/api/suites';
import { CheckDrawer } from '../../src/components/checks/CheckDrawer';

// The drawer is edit-only — creating a check is the dedicated /checks/new page
// (CheckNew). So only the update path is mocked + exercised here.
vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, updateCheck: vi.fn(), listCheckVersions: vi.fn() };
});

const mockUpdate = vi.mocked(updateCheck);
const mockVersions = vi.mocked(listCheckVersions);

function renderDrawer(props: Partial<Parameters<typeof CheckDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <CheckDrawer open suiteId="s1" target={null} onClose={vi.fn()} onSaved={vi.fn()} {...props} />
    </AntApp>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('CheckDrawer — edit', () => {
  const existing: Check = {
    id: 'chk1',
    suite_id: 's1',
    name: 'amount range',
    kind: 'expectation',
    expectation_type: 'expect_column_values_to_be_between',
    config: { column: 'amount', min_value: 0, max_value: 100 },
    warn_threshold: 5,
    fail_threshold: 10,
    critical_threshold: null,
  };

  it('prefills config + thresholds and submits an update', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mockUpdate.mockResolvedValue(existing);
    renderDrawer({ check: existing, onSaved });

    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
    expect(screen.getByLabelText('Warn ≥')).toHaveValue('5');

    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'amount range v2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', 'chk1', {
      name: 'amount range v2',
      expectation_type: 'expect_column_values_to_be_between',
      config: { column: 'amount', min_value: 0, max_value: 100 },
      warn_threshold: 5,
      fail_threshold: 10,
      critical_threshold: null,
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it('opens the version-history drawer from the History button (#280)', async () => {
    const user = userEvent.setup();
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
    renderDrawer({ check: existing });
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));

    await user.click(screen.getByRole('button', { name: /History/ }));

    expect(await screen.findByText(/History — /)).toBeInTheDocument();
    await waitFor(() => expect(mockVersions).toHaveBeenCalledWith('s1', 'chk1'));
    expect(await screen.findByText('v1')).toBeInTheDocument();
  });

  it('closes the history sub-drawer when the edited check changes (#280)', async () => {
    const user = userEvent.setup();
    mockVersions.mockResolvedValue([]);
    const { rerender } = renderDrawer({ check: existing });
    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
    await user.click(screen.getByRole('button', { name: /History/ }));
    expect(await screen.findByText(/History — /)).toBeInTheDocument();

    // Switch the editor to a different check — the left-open history must close,
    // not linger showing the previous check.
    const other: Check = { ...existing, id: 'chk2', name: 'row count' };
    rerender(
      <AntApp>
        <CheckDrawer
          open
          suiteId="s1"
          target={null}
          onClose={vi.fn()}
          onSaved={vi.fn()}
          check={other}
        />
      </AntApp>,
    );
    await waitFor(() => expect(screen.queryByText(/History — /)).not.toBeInTheDocument());
  });

  it('groups the expectation picker by category', async () => {
    const user = userEvent.setup();
    renderDrawer({ check: existing });

    await waitFor(() => expect(screen.getByLabelText('Column')).toHaveValue('amount'));
    await user.click(screen.getByLabelText('Expectation'));
    // antd renders optgroup headers with the category labels.
    expect(
      await screen.findByText('Column values', { selector: '.ant-select-item-group' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Table shape', { selector: '.ant-select-item-group' }),
    ).toBeInTheDocument();
  });
});
