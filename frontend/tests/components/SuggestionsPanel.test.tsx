import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type Suggestion,
  acceptSuggestion,
  listSuggestions,
  rejectSuggestion,
} from '../../src/api/suggestions';
import { SuggestionsPanel } from '../../src/components/suites/SuggestionsPanel';

vi.mock('../../src/api/suggestions', () => ({
  listSuggestions: vi.fn(),
  acceptSuggestion: vi.fn(),
  rejectSuggestion: vi.fn(),
}));

const mockList = vi.mocked(listSuggestions);
const mockAccept = vi.mocked(acceptSuggestion);
const mockReject = vi.mocked(rejectSuggestion);

const suggestion = (over: Partial<Suggestion> = {}): Suggestion => ({
  id: 'g1',
  suite_id: 's1',
  source: 'profile',
  status: 'pending',
  name: 'order_id is never null',
  expectation_type: 'expect_column_values_to_not_be_null',
  config: { column: 'order_id' },
  rationale: 'No nulls in 500 rows.',
  check_id: null,
  decided_by: null,
  decided_at: null,
  created_at: '2026-09-30T00:00:00Z',
  ...over,
});

const renderPanel = (canDecide = true, onAccepted = vi.fn()) =>
  render(
    <App>
      <SuggestionsPanel suiteId="s1" canDecide={canDecide} onAccepted={onAccepted} />
    </App>,
  );

describe('SuggestionsPanel (ADR 0047)', () => {
  beforeEach(() => {
    mockList.mockReset();
    mockAccept.mockReset();
    mockReject.mockReset();
  });

  it('renders nothing when the queue is empty', async () => {
    mockList.mockResolvedValue([]);
    renderPanel();
    await waitFor(() => expect(mockList).toHaveBeenCalledWith('s1'));
    expect(screen.queryByTestId('suggestions-panel')).toBeNull();
  });

  it('accepting adds the check and refreshes', async () => {
    mockList.mockResolvedValueOnce([suggestion()]).mockResolvedValueOnce([]);
    mockAccept.mockResolvedValue(suggestion({ status: 'accepted', check_id: 'c1' }));
    const onAccepted = vi.fn();
    renderPanel(true, onAccepted);
    expect(await screen.findByText('No nulls in 500 rows.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Accept' }));
    expect(mockAccept).toHaveBeenCalledWith('g1');
    await waitFor(() => expect(onAccepted).toHaveBeenCalled());
  });

  it('rejecting does not touch the checks', async () => {
    mockList.mockResolvedValueOnce([suggestion()]).mockResolvedValueOnce([]);
    mockReject.mockResolvedValue(suggestion({ status: 'rejected' }));
    const onAccepted = vi.fn();
    renderPanel(true, onAccepted);
    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    expect(mockReject).toHaveBeenCalledWith('g1');
    expect(onAccepted).not.toHaveBeenCalled();
  });

  it('a viewer sees the queue without the decisions', async () => {
    mockList.mockResolvedValue([suggestion()]);
    renderPanel(false);
    expect(await screen.findByText('order_id is never null')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull();
  });
});
