import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type Incident,
  type IncidentDetail,
  acknowledgeIncident,
  getIncident,
  listIncidents,
  resolveIncident,
} from '../../src/api/incidents';
import { IncidentsPanel } from '../../src/components/assets/IncidentsPanel';

vi.mock('../../src/api/incidents', () => ({
  listIncidents: vi.fn(),
  acknowledgeIncident: vi.fn(),
  resolveIncident: vi.fn(),
  getIncident: vi.fn(),
  getIncidentNarrative: vi.fn().mockResolvedValue({
    narrative: null,
    invocation_id: null,
    generated_at: null,
    withheld_reason: null,
  }),
}));
const mockList = vi.mocked(listIncidents);
const mockAck = vi.mocked(acknowledgeIncident);
const mockResolve = vi.mocked(resolveIncident);
const mockGetIncident = vi.mocked(getIncident);

function incident(over: Partial<Incident> = {}): Incident {
  return {
    id: 'inc-1',
    asset_id: 'a1',
    check_id: 'c1',
    suite_id: 's1',
    status: 'open',
    resolved_by: null,
    occurrence_count: 3,
    created_at: '2026-07-01T08:00:00Z',
    last_seen_at: '2026-07-01T09:00:00Z',
    acknowledged_at: null,
    resolved_at: null,
    check_name: 'orders not null',
    asset_namespace: 'snowflake://acct',
    asset_name: 'ORDERS',
    latest_status: 'fail',
    ...over,
  };
}

function detail(over: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    ...incident(over),
    acknowledged_by: null,
    resolved_by_user_id: null,
    prior_incident_id: null,
    acknowledge_note: null,
    resolution_note: null,
    evidence: null,
    ...over,
  };
}

afterEach(() => vi.clearAllMocks());

function renderPanel(permissionBySuite: Record<string, string>, restrictedSuiteCount = 0) {
  return render(
    <AntApp>
      <IncidentsPanel
        assetId="a1"
        permissionBySuite={permissionBySuite}
        restrictedSuiteCount={restrictedSuiteCount}
      />
    </AntApp>,
  );
}

