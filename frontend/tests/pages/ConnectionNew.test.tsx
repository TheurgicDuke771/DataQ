import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createConnection } from '../../src/api/connections';
import { ConnectionNew } from '../../src/pages/ConnectionNew';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, createConnection: vi.fn() };
});

const mockCreate = vi.mocked(createConnection);

// Render the page and a stub /connections route so the post-create navigation
// has somewhere to land.
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/connections/new']}>
      <AntApp>
        <Routes>
          <Route path="/connections/new" element={<ConnectionNew />} />
          <Route path="/connections" element={<div>Connections list</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('ConnectionNew', () => {
  it('categorizes the source picker with Orchestration first', () => {
    renderPage();
    // The category labels render (Orchestration leads, datasources fan out).
    expect(screen.getByText('Orchestration')).toBeInTheDocument();
    expect(screen.getByText('Warehouses')).toBeInTheDocument();
    expect(screen.getByText('Cloud Storage')).toBeInTheDocument();
    // Orchestration is the first category in document order (ADR 0022).
    const labels = screen.getAllByText(/Orchestration|Warehouses|Lakehouses|Cloud Storage/);
    expect(labels[0]).toHaveTextContent('Orchestration');
    // Its lead-in note frames orchestration as optional (cron/manual also run suites).
    expect(
      screen.getByText(/Optional — connect Azure Data Factory or Airflow/),
    ).toBeInTheDocument();
    // A datasource and an orchestration source are each offered.
    expect(screen.getByText('Snowflake')).toBeInTheDocument();
    expect(screen.getByText('Azure Data Factory')).toBeInTheDocument();
  });

  it('does not leak name/env when re-picking a different type', async () => {
    const user = userEvent.setup();
    renderPage();

    // Pick Snowflake, fill the name, then go back to the picker.
    await user.click(screen.getByText('Snowflake'));
    await user.type(screen.getByLabelText('Name'), 'sf-dev');
    await user.click(screen.getAllByRole('button', { name: 'Back' })[0]);

    // Re-pick a different type — the form must start clean (no leftover name).
    await user.click(screen.getByText('Airflow'));
    expect(screen.getByRole('heading', { name: /New Airflow connection/ })).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toHaveValue('');
  });

  it('picks a type, fills the form, creates, and navigates to the list', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({
      id: 'c1',
      name: 'sf-dev',
      type: 'snowflake',
      env: 'dev',
      config: {},
      has_secret: true,
      created_by: 'u1',
    });
    renderPage();

    // Step 1: pick Snowflake → the type-specific form appears.
    await user.click(screen.getByText('Snowflake'));
    expect(screen.getByRole('heading', { name: /New Snowflake connection/ })).toBeInTheDocument();

    // Step 2: fill name + env + the Snowflake-required fields + secret.
    await user.type(screen.getByLabelText('Name'), 'sf-dev');
    await user.click(screen.getByLabelText('Environment'));
    await user.click(await screen.findByText('DEV'));
    for (const label of ['Account', 'User', 'Database', 'Schema', 'Warehouse']) {
      await user.type(screen.getByLabelText(label), `${label.toLowerCase()}-val`);
    }
    await user.type(screen.getByLabelText('Password'), 'sekret');

    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    const payload = mockCreate.mock.calls[0][0];
    expect(payload).toMatchObject({
      name: 'sf-dev',
      type: 'snowflake',
      env: 'dev',
      secret: 'sekret',
    });
    // Navigated to the list on success.
    expect(await screen.findByText('Connections list')).toBeInTheDocument();
  });
});
