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
  stale: false,
};

const FAILING = {
  connection_id: 'c2',
  name: 'prod-uc',
  type: 'unity_catalog',
  tier: null,
  degraded_reason: null,
  last_error: 'the datasource could not be reached',
  last_refreshed_at: '2026-07-17T10:00:00Z',
  stale: false,
};

// #1091: the prod shape verbatim — refreshed 9 days ago, zero errors, zero degraded reasons.
const STALE = {
  connection_id: 'c3',
  name: 'prod-uc-stale',
  type: 'unity_catalog',
  tier: 'uc_system_access',
  degraded_reason: null,
  last_error: null,
  last_refreshed_at: '2026-07-18T19:16:00Z',
  stale: true,
};

describe('LineageGraph warehouse-lineage status (#858, #915, #916)', () => {
  it('shows nothing when no warehouse source is degraded or failing', () => {
    // The healthy case is an EMPTY list: the API omits healthy full-tier sources entirely ("no
    // banner over a clean, current graph" — `asset_view_service.warehouse_lineage_status`).
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
    // #915: this used to render at INFO weight alongside tier qualifiers, so a real operational
    // failure read as a footnote.
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
    // The two fields are not mutually exclusive: the success path records `degraded_reason` and
    // `_record_refresh_error` never clears it.
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
    // Deliberately workspace-wide: a tier is a property of the SOURCE, not of this asset, so a
    // pure-UC asset page legitimately lists Snowflake connections.
    renderGraph({ warehouseStatus: [DEGRADED] });
    expect(screen.getByText(/Workspace lineage sources/)).toBeTruthy();
    expect(
      screen.getByText(/workspace-wide source qualifiers, not findings about this asset/),
    ).toBeTruthy();
  });
});

describe('LineageGraph staleness surface (#1091)', () => {
  it('surfaces a silently-stopped source as stale, naming the last refresh', () => {
    render(
      <LineageGraph
        center={center}
        upstream={[]}
        downstream={[]}
        edges={[]}
        onOpenAsset={() => {}}
        warehouseStatus={[STALE]}
      />,
    );
    const note = screen.getByText(/Workspace lineage sources/).closest('.ant-alert');
    expect(note).toHaveTextContent('prod-uc-stale');
    expect(note).toHaveTextContent(/no refresh since/);
    expect(note).toHaveTextContent(/stale/);
    // The misnaming this guards against: a stale source is NOT "degraded".
    expect(note).not.toHaveTextContent('lineage is degraded');
  });

  it('a source that is both coarse and stale reports both qualifiers', () => {
    render(
      <LineageGraph
        center={center}
        upstream={[]}
        downstream={[]}
        edges={[]}
        onOpenAsset={() => {}}
        warehouseStatus={[{ ...STALE, degraded_reason: 'view-level lineage only' }]}
      />,
    );
    const note = screen.getByText(/Workspace lineage sources/).closest('.ant-alert');
    expect(note).toHaveTextContent(/no refresh since/);
    expect(note).toHaveTextContent('view-level lineage only');
  });

  it('a source that is both FAILING and stale keeps the staleness note (#987 shape)', () => {
    // Error + stale co-occur only when the refresh loop died after a failing attempt (the error
    // path bumps the stamp per attempt) — prod's dead-PAT connections during #1091.
    render(
      <LineageGraph
        center={center}
        upstream={[]}
        downstream={[]}
        edges={[]}
        onOpenAsset={() => {}}
        warehouseStatus={[{ ...FAILING, stale: true }]}
      />,
    );
    const alert = screen.getByText(/refresh is failing/).closest('.ant-alert');
    expect(alert).toHaveClass('ant-alert-warning');
    expect(alert).toHaveTextContent('the datasource could not be reached');
    expect(alert).toHaveTextContent(/No refresh attempt since/);
    // And the qualifier must NOT leak into the info alert: the row is failing,
    // not merely coarse.
    expect(screen.queryByText(/Workspace lineage sources/)).toBeNull();
  });
});

// #1236: a suspended prune is the OPPOSITE failure from staleness — the graph accretes, so an
// edge shown may be a dependency that no longer exists. Worded as extra edges, never missing ones.
const SUSPENDED = {
  connection_id: 'c4',
  name: 'prod-snowflake-suspended',
  type: 'snowflake',
  tier: 'snowflake_object_dependencies',
  degraded_reason: null,
  last_error: null,
  last_refreshed_at: '2026-07-18T19:16:00Z',
  stale: false,
  prune_suspended: true,
  prune_suspended_since: '2026-07-01T00:00:00Z',
};

describe('LineageGraph prune-suspension surface (#1236)', () => {
  it('reports a suspended prune with its age, as accretion rather than staleness', () => {
    renderGraph({ warehouseStatus: [SUSPENDED] });
    const note = screen.getByText(/Workspace lineage sources/).closest('.ant-alert');
    expect(note).toHaveTextContent('prod-snowflake-suspended');
    expect(note).toHaveTextContent(/pruning is suspended/);
    expect(note).toHaveTextContent(/has not removed stale edges since/);
    expect(note).toHaveTextContent(/may include dependencies that no longer exist/);
    // The misnaming this guards against — a suspended prune is neither of these.
    expect(note).not.toHaveTextContent('lineage is degraded');
    expect(note).not.toHaveTextContent(/no refresh since/);
  });

  it('says NEVER, not a date, when the source has no recorded prune', () => {
    // null means no prune has ever happened. Rendering it as "since Invalid Date" (or omitting
    // the clause) would turn "we have never once cleaned this graph" into a detail.
    renderGraph({ warehouseStatus: [{ ...SUSPENDED, prune_suspended_since: null }] });
    const note = screen.getByText(/Workspace lineage sources/).closest('.ant-alert');
    expect(note).toHaveTextContent(/has never removed stale edges/);
    expect(note).not.toHaveTextContent(/Invalid Date/);
  });

  it('says nothing when an API predating the field omits it', () => {
    // `undefined` is "not reported", which must not render as either a suspension or an
    // all-clear — the two-step-deploy window where the banner runs against the old API.
    renderGraph({
      warehouseStatus: [
        { ...DEGRADED, prune_suspended: undefined, prune_suspended_since: undefined },
      ],
    });
    expect(screen.queryByText(/pruning is suspended/)).toBeNull();
    expect(screen.getByText(/view-level lineage only/)).toBeTruthy();
  });

  it('keeps the suspension note on a FAILING source (#987 shape)', () => {
    // A failing source is also a non-pruning one; suppressing either note behind the other is
    // exactly the one-field-hides-another bug.
    renderGraph({
      warehouseStatus: [{ ...FAILING, prune_suspended: true, prune_suspended_since: null }],
    });
    const alert = screen.getByText(/refresh is failing/).closest('.ant-alert');
    expect(alert).toHaveClass('ant-alert-warning');
    expect(alert).toHaveTextContent('the datasource could not be reached');
    expect(alert).toHaveTextContent(/pruning is suspended/);
    expect(alert).toHaveTextContent(/has never removed stale edges/);
  });

  it('reports a coarse AND suspended source as two qualifiers, not one', () => {
    renderGraph({
      warehouseStatus: [{ ...SUSPENDED, degraded_reason: 'view-level lineage only' }],
    });
    const note = screen.getByText(/Workspace lineage sources/).closest('.ant-alert');
    expect(note).toHaveTextContent('view-level lineage only');
    expect(note).toHaveTextContent(/pruning is suspended/);
  });
});
