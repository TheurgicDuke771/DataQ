import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getDeploymentPosture, listAuditEvents } from '../../../src/api/admin';
import { AdminCompliance } from '../../../src/pages/admin/AdminCompliance';
import { AUDIT_PAGE_1, AUDIT_PAGE_2, DEPLOYMENT_POSTURE, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  listAuditEvents: vi.fn(),
  getDeploymentPosture: vi.fn(),
}));

const mockAuditEvents = vi.mocked(listAuditEvents);
const mockDeploymentPosture = vi.mocked(getDeploymentPosture);

beforeEach(() => {
  mockAuditEvents.mockResolvedValue(AUDIT_PAGE_1);
  mockDeploymentPosture.mockResolvedValue(DEPLOYMENT_POSTURE);
});
afterEach(() => vi.clearAllMocks());

describe('AdminCompliance', () => {
  it('renders the audit log with its honesty fields and the deployment posture table', async () => {
    renderSubPage(<AdminCompliance />);
    expect(await screen.findByText('check.update')).toBeInTheDocument();
    expect(
      screen.getByText(/Retained 365 days .* events older than that have been swept/),
    ).toBeInTheDocument();
    expect(screen.getByTitle('2')).toBeInTheDocument(); // real total via antd's pager

    expect(screen.getByText('us-east-1')).toBeInTheDocument();
    expect(screen.getByText('alert_delivery')).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
    expect(screen.getByText('off')).toBeInTheDocument(); // zero-sample mode, off by default
  });

  it('Next actually refetches the next page — not just a local page-number bump', async () => {
    // Regression: page state bumped without calling reload(), so the fetch never fired.
    mockAuditEvents.mockResolvedValueOnce(AUDIT_PAGE_1).mockResolvedValueOnce(AUDIT_PAGE_2);
    const user = userEvent.setup();
    const { container } = renderSubPage(<AdminCompliance />);
    expect(await screen.findByText('check.update')).toBeInTheDocument();

    const next = container.querySelector('.ant-pagination-next button');
    expect(next).not.toBeNull();
    await user.click(next as HTMLButtonElement);

    expect(await screen.findByText('run_results.read')).toBeInTheDocument();
    expect(screen.queryByText('check.update')).not.toBeInTheDocument();
    expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({ offset: 25 });
  });

  it('Search applies the pending filters and resets to the first page', async () => {
    const user = userEvent.setup();
    renderSubPage(<AdminCompliance />);
    await screen.findByText('check.update');

    await user.type(screen.getByPlaceholderText('e.g. suite'), 'suite');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    });
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({ entity_type: 'suite', offset: 0 });
  });

  it('Next does not apply an unsubmitted filter edit to the page fetch', async () => {
    // Regression: a collapsed filter state let Next read a typed-but-unsubmitted edit.
    mockAuditEvents.mockResolvedValueOnce(AUDIT_PAGE_1).mockResolvedValueOnce(AUDIT_PAGE_2);
    const user = userEvent.setup();
    const { container } = renderSubPage(<AdminCompliance />);
    await screen.findByText('check.update');

    await user.type(screen.getByPlaceholderText('e.g. suite'), 'suite'); // not submitted
    const next = container.querySelector('.ant-pagination-next button');
    await user.click(next as HTMLButtonElement);

    await waitFor(() => {
      expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    });
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({
      entity_type: undefined,
      offset: 25,
    });
  });

  it('trims whitespace from the entity type filter before sending it', async () => {
    const user = userEvent.setup();
    renderSubPage(<AdminCompliance />);
    await screen.findByText('check.update');

    await user.type(screen.getByPlaceholderText('e.g. suite'), '  suite  ');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    });
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({ entity_type: 'suite' });
  });

  it('names the disabled sweep instead of implying a normal retention window', async () => {
    mockAuditEvents.mockResolvedValue({ ...AUDIT_PAGE_1, retention_days: 0, retained_since: null });
    renderSubPage(<AdminCompliance />);
    expect(
      await screen.findByText(/Retention sweep is disabled .* the log is unbounded/),
    ).toBeInTheDocument();
  });

  it('warns inline when the audit log fails to load', async () => {
    mockAuditEvents.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<AdminCompliance />);
    expect(await screen.findByText('Failed to load the audit log')).toBeInTheDocument();
  });

  it('warns inline when the deployment posture fails to load', async () => {
    mockDeploymentPosture.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<AdminCompliance />);
    expect(await screen.findByText('Failed to load deployment posture')).toBeInTheDocument();
  });
});
