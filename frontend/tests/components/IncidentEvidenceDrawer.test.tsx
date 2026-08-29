import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { IncidentDetail, IncidentEvidence } from '../../src/api/incidents';
import { getIncident } from '../../src/api/incidents';
import { IncidentEvidenceDrawer } from '../../src/components/assets/IncidentEvidenceDrawer';

// The drawer's asset refs use AssetLink, which navigates via react-router — every render needs a
// Router ancestor, same as any other test that renders it (RunDetail.test.tsx, IncidentsPanel).
function renderDrawer(incidentId: string | null, onClose = vi.fn()) {
  return render(
    <MemoryRouter>
      <IncidentEvidenceDrawer incidentId={incidentId} onClose={onClose} />
    </MemoryRouter>,
  );
}

vi.mock('../../src/api/incidents', () => ({
  getIncident: vi.fn(),
}));
const mockGetIncident = vi.mocked(getIncident);

function fullEvidence(): IncidentEvidence {
  return {
    generated_at: '2026-08-20T10:00:00Z',
    check: {
      id: 'chk-1',
      name: 'order_id not null',
      expectation_type: 'expect_column_values_to_not_be_null',
      kind: 'expectation',
    },
    asset: { id: 'a1', namespace: 'snowflake://acct', name: 'ORDERS', env: 'prod' },
    failing_result: {
      status: 'fail',
      metric_value: 4.2,
      observed_value: { unexpected_percent: 4.2 },
      expected_value: null,
    },
    metric_trend: [
      { status: 'fail', metric_value: 4.2, created_at: '2026-08-20T09:00:00Z', run_id: 'r1' },
      { status: 'pass', metric_value: 0, created_at: '2026-08-19T09:00:00Z', run_id: 'r0' },
    ],
    sibling_checks: [{ check_name: 'order_total positive', status: 'pass' }],
    upstream_pipeline_run: {
      provider: 'adf',
      pipeline_or_dag_id: 'pl_orders',
      provider_run_id: 'run-77',
      status: 'succeeded',
      started_at: '2026-08-20T08:00:00Z',
      finished_at: '2026-08-20T08:10:00Z',
      duration_seconds: 600,
      delay_seconds_vs_history: 120,
    },
    downstream_blast_radius: [
      { id: 'a2', namespace: 'snowflake://acct', name: 'ORDER_SUMMARY', env: 'prod' },
    ],
    profile_diff: null,
  };
}

function detail(evidence: IncidentEvidence | null): IncidentDetail {
  return {
    id: 'inc-1',
    asset_id: 'a1',
    check_id: 'chk-1',
    suite_id: 's1',
    status: 'open',
    resolved_by: null,
    occurrence_count: 2,
    created_at: '2026-08-20T09:00:00Z',
    last_seen_at: '2026-08-20T09:00:00Z',
    acknowledged_at: null,
    resolved_at: null,
    check_name: 'order_id not null',
    asset_namespace: 'snowflake://acct',
    asset_name: 'ORDERS',
    latest_status: 'fail',
    acknowledged_by: null,
    resolved_by_user_id: null,
    prior_incident_id: null,
    acknowledge_note: null,
    resolution_note: null,
    evidence,
  };
}

afterEach(() => vi.clearAllMocks());

