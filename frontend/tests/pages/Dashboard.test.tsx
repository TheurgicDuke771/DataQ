import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type DashboardSummary, getDashboardSummary } from '../../src/api/dashboard';
import { Dashboard } from '../../src/pages/Dashboard';

vi.mock('../../src/api/dashboard', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/dashboard')>();
  return { ...actual, getDashboardSummary: vi.fn() };
});

const mockGet = vi.mocked(getDashboardSummary);

const summary: DashboardSummary = {
  window_days: 7,
  kpis: { health_score: 81.2, pass_rate: 50, total_runs: 12, active_connections: 3 },
  trend: [],
  suite_performance: [],
};

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

describe('Dashboard', () => {
  it('renders the backed KPIs from the summary', async () => {
    mockGet.mockResolvedValue(summary);
    renderPage();

    expect(await screen.findByText('81.2')).toBeInTheDocument();
    expect(screen.getByText('Data Integrity Score')).toBeInTheDocument();
    expect(screen.getByText('50')).toBeInTheDocument(); // pass rate
    expect(screen.getByText('12')).toBeInTheDocument(); // total runs
    expect(screen.getByText('3')).toBeInTheDocument(); // active connections
    // Default range is 7d.
    expect(mockGet).toHaveBeenCalledWith(7);
  });

  it('does not render the unbacked prototype KPIs (KPI honesty)', async () => {
    mockGet.mockResolvedValue(summary);
    renderPage();
    await screen.findByText('81.2');

    expect(screen.queryByText(/anomalies/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/time to resolution/i)).not.toBeInTheDocument();
  });

  it('renders em dashes for null KPIs on an empty workspace', async () => {
    mockGet.mockResolvedValue({
      window_days: 7,
      kpis: { health_score: null, pass_rate: null, total_runs: 0, active_connections: 0 },
      trend: [],
      suite_performance: [],
    });
    renderPage();

    // health_score + pass_rate render as em dashes; total_runs/connections as 0.
    await waitFor(() => expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2));
  });

  it('refetches with the new window when the range changes', async () => {
    mockGet.mockResolvedValue(summary);
    renderPage();
    await screen.findByText('81.2');

    await userEvent.click(screen.getByText('30d'));
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(30));
  });

  it('shows an error alert when the summary fails to load', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText('Failed to load dashboard')).toBeInTheDocument();
  });
});
