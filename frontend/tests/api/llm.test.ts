import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../src/api/client';
import {
  generateRcaNarrative,
  generateSql,
  LlmInvocationTimeout,
  type LlmInvocation,
  runLlmFeature,
  suggestChecks,
  waitForLlmInvocation,
} from '../../src/api/llm';

vi.mock('../../src/api/client', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
const mockGet = vi.mocked(api.get);
const mockPost = vi.mocked(api.post);

function row(status: LlmInvocation['status'], over: Partial<LlmInvocation> = {}): LlmInvocation {
  return {
    id: 'inv-1',
    kind: 'sql_generation',
    status,
    suite_id: 's1',
    response: null,
    error: null,
    input_tokens: null,
    output_tokens: null,
    duration_ms: null,
    created_at: '2026-09-03T00:00:00Z',
    finished_at: null,
    ...over,
  };
}

afterEach(() => vi.clearAllMocks());

describe('feature calls', () => {
  it('POST the three feature endpoints with their payloads', async () => {
    mockPost.mockResolvedValue({ data: { invocation_id: 'inv-1', status: 'pending' } });
    await generateSql({ suite_id: 's1', description: 'no nulls', include_profile: true });
    expect(mockPost).toHaveBeenCalledWith('/llm/sql_generation', {
      suite_id: 's1',
      description: 'no nulls',
      include_profile: true,
    });
    await suggestChecks('s1');
    expect(mockPost).toHaveBeenCalledWith('/llm/check_suggestions', { suite_id: 's1' });
    await generateRcaNarrative('inc-1');
    expect(mockPost).toHaveBeenCalledWith('/llm/rca_narrative', { incident_id: 'inc-1' });
  });
});

describe('waitForLlmInvocation', () => {
  it('polls until the row is terminal and returns it', async () => {
    mockGet
      .mockResolvedValueOnce({ data: row('pending') })
      .mockResolvedValueOnce({ data: row('running') })
      .mockResolvedValueOnce({ data: row('succeeded', { response: { sql: 'SELECT 1' } }) });
    const result = await waitForLlmInvocation('inv-1', { pollMs: 1 });
    expect(result.status).toBe('succeeded');
    expect(mockGet).toHaveBeenCalledTimes(3);
    expect(mockGet).toHaveBeenCalledWith('/llm/invocations/inv-1');
  });

  it('returns a failed row rather than throwing — the reason is data', async () => {
    mockGet.mockResolvedValueOnce({ data: row('failed', { error: 'columns could not be read' }) });
    const result = await waitForLlmInvocation('inv-1', { pollMs: 1 });
    expect(result.status).toBe('failed');
    expect(result.error).toBe('columns could not be read');
  });

  it('gives up after the timeout while the row is still running', async () => {
    mockGet.mockResolvedValue({ data: row('running') });
    await expect(waitForLlmInvocation('inv-1', { pollMs: 1, timeoutMs: 0 })).rejects.toBeInstanceOf(
      LlmInvocationTimeout,
    );
  });

  it('runLlmFeature queues then waits on the returned invocation id', async () => {
    mockPost.mockResolvedValueOnce({ data: { invocation_id: 'inv-9', status: 'pending' } });
    mockGet.mockResolvedValueOnce({ data: row('succeeded', { id: 'inv-9' }) });
    const result = await runLlmFeature(() => suggestChecks('s1'), { pollMs: 1 });
    expect(result.id).toBe('inv-9');
    expect(mockGet).toHaveBeenCalledWith('/llm/invocations/inv-9');
  });
});