describe('IncidentEvidenceDrawer', () => {
  it('renders nothing fetched when incidentId is null (drawer closed)', () => {
    renderDrawer(null);
    expect(mockGetIncident).not.toHaveBeenCalled();
    expect(screen.queryByText('Incident evidence')).not.toBeInTheDocument();
  });

  it('renders every populated layer of a full evidence card', async () => {
    mockGetIncident.mockResolvedValue(detail(fullEvidence()));
    renderDrawer('inc-1');

    expect(await screen.findByText('order_id not null')).toBeInTheDocument();
    expect(screen.getByText('expect_column_values_to_not_be_null')).toBeInTheDocument();
    // The asset ref (AssetRef) splits namespace/name and env across nested elements — assert by
    // substring, like the blast-radius assertion below — plus its navigable AssetLink.
    expect(screen.getAllByText(/snowflake:\/\/acct\.ORDERS/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Asset').length).toBeGreaterThan(0);
    // Failing result.
    expect(screen.getAllByText('fail').length).toBeGreaterThan(0);
    // Metric trend — both rows render.
    expect(screen.getByText('order_total positive')).toBeInTheDocument();
    // Upstream pipeline.
    expect(screen.getByText('pl_orders')).toBeInTheDocument();
    expect(screen.getByText('run-77')).toBeInTheDocument();
    expect(screen.getByText('+120s')).toBeInTheDocument();
    // Blast radius.
    expect(screen.getByText(/ORDER_SUMMARY/)).toBeInTheDocument();
    // profile_diff is null in the fixture — must say so explicitly, not render blank.
    expect(screen.getByText(/Not available — not implemented yet/)).toBeInTheDocument();
    expect(mockGetIncident).toHaveBeenCalledWith('inc-1');
  });

  it('renders an explicit "Not available" for every null layer (a layer failure), not blank', async () => {
    mockGetIncident.mockResolvedValue(
      detail({
        generated_at: '2026-08-20T10:00:00Z',
        check: null,
        asset: null,
        failing_result: null,
        metric_trend: null,
        sibling_checks: null,
        upstream_pipeline_run: null,
        downstream_blast_radius: null,
        profile_diff: null,
      }),
    );
    renderDrawer('inc-1');

    await screen.findByText('Captured', { exact: false });
    // One "Not available" per null layer: check&asset, failing result, metric trend, siblings,
    // upstream pipeline (with its own reason), blast radius, profile diff.
    const notAvailable = screen.getAllByText(/Not available/);
    expect(notAvailable.length).toBeGreaterThanOrEqual(7);
    expect(
      screen.getByText(/Not available — not triggered by a monitored pipeline/),
    ).toBeInTheDocument();
  });

  it('distinguishes an empty metric trend / siblings / blast radius from a null (failed) layer', async () => {
    mockGetIncident.mockResolvedValue(
      detail({
        ...fullEvidence(),
        metric_trend: [],
        sibling_checks: [],
        downstream_blast_radius: [],
      }),
    );
    renderDrawer('inc-1');

    expect(
      await screen.findByText('No prior readings recorded for this check.'),
    ).toBeInTheDocument();
    expect(screen.getByText('No other checks ran alongside this one.')).toBeInTheDocument();
    expect(screen.getByText('No downstream assets recorded.')).toBeInTheDocument();
  });

  it('shows an explicit reason when delay_seconds_vs_history has no baseline', async () => {
    const evidence = fullEvidence();
    const pipeline = evidence.upstream_pipeline_run;
    if (!pipeline) throw new Error('fixture must carry an upstream_pipeline_run');
    mockGetIncident.mockResolvedValue(
      detail({
        ...evidence,
        upstream_pipeline_run: { ...pipeline, delay_seconds_vs_history: null },
      }),
    );
    renderDrawer('inc-1');

    expect(
      await screen.findByText(/Not available — no completed prior run to compare against/),
    ).toBeInTheDocument();
  });

  it('shows an empty-card state when the incident has no evidence recorded at all', async () => {
    mockGetIncident.mockResolvedValue(detail(null));
    renderDrawer('inc-1');

    expect(
      await screen.findByText('No evidence card recorded for this incident.'),
    ).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockGetIncident.mockRejectedValue(new Error('boom'));
    renderDrawer('inc-1');

    expect(await screen.findByText('Failed to load incident evidence')).toBeInTheDocument();
  });

  it('refetches when the incident id changes while the drawer stays open', async () => {
    mockGetIncident.mockImplementation(() => Promise.resolve(detail(fullEvidence())));
    const { rerender } = renderDrawer('inc-1');
    await screen.findByText('order_id not null');
    expect(mockGetIncident).toHaveBeenCalledWith('inc-1');

    rerender(
      <MemoryRouter>
        <IncidentEvidenceDrawer incidentId="inc-2" onClose={vi.fn()} />
      </MemoryRouter>,
    );
    await within(screen.getByRole('dialog')).findByText('order_id not null');
    expect(mockGetIncident).toHaveBeenCalledWith('inc-2');
  });
});
