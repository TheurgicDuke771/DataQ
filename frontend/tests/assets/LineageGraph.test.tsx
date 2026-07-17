import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { CenterAsset } from '../../src/components/assets/lineageLayout';
import { LineageGraph } from '../../src/components/assets/LineageGraph';

const center: CenterAsset = {
  id: 'a1',
  name: 'DB.S.ORDERS',
  namespace: 'snowflake://ACCT',
  env: 'dev',
};

function renderGraph(props: Partial<Parameters<typeof LineageGraph>[0]> = {}) {
  return render(
    <LineageGraph
      center={center}
      upstream={[]}
      downstream={[]}
      edges={[]}
      onOpenAsset={() => {}}
      {...props}
    />,
  );
}

describe('LineageGraph warehouse-lineage status (#858)', () => {
  it('shows nothing when no warehouse source is degraded', () => {
    renderGraph();
    expect(screen.queryByText(/Warehouse lineage may be coarse/)).toBeNull();
  });

  it('surfaces a degraded (view-level-only) warehouse source', () => {
    renderGraph({
      warehouseStatus: [
        {
          connection_id: 'c1',
          name: 'prod-snowflake',
          type: 'snowflake',
          tier: 'snowflake_object_dependencies',
          degraded_reason: 'view-level lineage only — richer tiers need Enterprise',
          last_error: null,
          last_refreshed_at: '2026-07-17T10:00:00Z',
        },
      ],
    });
    // The graph is real but coarse — an INFO note, not the failing-source warning.
    expect(screen.getByText(/Warehouse lineage may be coarse or stale/)).toBeTruthy();
    expect(screen.getByText(/view-level lineage only/)).toBeTruthy();
  });

  it('surfaces a failing warehouse refresh with its classified error', () => {
    renderGraph({
      warehouseStatus: [
        {
          connection_id: 'c2',
          name: 'prod-uc',
          type: 'unity_catalog',
          tier: null,
          degraded_reason: null,
          last_error: 'the datasource could not be reached',
          last_refreshed_at: '2026-07-17T10:00:00Z',
        },
      ],
    });
    expect(screen.getByText(/last refresh failed/)).toBeTruthy();
    expect(screen.getByText(/the datasource could not be reached/)).toBeTruthy();
  });
});
