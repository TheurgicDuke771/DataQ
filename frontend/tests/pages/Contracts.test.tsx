import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { listConnections } from '../../src/api/connections';
import {
  type Contract,
  type ContractDetail as ContractDetailData,
  exportContractYaml,
  getContract,
  importContract,
  listContracts,
} from '../../src/api/contracts';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { ContractDetail } from '../../src/pages/ContractDetail';
import { Contracts } from '../../src/pages/Contracts';
import { downloadText } from '../../src/utils/download';

vi.mock('../../src/api/contracts', () => ({
  listContracts: vi.fn(),
  getContract: vi.fn(),
  importContract: vi.fn(),
  exportContractYaml: vi.fn(),
}));
vi.mock('../../src/api/connections', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/connections')>()),
  listConnections: vi.fn(),
}));
vi.mock('../../src/utils/download', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/utils/download')>()),
  downloadText: vi.fn(),
}));

const CONTRACT: Contract = {
  id: 'k1',
  odcs_id: 'orders-contract',
  name: 'orders',
  version: '1.0.0',
  status: 'active',
  domain: 'sales',
  tenant: null,
  api_version: 'v3.2.0',
  created_at: '2026-10-25T09:00:00Z',
  latest_version: true,
  suites: [{ id: 's1', name: 'orders · orders', contract_object: 'orders', runnable: true }],
  restricted_suite_count: 1,
  unmapped: [
    {
      object: null,
      property: null,
      rule_id: 'slaProperties[0]',
      field: 'slaProperties.retention',
      reason: 'not a data-quality check',
      clause: {},
    },
  ],
};

const DETAIL: ContractDetailData = {
  ...CONTRACT,
  compliance: {
    clauses: [
      {
        suite_id: 's1',
        check_id: 'c1',
        check_name: 'orders row floor',
        clause: { object: 'orders', property: null, rule_id: 'floor', field: 'quality' },
        state: 'fail',
        drifted: true,
      },
      {
        suite_id: 's1',
        check_id: null,
        check_name: null,
        clause: { object: 'orders', property: 'order_id', rule_id: null, field: 'required' },
        state: 'missing',
        drifted: null,
      },
    ],
    suites: [
      {
        suite_id: 's1',
        last_run_id: 'r1',
        last_run_status: 'running',
        last_run_at: '2026-10-25T10:00:00Z',
        results_final: false,
      },
    ],
    counts: {
      pass: 0,
      warn: 0,
      fail: 1,
      critical: 0,
      unverified: 0,
      not_run: 0,
      disabled: 0,
      missing: 1,
    },
    not_enforced: 1,
  },
};

function me(role: 'member' | 'viewer'): AsyncState<MeResponse> {
  return {
    status: 'ok',
    data: {
      id: 'u-1',
      aad_object_id: 'oid-1',
      email: 'user@dataq.io',
      display_name: 'User',
      last_seen_at: null,
      role,
      is_workspace_admin: false,
    },
  };
}

