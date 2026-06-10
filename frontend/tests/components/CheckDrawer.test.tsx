import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Check, createCheck, updateCheck } from '../../src/api/suites';
import { CheckDrawer } from '../../src/components/checks/CheckDrawer';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, createCheck: vi.fn(), updateCheck: vi.fn() };
});

const mockCreate = vi.mocked(createCheck);
const mockUpdate = vi.mocked(updateCheck);

function renderDrawer(props: Partial<Parameters<typeof CheckDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <CheckDrawer open suiteId="s1" onClose={vi.fn()} onSaved={vi.fn()} {...props} />
    </AntApp>,
  );
}

// antd Select renders options in a portal; pick by visible option text.
async function selectOption(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  option: string,
) {
  await user.click(screen.getByLabelText(label));
  const opt = await screen.findByText(option, { selector: '.ant-select-item-option-content' });
  await user.click(opt);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('CheckDrawer — create', () => {
  it('renders config fields for the chosen expectation and parses a value-set list', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mockCreate.mockResolvedValue({} as Check);
    renderDrawer({ onSaved });

    await user.type(screen.getByLabelText('Name'), 'status in set');
    await selectOption(user, 'Expectation', 'Column values in set');

    // Fields from the catalog appear.
    await user.type(await screen.findByLabelText('Column'), 'status');
    await user.type(screen.getByLabelText('Allowed values'), 'active, closed , pending');

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'status in set',
      expectation_type: 'expect_column_values_to_be_in_set',
      // list is split/trimmed; thresholds omitted → null
      config: { column: 'status', value_set: ['active', 'closed', 'pending'] },
      warn_threshold: null,
      fail_threshold: null,
      critical_threshold: null,
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it('does not submit without a name and expectation', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(screen.getAllByText('Name').length).toBeGreaterThan(0));
    expect(mockCreate).not.toHaveBeenCalled();
  });
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
    expect(mockCreate).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalled();
  });
});
