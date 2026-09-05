import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DataSubjectErasure, DataSubjectExport } from '../../../src/api/admin';
import { eraseDataSubject, exportDataSubject } from '../../../src/api/admin';
import { DataSubjectCard } from '../../../src/pages/admin/DataSubjectCard';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  exportDataSubject: vi.fn(),
  eraseDataSubject: vi.fn(),
}));

const mockExport = vi.mocked(exportDataSubject);
const mockErase = vi.mocked(eraseDataSubject);

const SUBJECT = 'alice@example.com';

const EXPORT_WITH_MATCHES: DataSubjectExport = {
  column: 'email',
  value: SUBJECT,
  match_count: 2,
  incident_match_count: 1,
  matches: [
    {
      result_id: 'r1',
      run_id: 'run1',
      suite_id: 's1',
      suite_name: 'Orders',
      check_id: 'c1',
      check_name: 'no nulls',
      created_at: '2026-08-20T10:00:00Z',
      matched_in: ['sample_failures'],
      sample_failures: { partial_unexpected_list: [{ email: SUBJECT }] },
      observed_value: null,
    },
  ],
  incident_matches: [
    {
      incident_id: 'i1',
      suite_id: 's1',
      suite_name: 'Orders',
      check_id: 'c1',
      check_name: 'no nulls',
      status: 'open',
      created_at: '2026-08-20T11:00:00Z',
      observed_value: { unparsed_value: SUBJECT },
    },
  ],
};

const ERASURE_COMPLETE: DataSubjectErasure = {
  column: 'email',
  value: SUBJECT,
  matched_count: 2,
  erased_count: 2,
  matched_result_count: 1,
  erased_result_count: 1,
  matched_incident_count: 1,
  erased_incident_count: 1,
};

function fillSubject(column = 'email', value = SUBJECT) {
  fireEvent.change(screen.getByPlaceholderText('e.g. email'), { target: { value: column } });
  fireEvent.change(screen.getByPlaceholderText('e.g. alice@example.com'), { target: { value } });
}

beforeEach(() => {
  mockExport.mockResolvedValue(EXPORT_WITH_MATCHES);
  mockErase.mockResolvedValue(ERASURE_COMPLETE);
});
afterEach(() => vi.clearAllMocks());

describe('DataSubjectCard', () => {
  it('fires nothing on mount and keeps both actions disabled until a subject is named', async () => {
    renderSubPage(<DataSubjectCard />);
    expect(await screen.findByText('Export data')).toBeInTheDocument();
    expect(mockExport).not.toHaveBeenCalled();
    expect(mockErase).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Export data/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Erase subject/ })).toBeDisabled();
  });

  it('exports the (column, value) pair and renders the receipt', async () => {
    renderSubPage(<DataSubjectCard />);
    fillSubject();
    fireEvent.click(screen.getByRole('button', { name: /Export data/ }));

    expect(await screen.findByText('Export receipt')).toBeInTheDocument();
    expect(mockExport).toHaveBeenCalledWith('email', SUBJECT);
    expect(
      screen.getByText('2 match(es): 1 in results, 1 in stored incident evidence'),
    ).toBeInTheDocument();
    // The receipt body itself is on screen, not merely offered as a download.
    expect(screen.getByText(/"suite_name": "Orders"/)).toBeInTheDocument();
  });

  it('says an empty export proves nothing about the warehouse', async () => {
    mockExport.mockResolvedValue({
      column: 'email',
      value: SUBJECT,
      match_count: 0,
      incident_match_count: 0,
      matches: [],
      incident_matches: [],
    });
    renderSubPage(<DataSubjectCard />);
    fillSubject();
    fireEvent.click(screen.getByRole('button', { name: /Export data/ }));

    expect(await screen.findByText('No captured data matches this subject')).toBeInTheDocument();
    expect(screen.getByText(/your warehouse is unaffected and unexamined/)).toBeInTheDocument();
  });

  it('surfaces a failed request with its request ID and does not show a receipt', async () => {
    mockExport.mockRejectedValue(new Error('Request failed with status code 503'));
    renderSubPage(<DataSubjectCard />);
    fillSubject();
    fireEvent.click(screen.getByRole('button', { name: /Export data/ }));

    expect(await screen.findByText('The request failed')).toBeInTheDocument();
    expect(screen.getByText(/status code 503/)).toBeInTheDocument();
    expect(screen.queryByText('Export receipt')).not.toBeInTheDocument();
  });

  it('keeps erase behind a typed confirmation that must match the value exactly', async () => {
    renderSubPage(<DataSubjectCard />);
    fillSubject();
    fireEvent.click(screen.getByRole('button', { name: /Erase subject/ }));

    const confirm = await screen.findByRole('button', { name: /Erase permanently/ });
    expect(confirm).toBeDisabled();
    expect(mockErase).not.toHaveBeenCalled();

    const typed = screen.getByLabelText('Type the subject value to confirm');
    // A near-miss stays refused — this is the guard against erasing someone else.
    fireEvent.change(typed, { target: { value: 'alice@example.co' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(typed, { target: { value: ` ${SUBJECT}` } });
    expect(confirm).toBeDisabled();

    fireEvent.change(typed, { target: { value: SUBJECT } });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);

    expect(await screen.findByText('Erasure receipt')).toBeInTheDocument();
    expect(mockErase).toHaveBeenCalledWith('email', SUBJECT);
    expect(screen.getByText('Erased 2 of 2 match(es)')).toBeInTheDocument();
  });

  it('cancelling erases nothing and drops the typed confirmation', async () => {
    renderSubPage(<DataSubjectCard />);
    fillSubject();
    fireEvent.click(screen.getByRole('button', { name: /Erase subject/ }));
    fireEvent.change(await screen.findByLabelText('Type the subject value to confirm'), {
      target: { value: SUBJECT },
    });
    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));
    expect(mockErase).not.toHaveBeenCalled();

    // Re-opening must start from an empty box — a confirmation typed for one
    // subject must never carry over and arm the erase for the next.
    fireEvent.click(screen.getByRole('button', { name: /Erase subject/ }));
    await waitFor(() =>
      expect(screen.getByLabelText('Type the subject value to confirm')).toHaveValue(''),
    );
    expect(screen.getByRole('button', { name: /Erase permanently/ })).toBeDisabled();
    expect(mockErase).not.toHaveBeenCalled();
  });

  it('reports a partial erasure as a warning rather than as done', async () => {
    mockErase.mockResolvedValue({ ...ERASURE_COMPLETE, erased_count: 1, erased_incident_count: 0 });
    renderSubPage(<DataSubjectCard />);
    fillSubject();
    fireEvent.click(screen.getByRole('button', { name: /Erase subject/ }));
    const typed = await screen.findByLabelText('Type the subject value to confirm');
    fireEvent.change(typed, { target: { value: SUBJECT } });
    fireEvent.click(screen.getByRole('button', { name: /Erase permanently/ }));

    expect(await screen.findByText('Erased 1 of 2 match(es)')).toBeInTheDocument();
    expect(screen.getByText(/Some matches had no scrub path/)).toBeInTheDocument();
  });
});
