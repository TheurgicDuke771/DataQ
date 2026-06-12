import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Connection } from '../../src/api/connections';
import { createSuite, type Suite, updateSuite } from '../../src/api/suites';
import { SuiteDrawer } from '../../src/components/suites/SuiteDrawer';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, createSuite: vi.fn(), updateSuite: vi.fn() };
});

const mockCreate = vi.mocked(createSuite);
const mockUpdate = vi.mocked(updateSuite);

const connection: Connection = {
  id: 'conn1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

function renderDrawer(props: Partial<Parameters<typeof SuiteDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <SuiteDrawer open connections={[connection]} onClose={vi.fn()} onSaved={vi.fn()} {...props} />
    </AntApp>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('SuiteDrawer — create', () => {
  it('submits the new suite and calls onSaved', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mockCreate.mockResolvedValue({
      id: 's1',
      name: 'orders-suite',
      description: null,
      connection_id: 'conn1',
      target: null,
      created_by: 'u1',
    });
    renderDrawer({ onSaved });

    await user.type(screen.getByLabelText('Name'), 'orders-suite');
    await user.click(screen.getByLabelText('Connection'));
    await user.click(
      await screen.findByText('sf-dev · Snowflake · DEV', {
        selector: '.ant-select-item-option-content',
      }),
    );
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate).toHaveBeenCalledWith({
      name: 'orders-suite',
      description: null,
      connection_id: 'conn1',
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it('does not submit when required fields are missing', async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(screen.getAllByText('Name').length).toBeGreaterThan(0));
    expect(mockCreate).not.toHaveBeenCalled();
  });
});

describe('SuiteDrawer — edit', () => {
  const existing: Suite = {
    id: 's1',
    name: 'orders-suite',
    description: 'old desc',
    connection_id: 'conn1',
    target: null,
    created_by: 'u1',
  };

  it('prefills, locks the connection, and submits an update', async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    mockUpdate.mockResolvedValue({ ...existing, name: 'orders-suite-2' });
    renderDrawer({ suite: existing, onSaved });

    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue('orders-suite'));
    // Connection is locked in edit mode.
    expect(screen.getByRole('combobox')).toBeDisabled();

    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'orders-suite-2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith('s1', {
      name: 'orders-suite-2',
      description: 'old desc',
    });
    expect(mockCreate).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalled();
  });
});
