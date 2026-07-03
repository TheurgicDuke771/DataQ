import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type DashboardSummary, getDashboardSummary } from '../../src/api/dashboard';
import { listRuns } from '../../src/api/runs';
import { listSuites } from '../../src/api/suites';
import { Dashboard } from '../../src/pages/Dashboard';

vi.mock('../../src/api/dashboard', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/dashboard')>();
  return { ...actual, getDashboardSummary: vi.fn() };
});

// The Recent Runs widget fetches its own slice; stub it out here so these tests
// stay focused on the KPI row / range behaviour.
vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, listRuns: vi.fn() };
});
vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listSuites: vi.fn() };
});

const mockGet = vi.mocked(getDashboardSummary);
vi.mocked(listRuns).mockResolvedValue([]);
vi.mocked(listSuites).mockResolvedValue([]);

const summary: DashboardSummary = {
  window_days: 7,
  kpis: {
    health_score: 81.2,
    pass_rate: 50,
    total_runs: 12,
    active_connections: 3,
    avg_duration_ms: 2400,
    health_score_delta: 1.3,
    pass_rate_delta: -2.5,
    total_runs_delta_pct: 20,
    avg_duration_delta_pct: -10,
  },
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

  it('renders the avg-duration card and real period-over-period deltas (#352)', async () => {
    mockGet.mockResolvedValue(summary);
    renderPage();
    await screen.findByText('81.2');

    expect(screen.getByText('Avg. Duration')).toBeInTheDocument();
    expect(screen.getByText('2s')).toBeInTheDocument(); // 2400ms formatted
    expect(screen.getByText('+1.3 pts vs prior period')).toBeInTheDocument();
    expect(screen.getByText('-2.5 pts vs prior period')).toBeInTheDocument();
    expect(screen.getByText('+20% vs prior period')).toBeInTheDocument();
    expect(screen.getByText('-10% vs prior period')).toBeInTheDocument();
  });

  it('renders no delta badges when the prior window has no data', async () => {
    mockGet.mockResolvedValue({
      ...summary,
      kpis: {
        ...summary.kpis,
        health_score_delta: null,
        pass_rate_delta: null,
        total_runs_delta_pct: null,
        avg_duration_delta_pct: null,
      },
    });
    renderPage();
    await screen.findByText('81.2');
    expect(screen.queryByText(/vs prior period/)).not.toBeInTheDocument();
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
      kpis: {
        health_score: null,
        pass_rate: null,
        total_runs: 0,
        active_connections: 0,
        avg_duration_ms: null,
        health_score_delta: null,
        pass_rate_delta: null,
        total_runs_delta_pct: null,
        avg_duration_delta_pct: null,
      },
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
