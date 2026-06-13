import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createTriggerBinding,
  deleteTriggerBinding,
  listTriggerBindings,
  setTriggerBindingEnabled,
  type TriggerBinding,
} from '../../src/api/triggerBindings';
import { TriggersPanel } from '../../src/components/suites/TriggersPanel';

vi.mock('../../src/api/triggerBindings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/triggerBindings')>();
  return {
    ...actual,
    listTriggerBindings: vi.fn(),
    createTriggerBinding: vi.fn(),
    setTriggerBindingEnabled: vi.fn(),
    deleteTriggerBinding: vi.fn(),
  };
});

const mockList = vi.mocked(listTriggerBindings);
const mockCreate = vi.mocked(createTriggerBinding);
const mockToggle = vi.mocked(setTriggerBindingEnabled);
const mockDelete = vi.mocked(deleteTriggerBinding);

const BINDING: TriggerBinding = {
  id: 'b1',
  provider: 'adf',
  pipeline_or_dag_id: 'nightly-load',
  env: 'prod',
  suite_id: 's1',
  enabled: true,
};

function renderPanel(props: Partial<Parameters<typeof TriggersPanel>[0]> = {}) {
  return render(
    <AntApp>
      <TriggersPanel suiteId="s1" canManage {...props} />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('TriggersPanel', () => {
  it('lists bindings with pipeline id, provider, and env', async () => {
    mockList.mockResolvedValue([BINDING]);
    renderPanel();

    expect(await screen.findByText('nightly-load')).toBeInTheDocument();
    expect(screen.getByText('Azure Data Factory')).toBeInTheDocument();
    expect(screen.getByText('PROD')).toBeInTheDocument();
  });

  it('shows an empty state when there are no triggers', async () => {
    mockList.mockResolvedValue([]);
    renderPanel();

    expect(
      await screen.findByText('No triggers — this suite runs only on manual / scheduled runs.'),
    ).toBeInTheDocument();
  });

  it('adds a binding from the provider/pipeline/env form', async () => {
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue(BINDING);
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText(/No triggers/);

    // Two Selects in the add form: [provider, env]; the Input is a textbox.
    const [providerSelect, envSelect] = screen.getAllByRole('combobox');
    await user.click(providerSelect);
    await user.click(await screen.findByText('Azure Data Factory'));
    await user.type(screen.getByPlaceholderText('Pipeline / DAG id'), 'nightly-load');
    await user.click(envSelect);
    await user.click(await screen.findByText('PROD'));
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({
        provider: 'adf',
        env: 'prod',
        pipeline_or_dag_id: 'nightly-load',
        suite_id: 's1',
      }),
    );
  });

  it('toggles a binding enabled state', async () => {
    mockList.mockResolvedValue([BINDING]);
    mockToggle.mockResolvedValue({ ...BINDING, enabled: false });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('nightly-load');

    await user.click(screen.getByRole('switch', { name: 'Enable nightly-load' }));

    await waitFor(() => expect(mockToggle).toHaveBeenCalledWith('b1', false));
  });

  it('removes a binding', async () => {
    mockList.mockResolvedValue([BINDING]);
    mockDelete.mockResolvedValue();
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('nightly-load');

    await user.click(screen.getByRole('button', { name: 'Remove nightly-load' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('b1'));
  });

  it('is read-only for a non-editor (no add form, no toggle/remove)', async () => {
    mockList.mockResolvedValue([BINDING]);
    renderPanel({ canManage: false });
    await screen.findByText('nightly-load');

    expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove nightly-load' })).not.toBeInTheDocument();
    // The enabled state is shown read-only as a tag.
    expect(screen.getByText('enabled')).toBeInTheDocument();
  });
});
