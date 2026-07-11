import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type Incident,
  type IncidentDetail,
  acknowledgeIncident,
  listIncidents,
  resolveIncident,
} from '../../src/api/incidents';
import { IncidentsPanel } from '../../src/components/assets/IncidentsPanel';

vi.mock('../../src/api/incidents', () => ({
  listIncidents: vi.fn(),
  acknowledgeIncident: vi.fn(),
  resolveIncident: vi.fn(),
}));
const mockList = vi.mocked(listIncidents);
const mockAck = vi.mocked(acknowledgeIncident);
const mockResolve = vi.mocked(resolveIncident);

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
    evidence: {},
    ...over,
  };
}

afterEach(() => vi.clearAllMocks());

function renderPanel(permissionBySuite: Record<string, string>) {
  return render(
    <AntApp>
      <IncidentsPanel assetId="a1" permissionBySuite={permissionBySuite} />
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
    // Popconfirm — click the confirming "Resolve".
    const confirms = await screen.findAllByRole('button', { name: 'Resolve' });
    await userEvent.click(confirms[confirms.length - 1]);
    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith('inc-1'));
  });

  it('surfaces a failed acknowledge, resets busy, and does not reload', async () => {
    mockList.mockResolvedValue([incident()]);
    mockAck.mockRejectedValue(new Error('forbidden'));
    renderPanel({ s1: 'edit' });
    await userEvent.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    // The error surfaces to the user…
    expect(await screen.findByText(/Action failed: forbidden/)).toBeInTheDocument();
    // …the list is NOT reloaded (initial fetch only)…
    expect(mockList).toHaveBeenCalledTimes(1);
    // …and the action button is usable again (busy state reset in finally).
    // Name regex + button-level class: jsdom never fires the leave-motion events,
    // so the spinner SPAN lingers mid-animation — but the button's own
    // `ant-btn-loading` state class drops the moment `loading` goes false.
    const ackButton = await screen.findByRole('button', { name: /Acknowledge/ });
    expect(ackButton).toBeEnabled();
    expect(ackButton).not.toHaveClass('ant-btn-loading');
  });

  it('surfaces a failed resolve without reloading', async () => {
    mockList.mockResolvedValue([incident()]);
    mockResolve.mockRejectedValue(new Error('nope'));
    renderPanel({ s1: 'owner' });
    await userEvent.click(await screen.findByRole('button', { name: 'Resolve' }));
    const confirms = await screen.findAllByRole('button', { name: 'Resolve' });
    await userEvent.click(confirms[confirms.length - 1]);
    expect(await screen.findByText(/Action failed: nope/)).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledTimes(1);
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    renderPanel({ s1: 'owner' });
    expect(await screen.findByText('Failed to load incidents')).toBeInTheDocument();
  });
});
