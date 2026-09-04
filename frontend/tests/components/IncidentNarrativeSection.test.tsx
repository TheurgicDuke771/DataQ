import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getIncidentNarrative } from '../../src/api/incidents';
import { type LlmInvocation, type RcaNarrative, runLlmFeature } from '../../src/api/llm';
import { IncidentNarrativeSection } from '../../src/components/assets/IncidentNarrativeSection';

vi.mock('../../src/api/incidents', () => ({ getIncidentNarrative: vi.fn() }));
vi.mock('../../src/api/llm', () => ({ generateRcaNarrative: vi.fn(), runLlmFeature: vi.fn() }));
const mockGet = vi.mocked(getIncidentNarrative);
const mockRun = vi.mocked(runLlmFeature);

const NARRATIVE: RcaNarrative = {
  summary: '18% of statuses fall outside the allowed set.',
  ranked_hypotheses: [
    { cause: 'Bad upstream values', confidence: 'high', evidence_refs: ['failing_result'] },
  ],
  blind_spots: ['no upstream pipeline run is linked'],
  suggested_next_checks: ['Check the loader'],
};

const NONE = { narrative: null, invocation_id: null, generated_at: null, withheld_reason: null };

function row(over: Partial<LlmInvocation<RcaNarrative>>): LlmInvocation<RcaNarrative> {
  return {
    id: 'inv-1',
    kind: 'rca_narrative',
    status: 'succeeded',
    suite_id: 's1',
    response: null,
    error: null,
    input_tokens: 1,
    output_tokens: 1,
    duration_ms: 1,
    created_at: '2026-09-03T00:00:00Z',
    finished_at: '2026-09-03T00:00:05Z',
    ...over,
  };
}

afterEach(() => vi.clearAllMocks());

describe('IncidentNarrativeSection', () => {
  it('renders the stored narrative with hypotheses, refs and blind spots', async () => {
    mockGet.mockResolvedValue({
      narrative: NARRATIVE,
      invocation_id: 'inv-0',
      generated_at: '2026-09-03T00:00:00Z',
      withheld_reason: null,
    });
    render(<IncidentNarrativeSection incidentId="inc-1" />);
    await screen.findByText('18% of statuses fall outside the allowed set.');
    expect(screen.getByText(/Bad upstream values/)).toBeInTheDocument();
    expect(screen.getByText('high')).toBeInTheDocument();
    expect(screen.getByText('failing_result')).toBeInTheDocument();
    expect(screen.getByText('no upstream pipeline run is linked')).toBeInTheDocument();
    expect(screen.getByText('Check the loader')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument();
  });

  it('says a narrative is withheld rather than absent', async () => {
    mockGet.mockResolvedValue({
      narrative: null,
      invocation_id: 'inv-0',
      generated_at: '2026-09-03T00:00:00Z',
      withheld_reason: 'generated from another user’s view',
    });
    render(<IncidentNarrativeSection incidentId="inc-1" />);
    await screen.findByText(/A narrative exists but is not shown to you/);
    expect(screen.getByRole('button', { name: 'Explain this failure' })).toBeInTheDocument();
  });

  it('generates on demand and renders the fresh narrative', async () => {
    mockGet.mockResolvedValue(NONE);
    mockRun.mockResolvedValue(row({ response: NARRATIVE }));
    render(<IncidentNarrativeSection incidentId="inc-1" />);
    await screen.findByText(/None generated yet/);
    await userEvent.click(screen.getByRole('button', { name: 'Explain this failure' }));
    await screen.findByText('18% of statuses fall outside the allowed set.');
    // The stored read is refreshed after a successful generation.
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it("shows a failed invocation's reason", async () => {
    mockGet.mockResolvedValue(NONE);
    mockRun.mockResolvedValue(
      row({ status: 'failed', error: 'this incident has no evidence card to narrate' }),
    );
    render(<IncidentNarrativeSection incidentId="inc-1" />);
    await screen.findByText(/None generated yet/);
    await userEvent.click(screen.getByRole('button', { name: 'Explain this failure' }));
    await screen.findByText('this incident has no evidence card to narrate');
  });
});
