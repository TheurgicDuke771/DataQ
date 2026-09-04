import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type CheckSuggestionsResponse,
  type LlmInvocation,
  runLlmFeature,
} from '../../src/api/llm';
import { createCheck } from '../../src/api/suites';
import { SuggestChecksDrawer } from '../../src/components/suites/SuggestChecksDrawer';

vi.mock('../../src/api/llm', () => ({ suggestChecks: vi.fn(), runLlmFeature: vi.fn() }));
vi.mock('../../src/api/suites', () => ({ createCheck: vi.fn() }));
const mockRun = vi.mocked(runLlmFeature);
const mockCreate = vi.mocked(createCheck);

function row(over: Partial<LlmInvocation<CheckSuggestionsResponse>>) {
  return {
    id: 'inv-1',
    kind: 'check_suggestion',
    status: 'succeeded' as const,
    suite_id: 's1',
    response: null,
    error: null,
    input_tokens: 1,
    output_tokens: 1,
    duration_ms: 1,
    created_at: '2026-09-03T00:00:00Z',
    finished_at: '2026-09-03T00:00:01Z',
    ...over,
  };
}

const RESULT: CheckSuggestionsResponse = {
  suggestions: [
    {
      expectation_type: 'expect_column_values_to_not_be_null',
      name: 'order_id not null',
      rationale: '0 nulls in the profile',
      config: { column: 'order_id' },
      dimension: 'completeness',
    },
    {
      expectation_type: 'monitor:freshness',
      name: 'orders arrive daily',
      rationale: 'bound pipeline lands daily',
      config: { column: 'order_ts' },
      dimension: 'timeliness',
      fail_threshold_hours: 26,
    },
  ],
  rejected: [{ expectation_type: 'expect_table_row_count_to_be_between', reason: 'not offered' }],
  coverage_warnings: [
    {
      provider: 'airflow',
      pipeline_or_dag_id: 'load_orders',
      run_env: 'qa',
      binding_env: 'dev',
      last_observed_at: '2026-09-01T00:00:00Z',
    },
  ],
};

function renderDrawer(onAdded = vi.fn()) {
  return render(
    <AntApp>
      <SuggestChecksDrawer suiteId="s1" open onClose={vi.fn()} onAdded={onAdded} />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

/** The `<li>` List item wrapping a suggestion's title. */
async function itemFor(name: string): Promise<HTMLElement> {
  const li = (await screen.findByText(name)).closest('li');
  if (!li) throw new Error(`no list item for ${name}`);
  return li;
}

describe('SuggestChecksDrawer', () => {
  it('lists validated suggestions, the rejected ones and the near-miss warning', async () => {
    mockRun.mockResolvedValue(row({ response: RESULT }));
    renderDrawer();
    await screen.findByText('order_id not null');
    expect(screen.getByText('orders arrive daily')).toBeInTheDocument();
    expect(screen.getByText(/fail ≥ 26h/)).toBeInTheDocument();
    expect(screen.getByText('1 suggestion rejected by the validator')).toBeInTheDocument();
    expect(screen.getByText('not offered')).toBeInTheDocument();
    expect(
      screen.getByText(/load_orders runs in qa but the binding is for dev/),
    ).toBeInTheDocument();
  });

  it('adds one suggestion through createCheck with the editor payload and refetches', async () => {
    mockRun.mockResolvedValue(row({ response: RESULT }));
    mockCreate.mockResolvedValue({} as never);
    const onAdded = vi.fn();
    renderDrawer(onAdded);
    const item = await itemFor('orders arrive daily');
    await userEvent.click(within(item).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(within(item).getByText('Added')).toBeInTheDocument());
    expect(mockCreate).toHaveBeenCalledWith('s1', {
      name: 'orders arrive daily',
      kind: 'freshness',
      expectation_type: 'monitor:freshness',
      config: { column: 'order_ts' },
      dimension: 'timeliness',
      fail_threshold: 26,
    });
    expect(onAdded).toHaveBeenCalledTimes(1);
  });

  it('"Add all remaining" adds every pending suggestion and then disables itself', async () => {
    mockRun.mockResolvedValue(row({ response: RESULT }));
    mockCreate.mockResolvedValue({} as never);
    renderDrawer();
    await screen.findByText('order_id not null');
    await userEvent.click(screen.getByRole('button', { name: 'Add all remaining' }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Add all remaining' })).toBeDisabled(),
    );
  });

  it('keeps the item addable when createCheck fails', async () => {
    mockRun.mockResolvedValue(row({ response: RESULT }));
    mockCreate.mockRejectedValue(new Error('column not found'));
    renderDrawer();
    const item = await itemFor('order_id not null');
    await userEvent.click(within(item).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    // jsdom never finishes the spinner's leave animation, so its aria-label stays in the
    // accessible name; match the visible label.
    await waitFor(() => expect(within(item).getByRole('button', { name: /Add$/ })).toBeEnabled());
    expect(within(item).queryByText('Added')).not.toBeInTheDocument();
  });

  it('shows an all-rejected run as the failure reason', async () => {
    mockRun.mockResolvedValue(
      row({ status: 'failed', error: 'no suggested check passed validation (2 rejected)' }),
    );
    renderDrawer();
    await screen.findByText('no suggested check passed validation (2 rejected)');
  });

  it('renders a 409 as not enabled', async () => {
    const err = new AxiosError('no LLM provider is configured');
    err.response = {
      status: 409,
      statusText: '',
      data: {},
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    };
    mockRun.mockRejectedValue(err);
    renderDrawer();
    await screen.findByText('AI suggestions are not enabled on this workspace');
  });
});