function renderAt(path: string, role: 'member' | 'viewer' = 'member') {
  return render(
    <MeContext.Provider value={me(role)}>
      <AntApp>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/contracts" element={<Contracts />} />
            <Route path="/contracts/:contractId" element={<ContractDetail />} />
            <Route path="/suites/:suiteId" element={<div>suite page</div>} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </MeContext.Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Contracts list', () => {
  it('lists each version with its latest flag and what is hidden or not enforced', async () => {
    vi.mocked(listContracts).mockResolvedValue([CONTRACT]);
    renderAt('/contracts');
    const table = await screen.findByRole('table', { name: 'Data contracts' });
    expect(within(table).getByRole('link', { name: 'orders' })).toHaveAttribute(
      'href',
      '/contracts/k1',
    );
    expect(within(table).getByText('Latest')).toBeInTheDocument();
    expect(within(table).getByText('1 (+1 you cannot see)')).toBeInTheDocument();
    expect(within(table).getByText('1 clauses')).toBeInTheDocument();
  });

  it('offers import to a member and not to a viewer', async () => {
    vi.mocked(listContracts).mockResolvedValue([]);
    renderAt('/contracts', 'viewer');
    expect(await screen.findByText('No contracts yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Import contract/ })).not.toBeInTheDocument();
  });

  it('imports a pasted document onto the chosen connection', async () => {
    const user = userEvent.setup();
    vi.mocked(listContracts).mockResolvedValue([]);
    vi.mocked(listConnections).mockResolvedValue([
      { id: 'conn1', name: 'warehouse', type: 'postgres', env: 'dev' } as never,
      { id: 'adf1', name: 'pipelines', type: 'adf', env: 'dev' } as never,
    ]);
    vi.mocked(importContract).mockResolvedValue({
      contract: CONTRACT,
      created_suite_ids: ['s1'],
      reconciled_suite_ids: [],
      retired_suite_ids: [],
      untargeted_objects: [],
      checks_added: 3,
      checks_updated: 0,
      checks_disabled: 0,
      warnings: [],
    });
    vi.mocked(getContract).mockResolvedValue(DETAIL);
    renderAt('/contracts');

    await user.click((await screen.findAllByRole('button', { name: /Import contract/ }))[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Import a data contract' });
    await user.click(
      within(dialog).getByRole('combobox', { name: 'Connection the data lives on' }),
    );
    // Orchestration providers are never a suite's datasource.
    expect(screen.queryByTitle('pipelines (adf, dev)')).not.toBeInTheDocument();
    await user.click(await screen.findByTitle('warehouse (postgres, dev)'));
    await user.type(
      within(dialog).getByRole('textbox', { name: 'ODCS document (YAML)' }),
      'kind: DataContract',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Import' }));

    await waitFor(() =>
      expect(importContract).toHaveBeenCalledWith({
        connection_id: 'conn1',
        document_yaml: 'kind: DataContract',
      }),
    );
    expect(await screen.findByRole('heading', { name: /orders/ })).toBeInTheDocument();
  });

  it('shows the server refusal in the dialog', async () => {
    const user = userEvent.setup();
    vi.mocked(listContracts).mockResolvedValue([]);
    vi.mocked(listConnections).mockResolvedValue([
      { id: 'conn1', name: 'warehouse', type: 'postgres', env: 'dev' } as never,
    ]);
    vi.mocked(importContract).mockRejectedValue(new Error('apiVersion v4.0.0 is not supported'));
    renderAt('/contracts');
    await user.click((await screen.findAllByRole('button', { name: /Import contract/ }))[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Import a data contract' });
    await user.click(
      within(dialog).getByRole('combobox', { name: 'Connection the data lives on' }),
    );
    await user.click(await screen.findByTitle('warehouse (postgres, dev)'));
    await user.type(within(dialog).getByRole('textbox', { name: 'ODCS document (YAML)' }), 'x');
    await user.click(within(dialog).getByRole('button', { name: 'Import' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('v4.0.0');
  });
});

describe('Contract detail', () => {
  it('shows each clause state, the edited flag, and a missing check', async () => {
    vi.mocked(getContract).mockResolvedValue(DETAIL);
    renderAt('/contracts/k1');
    const clauses = await screen.findByRole('table', { name: 'Contract clauses and their state' });
    expect(within(clauses).getByLabelText(/^Fail:/)).toBeInTheDocument();
    expect(within(clauses).getByLabelText(/^Edited since import/)).toBeInTheDocument();
    expect(within(clauses).getByLabelText(/^No check:/)).toBeInTheDocument();
    const unmapped = screen.getByRole('table', { name: 'Clauses DataQ does not enforce' });
    expect(within(unmapped).getByText('not a data-quality check')).toBeInTheDocument();
  });

  it('warns that a running suite is not a verdict and that some suites are hidden', async () => {
    vi.mocked(getContract).mockResolvedValue(DETAIL);
    renderAt('/contracts/k1');
    expect(await screen.findByText('Not a final verdict yet')).toBeInTheDocument();
    expect(screen.getByText('1 linked suites are not shown')).toBeInTheDocument();
    expect(screen.getByText(/2 clauses are not being checked at all/)).toBeInTheDocument();
    expect(document.title).toBe('orders 1.0.0 · Contract · DataQ');
  });

  it('downloads the ODCS document', async () => {
    const user = userEvent.setup();
    vi.mocked(getContract).mockResolvedValue(DETAIL);
    vi.mocked(exportContractYaml).mockResolvedValue('apiVersion: v3.2.0\n');
    renderAt('/contracts/k1');
    await user.click(await screen.findByRole('button', { name: /Download ODCS/ }));
    await waitFor(() =>
      expect(downloadText).toHaveBeenCalledWith(
        'orders_1_0_0.odcs.yaml',
        'apiVersion: v3.2.0\n',
        'application/yaml',
      ),
    );
  });
});

describe('Contract import — versions and results', () => {
  const RESULT = {
    contract: CONTRACT,
    created_suite_ids: [],
    reconciled_suite_ids: ['s1'],
    retired_suite_ids: [],
    untargeted_objects: ['refunds'],
    checks_added: 1,
    checks_updated: 2,
    checks_disabled: 1,
    warnings: ['apiVersion v3.9.0 validated against v3.2.0'],
  };

  async function openAndFill(user: ReturnType<typeof userEvent.setup>) {
    await user.click((await screen.findAllByRole('button', { name: /Import contract/ }))[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Import a data contract' });
    await user.click(
      within(dialog).getByRole('combobox', { name: 'Connection the data lives on' }),
    );
    await user.click(await screen.findByTitle('warehouse (postgres, dev)'));
    await user.type(within(dialog).getByRole('textbox', { name: 'ODCS document (YAML)' }), 'x');
    return dialog;
  }

  beforeEach(() => {
    vi.mocked(listContracts).mockResolvedValue([CONTRACT]);
    vi.mocked(listConnections).mockResolvedValue([
      { id: 'conn1', name: 'warehouse', type: 'postgres', env: 'dev' } as never,
    ]);
    vi.mocked(importContract).mockResolvedValue(RESULT);
    vi.mocked(getContract).mockResolvedValue(DETAIL);
  });

  it("a new version takes over the previous version's suites", async () => {
    const user = userEvent.setup();
    renderAt('/contracts');
    const dialog = await openAndFill(user);
    await user.click(within(dialog).getByRole('combobox', { name: 'New version of' }));
    await user.click(await screen.findByTitle('orders 1.0.0'));
    await user.click(within(dialog).getByRole('button', { name: 'Import' }));
    await waitFor(() =>
      expect(importContract).toHaveBeenCalledWith({
        connection_id: 'conn1',
        document_yaml: 'x',
        suite_ids: ['s1'],
      }),
    );
  });

  it('can replace an already imported version', async () => {
    const user = userEvent.setup();
    renderAt('/contracts');
    const dialog = await openAndFill(user);
    await user.click(
      within(dialog).getByRole('checkbox', {
        name: 'Replace this version if it is already imported',
      }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Import' }));
    await waitFor(() =>
      expect(importContract).toHaveBeenCalledWith(expect.objectContaining({ replace: true })),
    );
  });

  it('surfaces warnings and unresolved objects instead of a plain success', async () => {
    const user = userEvent.setup();
    renderAt('/contracts');
    const dialog = await openAndFill(user);
    await user.click(within(dialog).getByRole('button', { name: 'Import' }));
    expect(
      (await screen.findAllByText(/1 suites updated, 1 checks added, 2 updated, 1 switched off/))
        .length,
    ).toBeGreaterThan(0);
    expect(screen.getByText('apiVersion v3.9.0 validated against v3.2.0')).toBeInTheDocument();
    expect(screen.getByText(/No table matched refunds/)).toBeInTheDocument();
  });

  it('names the file input', async () => {
    const user = userEvent.setup();
    renderAt('/contracts');
    await user.click((await screen.findAllByRole('button', { name: /Import contract/ }))[0]);
    expect(await screen.findByLabelText('ODCS file')).toHaveAttribute('type', 'file');
  });
});

describe('Contract detail — navigation', () => {
  it('loads the new contract when the id changes in place', async () => {
    const other = { ...DETAIL, id: 'k2', name: 'refunds' };
    vi.mocked(getContract).mockImplementation((id: string) =>
      Promise.resolve(id === 'k2' ? other : DETAIL),
    );
    function Nav() {
      const navigate = useNavigate();
      return <button onClick={() => navigate('/contracts/k2')}>go</button>;
    }
    const user = userEvent.setup();
    render(
      <MeContext.Provider value={me('member')}>
        <AntApp>
          <MemoryRouter initialEntries={['/contracts/k1']}>
            <Nav />
            <Routes>
              <Route path="/contracts/:contractId" element={<ContractDetail />} />
            </Routes>
          </MemoryRouter>
        </AntApp>
      </MeContext.Provider>,
    );
    expect(await screen.findByRole('heading', { name: /orders/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'go' }));
    expect(await screen.findByRole('heading', { name: /refunds/ })).toBeInTheDocument();
  });
});