describe('IncidentsPanel', () => {
  it('renders active incidents with state, severity and occurrence count', async () => {
    mockList.mockResolvedValue([incident()]);
    renderPanel({ s1: 'owner' });
    expect(await screen.findByText('orders not null')).toBeInTheDocument();
    expect(screen.getByText('open')).toBeInTheDocument();
    expect(screen.getByText('fail')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('filters out resolved incidents (only shows open/acknowledged)', async () => {
    mockList.mockResolvedValue([
      incident({ id: 'open-1', check_name: 'live check' }),
      incident({ id: 'res-1', status: 'resolved', check_name: 'closed check' }),
    ]);
    renderPanel({ s1: 'owner' });
    expect(await screen.findByText('live check')).toBeInTheDocument();
    expect(screen.queryByText('closed check')).not.toBeInTheDocument();
  });

  it('shows the empty state when there are no open incidents', async () => {
    mockList.mockResolvedValue([]);
    renderPanel({ s1: 'owner' });
    expect(await screen.findByText('No open incidents.')).toBeInTheDocument();
  });

  it('gates ack/resolve behind edit — a view-share sees "View only"', async () => {
    mockList.mockResolvedValue([incident()]);
    renderPanel({ s1: 'view' });
    expect(await screen.findByText('View only')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resolve' })).not.toBeInTheDocument();
  });

  it('lets an editor acknowledge an open incident and reloads', async () => {
    mockList.mockResolvedValue([incident()]);
    mockAck.mockResolvedValue(detail({ status: 'acknowledged' }));
    renderPanel({ s1: 'edit' });
    await userEvent.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    await waitFor(() => expect(mockAck).toHaveBeenCalledWith('inc-1'));
    // Reloads the list after the mutation.
    expect(mockList.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('hides Acknowledge on an already-acknowledged incident but keeps Resolve', async () => {
    mockList.mockResolvedValue([incident({ status: 'acknowledged' })]);
    renderPanel({ s1: 'owner' });
    await screen.findByText('acknowledged');
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resolve' })).toBeInTheDocument();
  });

  it('resolves an incident through the confirm popover', async () => {
    mockList.mockResolvedValue([incident()]);
    mockResolve.mockResolvedValue(detail({ status: 'resolved' }));
    renderPanel({ s1: 'owner' });
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }));
    // Popconfirm — WAIT for the confirming "Resolve" to render before clicking: findAll resolves on
    // the first match (the trigger), and clicking the trigger again toggles the popover closed.
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Resolve' }).length).toBeGreaterThan(1),
    );
    const confirms = screen.getAllByRole('button', { name: 'Resolve' });
    await userEvent.click(confirms[confirms.length - 1]);
    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith('inc-1'));
  }, 15000);

  it('surfaces a failed acknowledge, resets busy, and does not reload', async () => {
    mockList.mockResolvedValue([incident()]);
    mockAck.mockRejectedValue(new Error('forbidden'));
    renderPanel({ s1: 'edit' });
    await userEvent.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    await waitFor(() => expect(mockAck).toHaveBeenCalled());
    // The error surfaces to the user…
    expect(await screen.findByText(/Action failed: forbidden/)).toBeInTheDocument();
    // …the list is NOT reloaded (initial fetch only)…
    expect(mockList).toHaveBeenCalledTimes(1);
    // …and the action button is usable again (busy state reset in finally).
    const ackButton = await screen.findByRole('button', { name: /Acknowledge/ });
    expect(ackButton).toBeEnabled();
    expect(ackButton).not.toHaveClass('ant-btn-loading');
  }, 15000);

  it('surfaces a failed resolve without reloading', async () => {
    mockList.mockResolvedValue([incident()]);
    mockResolve.mockRejectedValue(new Error('nope'));
    renderPanel({ s1: 'owner' });
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }));
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Resolve' }).length).toBeGreaterThan(1),
    );
    const confirms = screen.getAllByRole('button', { name: 'Resolve' });
    await userEvent.click(confirms[confirms.length - 1]);
    expect(await screen.findByText(/Action failed: nope/)).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledTimes(1);
  }, 15000);

  it('qualifies the list when hidden suites compose the asset (ADR 0037)', async () => {
    mockList.mockResolvedValue([]);
    renderPanel({}, 2);
    expect(
      await screen.findByText('Incidents from 2 suites outside your access are not shown here.'),
    ).toBeInTheDocument();
  });

  it('shows no qualifier when every composing suite is visible', async () => {
    mockList.mockResolvedValue([]);
    renderPanel({ s1: 'owner' });
    expect(await screen.findByText('No open incidents.')).toBeInTheDocument();
    expect(screen.queryByText(/outside your access/)).not.toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    renderPanel({ s1: 'owner' });
    expect(await screen.findByText('Failed to load incidents')).toBeInTheDocument();
  });

  // ── evidence drawer (#1634) ─────────────────────────────────────────

  it('opens the evidence drawer for an incident, available to a view-only share too', async () => {
    mockList.mockResolvedValue([incident()]);
    mockGetIncident.mockResolvedValue(
      detail({
        evidence: {
          generated_at: '2026-08-20T10:00:00Z',
          check: {
            id: 'c1',
            name: 'orders not null',
            expectation_type: 'expect_column_values_to_not_be_null',
            kind: 'expectation',
          },
          asset: null,
          failing_result: null,
          metric_trend: null,
          sibling_checks: null,
          upstream_pipeline_run: null,
          downstream_blast_radius: null,
          profile_diff: null,
        },
      }),
    );
    // View-only share (no edit) — reading evidence must not need the acting permission.
    renderPanel({ s1: 'view' });

    await userEvent.click(await screen.findByRole('button', { name: 'View' }));
    expect(await screen.findByText('Incident evidence')).toBeInTheDocument();
    expect(mockGetIncident).toHaveBeenCalledWith('inc-1');
    // The Check & asset section resolves — proves the drawer is wired to the real id.
    await screen.findByText('expect_column_values_to_not_be_null');
  });
});
