import { Form } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { generateSql, type LlmInvocation, runLlmFeature } from '../../src/api/llm';
import { SqlGeneratePanel } from '../../src/components/checks/SqlGeneratePanel';
import { CUSTOM_SQL_QUERY_KEY } from '../../src/components/checks/customSql';

vi.mock('../../src/api/llm', () => ({
  generateSql: vi.fn(),
  runLlmFeature: vi.fn(),
}));
const mockRun = vi.mocked(runLlmFeature);
const mockGenerate = vi.mocked(generateSql);

function row(over: Partial<LlmInvocation>): LlmInvocation {
  return {
    id: 'inv-1',
    kind: 'sql_generation',
    status: 'succeeded',
    suite_id: 's1',
    response: null,
    error: null,
    input_tokens: 10,
    output_tokens: 5,
    duration_ms: 100,
    created_at: '2026-09-03T00:00:00Z',
    finished_at: '2026-09-03T00:00:01Z',
    ...over,
  };
}

function Harness({ onForm }: { onForm: (f: ReturnType<typeof Form.useForm>[0]) => void }) {
  const [form] = Form.useForm();
  onForm(form);
  return (
    <Form form={form}>
      <SqlGeneratePanel suiteId="s1" form={form} />
    </Form>
  );
}

function http(status: number, message: string): AxiosError {
  const err = new AxiosError(message);
  err.response = {
    status,
    statusText: '',
    data: { error: { code: 'x', message } },
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return err;
}

afterEach(() => vi.clearAllMocks());

describe('SqlGeneratePanel', () => {
  it('is disabled until a description is typed, then writes the SQL into the form field', async () => {
    let form!: ReturnType<typeof Form.useForm>[0];
    render(<Harness onForm={(f) => (form = f)} />);
    const button = screen.getByRole('button', { name: 'Generate SQL' });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Rule description'), 'no negative quantity');
    expect(button).toBeEnabled();

    mockRun.mockImplementation(async (start) => {
      await start();
      return row({
        response: { sql: 'SELECT * FROM T WHERE QTY < 0', explanation: 'rows breaking the rule' },
      });
    });
    mockGenerate.mockResolvedValue({ invocation_id: 'inv-1', status: 'pending' });
    await userEvent.click(button);

    await screen.findByTestId('sql-generate-result');
    expect(screen.getByText('rows breaking the rule')).toBeInTheDocument();
    expect(form.getFieldValue(['config', CUSTOM_SQL_QUERY_KEY])).toBe(
      'SELECT * FROM T WHERE QTY < 0',
    );
    expect(mockGenerate).toHaveBeenCalledWith({
      suite_id: 's1',
      description: 'no negative quantity',
      include_profile: false,
    });
  });

  it('sends include_profile when the box is ticked', async () => {
    render(<Harness onForm={() => undefined} />);
    await userEvent.type(screen.getByLabelText('Rule description'), 'x');
    await userEvent.click(screen.getByRole('checkbox', { name: /Include column profile/ }));
    mockRun.mockImplementation(async (start) => {
      await start();
      return row({ response: { sql: 'SELECT 1', explanation: '' } });
    });
    mockGenerate.mockResolvedValue({ invocation_id: 'inv-1', status: 'pending' });
    await userEvent.click(screen.getByRole('button', { name: 'Generate SQL' }));
    await waitFor(() =>
      expect(mockGenerate).toHaveBeenCalledWith(expect.objectContaining({ include_profile: true })),
    );
  });

  it("shows a failed invocation's own reason and leaves the form untouched", async () => {
    let form!: ReturnType<typeof Form.useForm>[0];
    render(<Harness onForm={(f) => (form = f)} />);
    await userEvent.type(screen.getByLabelText('Rule description'), 'x');
    mockRun.mockResolvedValue(
      row({ status: 'failed', error: "the table's columns could not be read" }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Generate SQL' }));
    await screen.findByText("the table's columns could not be read");
    expect(form.getFieldValue(['config', CUSTOM_SQL_QUERY_KEY])).toBeUndefined();
  });

  it('renders a 409 as "not enabled" rather than as a failure', async () => {
    render(<Harness onForm={() => undefined} />);
    await userEvent.type(screen.getByLabelText('Rule description'), 'x');
    mockRun.mockRejectedValue(http(409, 'no LLM provider is configured'));
    await userEvent.click(screen.getByRole('button', { name: 'Generate SQL' }));
    await screen.findByText('AI generation is not enabled on this workspace');
    expect(screen.getByText('no LLM provider is configured')).toBeInTheDocument();
  });

  it('refuses an over-long description client-side', async () => {
    render(<Harness onForm={() => undefined} />);
    const area = screen.getByLabelText('Rule description');
    await userEvent.click(area);
    await userEvent.paste('x'.repeat(2001));
    expect(screen.getByRole('button', { name: 'Generate SQL' })).toBeDisabled();
    expect(screen.getByText(/Keep the description under 2000 characters/)).toBeInTheDocument();
  });
});
