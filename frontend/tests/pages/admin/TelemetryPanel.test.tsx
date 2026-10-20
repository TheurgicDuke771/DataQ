import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  getTelemetrySettings,
  putTelemetrySettings,
  type TelemetrySettings,
} from '../../../src/api/telemetry';
import { TelemetryPanel } from '../../../src/pages/admin/TelemetryPanel';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/telemetry', () => ({
  getTelemetrySettings: vi.fn(),
  putTelemetrySettings: vi.fn(),
  resetTelemetryStatus: vi.fn(),
}));

const mockGet = vi.mocked(getTelemetrySettings);
const mockPut = vi.mocked(putTelemetrySettings);

const OFF: TelemetrySettings = {
  enabled: false,
  stored: false,
  sink: 'file',
  sink_ready: true,
  events: [
    { name: 'page_viewed', attributes: { route: ['dashboard', 'suites'] } },
    { name: 'run_completed', attributes: { checks: 'integer' } },
  ],
  updated_by: null,
  updated_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TelemetryPanel', () => {
  it('is off by default and turns on through the API', async () => {
    mockGet.mockResolvedValue(OFF);
    mockPut.mockResolvedValue({
      ...OFF,
      enabled: true,
      stored: true,
      updated_by: 'ada@example.com',
      updated_at: '2026-10-20T10:00:00Z',
    });
    renderSubPage(<TelemetryPanel />);
    const toggle = await screen.findByRole('switch', { name: 'Usage telemetry' });
    expect(toggle).not.toBeChecked();
    expect(screen.getByText('Off')).toBeInTheDocument();
    fireEvent.click(toggle);
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith(true));
    expect(await screen.findByText('Recording')).toBeInTheDocument();
    expect(screen.getByText(/by ada@example.com/)).toBeInTheDocument();
  });

  it('lists every event and its allowed values inline', async () => {
    mockGet.mockResolvedValue(OFF);
    renderSubPage(<TelemetryPanel />);
    fireEvent.click(await screen.findByText('What is recorded (2 events)'));
    expect(await screen.findByText('page_viewed')).toBeInTheDocument();
    expect(screen.getByText(/dashboard, suites/)).toBeInTheDocument();
    expect(screen.getByText(/a count/)).toBeInTheDocument();
  });

  it('says plainly when the opt-in is stored but no sink records it', async () => {
    mockGet.mockResolvedValue({ ...OFF, stored: true, sink: 'off' });
    renderSubPage(<TelemetryPanel />);
    expect(await screen.findByText('Opted in, but nothing is recorded')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Usage telemetry' })).toBeChecked();
    expect(screen.getByText('Off')).toBeInTheDocument();
  });

  it('says when a configured sink could not be started', async () => {
    mockGet.mockResolvedValue({ ...OFF, stored: true, sink_ready: false });
    renderSubPage(<TelemetryPanel />);
    expect(
      await screen.findByText('Opted in, but the sink could not be started'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Opted in, but nothing is recorded')).not.toBeInTheDocument();
  });

  it('shows the load failure rather than a default state', async () => {
    mockGet.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<TelemetryPanel />);
    expect(
      await screen.findByText('Could not load the usage-telemetry settings'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  });
});
