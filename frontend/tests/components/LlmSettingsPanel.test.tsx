import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getLlmConfig, testLlmConfig, updateLlmConfig, type LlmConfig } from '../../src/api/llm';
import { LlmSettingsPanel } from '../../src/components/admin/LlmSettingsPanel';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/llm', () => ({
  getLlmConfig: vi.fn(),
  updateLlmConfig: vi.fn(),
  testLlmConfig: vi.fn(),
}));

const mockGet = vi.mocked(getLlmConfig);
const mockUpdate = vi.mocked(updateLlmConfig);
const mockTest = vi.mocked(testLlmConfig);

const UNCONFIGURED: LlmConfig = {
  configured: false,
  provider: null,
  base_url: null,
  model: null,
  structured_output: null,
  enabled: false,
  has_credential: false,
  updated_at: null,
};

const CONFIGURED: LlmConfig = {
  configured: true,
  provider: 'anthropic',
  base_url: null,
  model: 'claude-sonnet-4-5-20250929',
  structured_output: 'native',
  enabled: true,
  has_credential: true,
  updated_at: '2026-08-29T10:00:00Z',
};

function renderPanel() {
  return render(
    <AntApp>
      <LlmSettingsPanel />
    </AntApp>,
  );
}

/** A 422 `{error: {code, message}}` envelope, matching the FastAPI error shape
 *  `apiFieldError` parses. */
function configInvalid(message: string): AxiosError {
  const err = new AxiosError(message);
  err.response = {
    status: 422,
    statusText: 'Unprocessable Entity',
    data: { error: { code: 'llm_config_invalid', message } },
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return err;
}

afterEach(() => vi.clearAllMocks());

describe('LlmSettingsPanel', () => {
  it('renders the unconfigured state with empty defaults', async () => {
    mockGet.mockResolvedValue(UNCONFIGURED);
    renderPanel();

    expect(await screen.findByText('Not configured')).toBeInTheDocument();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
    expect(screen.getByText('No credential')).toBeInTheDocument();
    expect(screen.getByLabelText('Model')).toHaveValue('');
    expect(screen.getByLabelText('API key')).toHaveValue('');
  });

  it('renders the configured state, including the credential-present badge', async () => {
    mockGet.mockResolvedValue(CONFIGURED);
    renderPanel();

    expect(await screen.findByText('Configured')).toBeInTheDocument();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('Credential set')).toBeInTheDocument();
    expect(screen.getByLabelText('Model')).toHaveValue('claude-sonnet-4-5-20250929');
  });

  it('never displays the stored key — the API key field stays blank and password-masked', async () => {
    mockGet.mockResolvedValue(CONFIGURED);
    renderPanel();

    const field = await screen.findByLabelText('API key');
    expect(field).toHaveValue('');
    expect(field).toHaveAttribute('type', 'password');
    expect(field).toHaveAttribute('placeholder', 'Stored — leave blank to keep');
  });

  it('omits api_key on save when left blank (keeps the stored key)', async () => {
    mockGet.mockResolvedValue(CONFIGURED);
    mockUpdate.mockResolvedValue(CONFIGURED);
    renderPanel();
    await screen.findByText('Configured');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0]).toMatchObject({ api_key: undefined });
  });

  it('sends a typed api_key on save and clears the field after', async () => {
    mockGet.mockResolvedValue(CONFIGURED);
    mockUpdate.mockResolvedValue(CONFIGURED);
    renderPanel();
    await screen.findByText('Configured');

    await userEvent.type(screen.getByLabelText('API key'), 'sk-new-key');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate.mock.calls[0][0]).toMatchObject({ api_key: 'sk-new-key' });
    await waitFor(() => expect(screen.getByLabelText('API key')).toHaveValue(''));
  });

  it('Test — success renders ok + latency distinctly from the configured/enabled tags', async () => {
    mockGet.mockResolvedValue(CONFIGURED);
    mockTest.mockResolvedValue({
      ok: true,
      model: 'claude-sonnet-4-5-20250929',
      latency_ms: 842,
      reply_chars: 120,
    });
    renderPanel();
    await screen.findByText('Configured');

    await userEvent.click(screen.getByRole('button', { name: 'Test' }));

    expect(
      await screen.findByText(/OK — claude-sonnet-4-5-20250929 · 842ms · 120 chars/),
    ).toBeInTheDocument();
  });

  it.each([
    ['llm_provider_unavailable', 'Provider unavailable'],
    ['llm_provider_error', 'Provider error'],
    ['llm_credential_missing', 'Credential missing'],
    ['secret_store_unavailable', 'Secret store unavailable'],
  ] as const)('Test — %s renders as %s, distinct from "not configured"', async (code, label) => {
    mockGet.mockResolvedValue(CONFIGURED);
    mockTest.mockResolvedValue({ ok: false, error_code: code, error: 'boom detail' });
    renderPanel();
    await screen.findByText('Configured');

    await userEvent.click(screen.getByRole('button', { name: 'Test' }));

    expect(await screen.findByText(`${label}: boom detail`)).toBeInTheDocument();
    // An outage must never render as the unconfigured state.
    expect(screen.queryByText('Not configured')).not.toBeInTheDocument();
  });

  it('Test — a 422 destination-move refusal lands on the API key field, not a toast', async () => {
    mockGet.mockResolvedValue(CONFIGURED);
    mockTest.mockRejectedValue(
      configInvalid('Re-enter the API key to change provider or base_url'),
    );
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Configured');

    await selectOption(user, 'OpenAI-compatible endpoint');
    await user.click(screen.getByRole('button', { name: 'Test' }));

    expect(
      await screen.findByText('Re-enter the API key to change provider or base_url'),
    ).toBeInTheDocument();
    // No success/failure badge — the refusal is a field error, not a probe result.
    expect(screen.queryByText(/OK —/)).not.toBeInTheDocument();
  });

  it('Save — a 422 destination-move refusal lands on the API key field, not a toast', async () => {
    mockGet.mockResolvedValue(CONFIGURED);
    mockUpdate.mockRejectedValue(
      configInvalid('Re-enter the API key to change provider or base_url'),
    );
    renderPanel();
    await screen.findByText('Configured');

    await userEvent.clear(screen.getByLabelText('Base URL'));
    await userEvent.type(screen.getByLabelText('Base URL'), 'https://new-endpoint.example/v1');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(
      await screen.findByText('Re-enter the API key to change provider or base_url'),
    ).toBeInTheDocument();
  });
});
