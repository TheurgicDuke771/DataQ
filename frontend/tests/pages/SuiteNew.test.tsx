import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import { createSuite, type Suite } from '../../src/api/suites';
import { SuiteNew } from '../../src/pages/SuiteNew';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnections: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, createSuite: vi.fn() };
});

const mockListConnections = vi.mocked(listConnections);
const mockCreateSuite = vi.mocked(createSuite);

const datasource: Connection = {
  id: 'conn1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

const orchestration: Connection = {
  id: 'conn2',
  name: 'adf-prod',
  type: 'adf',
  env: 'prod',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

// Render the page plus stub routes so the post-create navigation (to Add Check)
// and Cancel (to the list) have somewhere to land.
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites/new']}>
      <AntApp>
        <Routes>
          <Route path="/suites/new" element={<SuiteNew />} />
          <Route path="/suites" element={<div>Suites list</div>} />
          <Route path="/suites/:suiteId/checks/new" element={<div>Add check page</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('SuiteNew', () => {
  it('creates a suite and continues to the Add Check page', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([datasource]);
    mockCreateSuite.mockResolvedValue({
      id: 's1',
      name: 'orders-suite',
      description: null,
      connection_id: 'conn1',
      target: { table: 'orders' },
      created_by: 'u1',
    } satisfies Suite);

    renderPage();

    await user.type(await screen.findByLabelText('Name'), 'orders-suite');
    await user.click(screen.getByLabelText('Connection'));
    await user.click(await screen.findByText(/sf-dev · Snowflake · DEV/));
    // Snowflake target field appears once the connection is picked.
    await user.type(await screen.findByLabelText('Table'), 'orders');

    await user.click(screen.getByRole('button', { name: /Create & add checks/ }));

    await waitFor(() => expect(mockCreateSuite).toHaveBeenCalledTimes(1));
    expect(mockCreateSuite.mock.calls[0][0]).toMatchObject({
      name: 'orders-suite',
      connection_id: 'conn1',
      target: { table: 'orders' },
    });
    // Continues to Add Check (never leaves the new suite empty).
    expect(await screen.findByText('Add check page')).toBeInTheDocument();
  });

  it('only offers datasource connections (never orchestration providers)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([datasource, orchestration]);

    renderPage();

    await user.click(await screen.findByLabelText('Connection'));
    expect(await screen.findByText(/sf-dev · Snowflake · DEV/)).toBeInTheDocument();
    // ADF is orchestration — it can't back a suite, so it's absent from the picker.
    expect(screen.queryByText(/adf-prod/)).not.toBeInTheDocument();
  });

  it('blocks creation when no datasource connection exists', async () => {
    mockListConnections.mockResolvedValue([orchestration]);

    renderPage();

    expect(await screen.findByText('No datasource connections yet')).toBeInTheDocument();
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
  });
});
