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

const DEGRADED = {
  connection_id: 'c1',
  name: 'prod-snowflake',
  type: 'snowflake',
  tier: 'snowflake_object_dependencies',
  degraded_reason: 'view-level lineage only — richer tiers need Enterprise',
  last_error: null,
  last_refreshed_at: '2026-07-17T10:00:00Z',
};

const FAILING = {
  connection_id: 'c2',
  name: 'prod-uc',
  type: 'unity_catalog',
  tier: null,
  degraded_reason: null,
  last_error: 'the datasource could not be reached',
  last_refreshed_at: '2026-07-17T10:00:00Z',
};

describe('LineageGraph warehouse-lineage status (#858, #915, #916)', () => {
  it('shows nothing when no warehouse source is degraded or failing', () => {
    // The healthy case is an EMPTY list: the API omits healthy full-tier sources
    // entirely ("no banner over a clean, current graph" —
    // `asset_view_service.warehouse_lineage_status`).
    renderGraph();
    expect(screen.queryByText(/Workspace lineage sources/)).toBeNull();
    expect(screen.queryByText(/refresh is failing/)).toBeNull();
  });

  it('surfaces a degraded (view-level-only) warehouse source as an INFO note', () => {
    renderGraph({ warehouseStatus: [DEGRADED] });
    // The graph is real but coarse — an INFO note, not the failing-source warning.
    const alert = screen.getByText(/Workspace lineage sources/).closest('.ant-alert');
    expect(alert).toHaveClass('ant-alert-info');
    expect(screen.getByText(/view-level lineage only/)).toBeTruthy();
  });

  it('surfaces a failing warehouse refresh as a WARNING, with its classified error', () => {
    renderGraph({ warehouseStatus: [FAILING] });
    // #915: this used to render at INFO weight alongside tier qualifiers, so a real
    // operational failure read as a footnote. Severity is carried by the antd alert
    // class, so assert on that rather than on wording, which drifts.
    const alert = screen.getByText(/refresh is failing/).closest('.ant-alert');
    expect(alert).toHaveClass('ant-alert-warning');
    expect(screen.getByText(/last refresh failed/)).toBeTruthy();
    expect(screen.getByText(/the datasource could not be reached/)).toBeTruthy();
  });

  it('keeps a failing source visually distinct from a merely degraded one', () => {
    // The whole point of the #915 split: when both exist they must not collapse
    // into one box at one severity.
    renderGraph({ warehouseStatus: [DEGRADED, FAILING] });
    const failing = screen.getByText(/refresh is failing/).closest('.ant-alert');
    const degraded = screen.getByText(/Workspace lineage sources/).closest('.ant-alert');
    expect(failing).toHaveClass('ant-alert-warning');
    expect(degraded).toHaveClass('ant-alert-info');
    expect(failing).not.toBe(degraded);
    // Each source is listed under its own advisory, not both under one.
    expect(failing).toHaveTextContent('prod-uc');
    expect(failing).not.toHaveTextContent('prod-snowflake');
    expect(degraded).toHaveTextContent('prod-snowflake');
    expect(degraded).not.toHaveTextContent('prod-uc');
  });

  it('shows the tier note on a source that is BOTH degraded and failing (#987)', () => {
    // The two fields are not mutually exclusive: the success path records
    // `degraded_reason` and `_record_refresh_error` never clears it, so a source
    // that answered coarsely and has since started failing carries both. The tier
    // note describes what the source CAN answer (edition/grants), which still
    // holds while refreshes fail — so it must not be swallowed by the error.
    renderGraph({
      warehouseStatus: [
        {
          ...FAILING,
          degraded_reason: 'view-level lineage only — richer tiers need Enterprise',
        },
      ],
    });

    const alert = screen.getByText(/refresh is failing/).closest('.ant-alert');
    expect(alert).toHaveClass('ant-alert-warning'); // failure still dominates
    expect(alert).toHaveTextContent('the datasource could not be reached');
    expect(alert).toHaveTextContent(/view-level lineage only/);
    // …and it is not ALSO listed as a merely-degraded source, which would read as
    // two different sources having two different problems.
    expect(screen.queryByText(/Workspace lineage sources/)).toBeNull();
  });

  it('frames the degraded advisory as workspace-level, not asset-scoped (#916)', () => {
    // Deliberately workspace-wide: a tier is a property of the SOURCE, not of this
    // asset, so a pure-UC asset page legitimately lists Snowflake connections. The
    // framing has to say so, or it reads as a bug.
    renderGraph({ warehouseStatus: [DEGRADED] });
    expect(screen.getByText(/Workspace lineage sources/)).toBeTruthy();
    expect(
      screen.getByText(/workspace-wide source qualifiers, not findings about this asset/),
    ).toBeTruthy();
  });
});
