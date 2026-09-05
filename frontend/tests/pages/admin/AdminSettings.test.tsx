import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { testAuthEmail } from '../../../src/api/admin';
import { getLlmConfig, type LlmConfig } from '../../../src/api/llm';
import { listChannels } from '../../../src/api/notificationChannels';
import { authMethodLabel } from '../../../src/auth/config';
import { AdminSettings } from '../../../src/pages/admin/AdminSettings';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({ testAuthEmail: vi.fn() }));
vi.mock('../../../src/api/llm', () => ({
  getLlmConfig: vi.fn(),
  updateLlmConfig: vi.fn(),
  testLlmConfig: vi.fn(),
}));
vi.mock('../../../src/api/notificationChannels', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../src/api/notificationChannels')>()),
  listChannels: vi.fn(),
  createChannel: vi.fn(),
  updateChannel: vi.fn(),
  deleteChannel: vi.fn(),
}));

const mockTestAuthEmail = vi.mocked(testAuthEmail);
const mockLlmConfig = vi.mocked(getLlmConfig);
const mockChannels = vi.mocked(listChannels);

const LLM_CONFIG: LlmConfig = {
  configured: false,
  provider: null,
  base_url: null,
  model: null,
  structured_output: null,
  enabled: false,
  has_credential: false,
  updated_at: null,
};

beforeEach(() => {
  mockLlmConfig.mockResolvedValue(LLM_CONFIG);
  mockChannels.mockResolvedValue([]);
});
afterEach(() => vi.clearAllMocks());

describe('AdminSettings', () => {
  it('gathers the workspace facts, the channels panel and the LLM provider panel', async () => {
    renderSubPage(<AdminSettings />);
    expect(screen.getByText('Single tenant')).toBeInTheDocument();
    // Provider-neutral auth label derived from the runtime authMode.
    expect(screen.getByText(authMethodLabel)).toBeInTheDocument();
    expect(await screen.findByText('LLM provider')).toBeInTheDocument();
    expect(mockLlmConfig).toHaveBeenCalled();
  });

  it('keeps the secret-store and danger-zone notices', () => {
    renderSubPage(<AdminSettings />);
    expect(
      screen.getByText('Credentials live in the secret store, never the database'),
    ).toBeInTheDocument();
    expect(screen.getByText('No destructive workspace actions in v1')).toBeInTheDocument();
  });

  it('sends a real test email to the caller and toasts success', async () => {
    mockTestAuthEmail.mockResolvedValue({ status: 'ok', to: 'admin@dataq.io' });
    renderSubPage(<AdminSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test email' }));

    expect(await screen.findByText(/Test email sent to admin@dataq\.io/)).toBeInTheDocument();
    expect(mockTestAuthEmail).toHaveBeenCalledTimes(1);
  });

  it('surfaces the failing SMTP stage when the pre-flight test fails', async () => {
    // Shape the axios response interceptor already produces (client.ts folds the
    // error-envelope's message into `err.message`).
    mockTestAuthEmail.mockRejectedValue(
      new Error(
        "SMTP pre-flight failed at the 'auth' stage — see the server log for the underlying error type.",
      ),
    );
    renderSubPage(<AdminSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test email' }));

    expect(await screen.findByText(/'auth' stage/)).toBeInTheDocument();
    await waitFor(() => expect(mockTestAuthEmail).toHaveBeenCalledTimes(1));
  });

  it('carries the request ID on a failure, so the server log can be searched by it', async () => {
    mockTestAuthEmail.mockRejectedValue(
      Object.assign(new Error('SMTP pre-flight failed'), {
        isAxiosError: true,
        response: { status: 502, headers: { 'x-request-id': 'req-abc123' } },
      }),
    );
    renderSubPage(<AdminSettings />);

    fireEvent.click(screen.getByRole('button', { name: /Send test email/ }));

    expect(await screen.findByText('SMTP pre-flight test failed')).toBeInTheDocument();
    expect(screen.getByText('req-abc123')).toBeInTheDocument();
  });
});
