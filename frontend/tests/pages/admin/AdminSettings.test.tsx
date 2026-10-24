import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getWorkspaceConfig, testAuthEmail, type WorkspaceConfig } from '../../../src/api/admin';
import { getLlmConfig, type LlmConfig } from '../../../src/api/llm';
import { listChannels } from '../../../src/api/notificationChannels';
import { authMethodLabel } from '../../../src/auth/config';
import { AdminSettings } from '../../../src/pages/admin/AdminSettings';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  testAuthEmail: vi.fn(),
  getWorkspaceConfig: vi.fn(),
  getPrivacySettings: vi.fn(() => new Promise(() => {})),
  putPrivacySettings: vi.fn(),
  getScoringWeights: vi.fn(() => new Promise(() => {})),
  putScoringWeights: vi.fn(),
  resetScoringWeights: vi.fn(),
}));
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
const mockWorkspaceConfig = vi.mocked(getWorkspaceConfig);
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
  connection_id: null,
  updated_at: null,
};

const WORKSPACE_CONFIG: WorkspaceConfig = {
  retention: [
    {
      variable: 'SAMPLE_FAILURES_RETENTION_DAYS',
      value: 30,
      unit: 'days',
      detail: 'Failing-row samples are removed; the result row stays.',
    },
    {
      variable: 'AUDIT_RETENTION_DAYS',
      value: 365,
      unit: 'days',
      detail: 'Audit events older than this are deleted.',
    },
  ],
  signup_allowlists: [
    {
      variable: 'OIDC_ALLOWED_EMAILS',
      kind: 'emails',
      values: ['ada@example.com'],
      active: false,
      grants: 'sign_in',
    },
    {
      variable: 'AUTH_OTP_ALLOWED_DOMAINS',
      kind: 'domains',
      values: ['example.com'],
      active: true,
      grants: 'sign_in',
    },
    {
      variable: 'WORKSPACE_ADMIN_EMAILS',
      kind: 'emails',
      values: [],
      active: true,
      grants: 'admin_role',
    },
  ],
  oidc_default_role: 'member',
  otp_default_role: 'viewer',
  dev_bypass_active: false,
};

beforeEach(() => {
  mockLlmConfig.mockResolvedValue(LLM_CONFIG);
  mockChannels.mockResolvedValue([]);
  mockWorkspaceConfig.mockResolvedValue(WORKSPACE_CONFIG);
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

  it('keeps the secret-store notice and points each destructive action at where it lives', () => {
    renderSubPage(<AdminSettings />);
    expect(
      screen.getByText('Credentials live in the secret store, never the database'),
    ).toBeInTheDocument();
    expect(screen.getByText('Destructive actions')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Suites (admin)' })).toHaveAttribute(
      'href',
      '/admin/suites',
    );
    expect(screen.getByRole('link', { name: 'Compliance' })).toHaveAttribute(
      'href',
      '/admin/compliance',
    );
    // No labelled placeholder survives the IA pickup.
    expect(
      screen.queryByText(/post-v1|follow-up|aren't available yet|not available yet/i),
    ).toBeNull();
  });

  it('says workspace-wide alert defaults come from the deployment, with the reference linked', () => {
    renderSubPage(<AdminSettings />);
    expect(screen.getByText('TEAMS_WEBHOOK_SECRET_NAME')).toBeInTheDocument();
    expect(screen.getByText('SLACK_WEBHOOK_SECRET_NAME')).toBeInTheDocument();
    const guide = screen.getByRole('link', { name: 'notifications guide' });
    expect(guide).toHaveAttribute(
      'href',
      expect.stringMatching(/guides\/notifications\/#channels$/),
    );
    expect(guide).toHaveAttribute('target', '_blank');
  });

  describe('deployment configuration readout', () => {
    it('lists each sign-in allowlist with its values and a worded status', async () => {
      renderSubPage(<AdminSettings />);
      const table = await screen.findByRole('table', { name: 'Sign-in allowlists' });
      expect(table).toBeInTheDocument();
      expect(screen.getByText('OIDC_ALLOWED_EMAILS')).toBeInTheDocument();
      expect(screen.getByText('ada@example.com')).toBeInTheDocument();
      // An allowlist for a mode the deployment does not use must not read as the gate.
      expect(screen.getByText('Not read — sign-in mode not configured')).toBeInTheDocument();
      expect(screen.getByText('Read at sign-in')).toBeInTheDocument();
      expect(screen.getByText('Grants the Admin role')).toBeInTheDocument();
      expect(screen.getByText('No addresses')).toBeInTheDocument();
      expect(screen.getByText('Viewer')).toBeInTheDocument(); // the OTP first-sign-in role
      // Sign-in access and Destructive actions both point at Members.
      for (const link of screen.getAllByRole('link', { name: 'Members' })) {
        expect(link).toHaveAttribute('href', '/admin/members');
      }
      expect(screen.getByRole('link', { name: 'admin guide' })).toHaveAttribute(
        'href',
        expect.stringMatching(/guides\/admin\/#seed-and-emergency-access$/),
      );
      expect(mockWorkspaceConfig).toHaveBeenCalledTimes(1); // both cards share one fetch
    });

    it('shows each retention clock with its variable and what the sweep removes', async () => {
      renderSubPage(<AdminSettings />);
      expect(await screen.findByText('30 days')).toBeInTheDocument();
      expect(screen.getByText('SAMPLE_FAILURES_RETENTION_DAYS')).toBeInTheDocument();
      expect(screen.getByText('365 days')).toBeInTheDocument();
      expect(screen.getByText('Audit events older than this are deleted.')).toBeInTheDocument();
    });

    it('calls out the developer bypass when it is the active sign-in', async () => {
      mockWorkspaceConfig.mockResolvedValue({ ...WORKSPACE_CONFIG, dev_bypass_active: true });
      renderSubPage(<AdminSettings />);
      expect(await screen.findByText('Developer bypass is the active sign-in')).toBeInTheDocument();
    });

    it('shows the failure, not an empty card, when the readout cannot load', async () => {
      mockWorkspaceConfig.mockRejectedValue(new Error('upstream down'));
      renderSubPage(<AdminSettings />);
      expect(
        await screen.findByText('Could not load the sign-in configuration'),
      ).toBeInTheDocument();
      expect(screen.getByText('Could not load the retention configuration')).toBeInTheDocument();
    });
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
