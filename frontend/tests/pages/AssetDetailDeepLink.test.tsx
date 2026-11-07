import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { type AssetDetail as AssetDetailData, getAsset } from '../../src/api/assets';
import { getIncident } from '../../src/api/incidents';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { AssetDetail } from '../../src/pages/AssetDetail';

vi.mock('../../src/api/assets', () => ({ getAsset: vi.fn(), updateAsset: vi.fn() }));
vi.mock('../../src/api/admin', () => ({ listAdminUsers: vi.fn().mockResolvedValue([]) }));
vi.mock('../../src/api/incidents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/incidents')>()),
  listIncidents: vi.fn().mockResolvedValue([]),
  getIncident: vi.fn().mockRejectedValue(new Error('detail not stubbed')),
}));

const GOOD = '3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b';

const DETAIL: AssetDetailData = {
  summary: {
    id: 'a1',
    namespace: 'snowflake://acct',
    name: 'ANALYTICS.PUBLIC.ORDERS',
    env: 'dev',
    description: null,
    owner_user_id: null,
    last_seen: '2026-07-01T10:00:00Z',
    suite_count: 0,
    worst_severity: null,
    checks_total: 0,
    checks_passed: 0,
    last_run_at: null,
    has_failed_run: false,
    has_active_run: false,
    has_operational_error: false,
    has_cancelled_run: false,
    has_skip: false,
  },
  restricted_suite_count: 0,
  suites: [],
  upstream: [],
  downstream: [],
  lineage_edges: [],
  failing_lineage_sources: [],
  warehouse_lineage_status: [],
} as unknown as AssetDetailData;

const ME: AsyncState<MeResponse> = {
  status: 'ok',
  data: {
    id: 'u-1',
    aad_object_id: 'oid-1',
    email: 'user@dataq.io',
    display_name: 'User',
    last_seen_at: null,
    role: 'member',
    is_workspace_admin: false,
  },
};

function renderAt(search: string) {
  return render(
    <MeContext.Provider value={ME}>
      <AntApp>
        <MemoryRouter initialEntries={[`/assets/a1${search}`]}>
          <Routes>
            <Route path="/assets/:assetId" element={<AssetDetail />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </MeContext.Provider>,
  );
}

describe('AssetDetail ?incident= deep link', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAsset).mockResolvedValue(DETAIL);
  });

  it('opens the incident for a well-formed id', async () => {
    renderAt(`?incident=${GOOD}`);
    await waitFor(() => expect(vi.mocked(getIncident)).toHaveBeenCalledWith(GOOD));
  });

  it.each(['../../admin/users', 'abc', '1/ack?x=1#y', ''])(
    'issues no incident request for malformed %j',
    async (bad) => {
      renderAt(`?incident=${encodeURIComponent(bad)}`);
      await screen.findByRole('heading', { name: 'ANALYTICS.PUBLIC.ORDERS' });
      expect(vi.mocked(getIncident)).not.toHaveBeenCalled();
    },
  );
});
