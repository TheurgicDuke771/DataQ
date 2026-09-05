import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AuditChainStatus } from '../../../src/api/admin';
import { verifyAuditChain } from '../../../src/api/admin';
import { AuditChainCard } from '../../../src/pages/admin/AuditChainCard';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({ verifyAuditChain: vi.fn() }));

const mockVerify = vi.mocked(verifyAuditChain);

const CHAIN_OK: AuditChainStatus = {
  status: 'ok',
  verified_count: 412,
  unverifiable_legacy_count: 0,
  chain_head_hash: 'abcdef0123456789abcdef0123456789',
  anchor_mode: 'none',
  first_break: null,
};

// Braces are load-bearing: `mockResolvedValue` returns the mock, and a function
// returned from beforeEach is run by vitest as a teardown hook — which would
// call the mock an extra time and leave an unhandled rejection behind it.
beforeEach(() => {
  mockVerify.mockResolvedValue(CHAIN_OK);
});
afterEach(() => vi.clearAllMocks());

describe('AuditChainCard', () => {
  it('does not verify on mount — the check reads the whole hashed set', async () => {
    renderSubPage(<AuditChainCard />);
    expect(await screen.findByText('Not verified this session')).toBeInTheDocument();
    expect(mockVerify).not.toHaveBeenCalled();
    expect(screen.getByText(/may take a while on a large log/)).toBeInTheDocument();
  });

  it('reports an intact chain with its counts, and says an unanchored chain is only internally consistent', async () => {
    renderSubPage(<AuditChainCard />);
    fireEvent.click(screen.getByRole('button', { name: /Verify now/ }));

    expect(await screen.findByText('Intact')).toBeInTheDocument();
    expect(mockVerify).toHaveBeenCalledTimes(1);
    expect(screen.getByText('412')).toBeInTheDocument();
    expect(screen.getByText('Not configured')).toBeInTheDocument();
    expect(screen.getByText(/could also rewrite the hashes/)).toBeInTheDocument();
  });

  it('reports a break with the blamed event rather than a bare failure', async () => {
    mockVerify.mockResolvedValue({
      status: 'broken',
      verified_count: 9,
      unverifiable_legacy_count: 3,
      chain_head_hash: 'deadbeef',
      anchor_mode: 'webhook',
      first_break: {
        event_id: 'ev-42',
        occurred_at: '2026-08-20T10:00:00Z',
        expected_prev_hash: 'aaa',
        actual_prev_hash: 'bbb',
      },
    });
    renderSubPage(<AuditChainCard />);
    fireEvent.click(screen.getByRole('button', { name: /Verify now/ }));

    expect(await screen.findByText('The audit chain is broken')).toBeInTheDocument();
    expect(screen.getByText('Broken')).toBeInTheDocument();
    expect(screen.getByText('ev-42')).toBeInTheDocument();
    expect(screen.queryByText('Intact')).not.toBeInTheDocument();
    // Legacy rows are reported, never folded into the verified count.
    expect(screen.getByText(/3 event\(s\) written before the chain shipped/)).toBeInTheDocument();
  });

  it('an empty chain is not reported as intact', async () => {
    mockVerify.mockResolvedValue({
      status: 'empty',
      verified_count: 0,
      unverifiable_legacy_count: 0,
      chain_head_hash: null,
      anchor_mode: 'none',
      first_break: null,
    });
    renderSubPage(<AuditChainCard />);
    fireEvent.click(screen.getByRole('button', { name: /Verify now/ }));

    expect(await screen.findByText('No hashed events yet')).toBeInTheDocument();
    expect(screen.getByText('Nothing to verify')).toBeInTheDocument();
    expect(screen.queryByText('Intact')).not.toBeInTheDocument();
  });

  it('a failed verification is never rendered as an intact chain', async () => {
    mockVerify.mockRejectedValue(new Error('Request failed with status code 500'));
    renderSubPage(<AuditChainCard />);
    fireEvent.click(screen.getByRole('button', { name: /Verify now/ }));

    expect(await screen.findByText('Could not verify the audit chain')).toBeInTheDocument();
    expect(screen.getByText(/says nothing about whether the chain is intact/)).toBeInTheDocument();
    expect(screen.getByText('Not verified — the check failed')).toBeInTheDocument();
    expect(screen.queryByText('Intact')).not.toBeInTheDocument();
    expect(screen.queryByText('Events in chain')).not.toBeInTheDocument();
  });

  it('re-verifying replaces the previous result', async () => {
    renderSubPage(<AuditChainCard />);
    fireEvent.click(screen.getByRole('button', { name: /Verify now/ }));
    expect(await screen.findByText('Intact')).toBeInTheDocument();

    mockVerify.mockRejectedValueOnce(new Error('boom'));
    fireEvent.click(screen.getByRole('button', { name: /Verify now/ }));
    await waitFor(() => expect(screen.queryByText('Intact')).not.toBeInTheDocument());
    expect(mockVerify).toHaveBeenCalledTimes(2);
  });
});
