import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setAssetClassificationDismissed } from '../../src/api/assets';
import { DetectedMaskingPanel } from '../../src/components/assets/DetectedMaskingPanel';

vi.mock('../../src/api/assets', () => ({ setAssetClassificationDismissed: vi.fn() }));
const mockSet = vi.mocked(setAssetClassificationDismissed);

const masked = {
  column: 'contact',
  detector: 'email',
  effect: 'masks' as const,
  first_detected_at: '2026-10-19T08:00:00Z',
  last_detected_at: '2026-10-20T08:00:00Z',
  dismissed: false,
  dismissed_at: null,
};
const dismissed = {
  ...masked,
  column: 'payload',
  detector: 'encoded',
  effect: 'dismissed' as const,
  dismissed: true,
  dismissed_at: '2026-10-20T09:00:00Z',
};
const overridden = {
  ...masked,
  column: 'ssn',
  detector: 'phone',
  effect: 'overridden_by_tag' as const,
};

const renderPanel = (props: Partial<Parameters<typeof DetectedMaskingPanel>[0]> = {}) =>
  render(
    <AntApp>
      <DetectedMaskingPanel
        assetId="a1"
        entries={[masked, dismissed, overridden]}
        refreshedAt="2026-10-20T08:00:00Z"
        canDismiss={false}
        onChanged={() => {}}
        {...props}
      />
    </AntApp>,
  );

beforeEach(() => {
  mockSet.mockReset();
});

describe('DetectedMaskingPanel (ADR 0049)', () => {
  it('renders nothing for an older API or an asset never profiled', () => {
    // The antd App wrapper is an (empty) div of its own, so assert on the content.
    expect(renderPanel({ entries: undefined }).container).toHaveTextContent('');
    expect(renderPanel({ entries: [], refreshedAt: null }).container).toHaveTextContent('');
    expect(screen.queryByTestId('detected-masking-panel')).not.toBeInTheDocument();
  });

  it('says so when the answer is unknown rather than showing nothing', () => {
    renderPanel({ entries: null });
    expect(screen.getByTestId('detected-masking-unknown')).toHaveTextContent(
      'reading the detections failed',
    );
  });

  it('distinguishes "looked and found none" from "never looked"', () => {
    renderPanel({ entries: [] });
    expect(screen.getByTestId('detected-masking-none')).toBeInTheDocument();
    expect(screen.getByText(/Last full profile/)).toBeInTheDocument();
  });

  it('names each column, what it looked like, and whether it still masks', () => {
    renderPanel();
    expect(screen.getByText('Detected personal data')).toBeInTheDocument();
    expect(screen.getByText('contact')).toBeInTheDocument();
    expect(screen.getByText('email addresses')).toBeInTheDocument();
    expect(screen.getByTestId('detection-contact-masked')).toHaveTextContent('masked');
    expect(screen.getByTestId('detection-payload-dismissed')).toHaveTextContent('dismissed');
    // The tag never overclaims: a column with its own warehouse tag is not "masked" by this.
    expect(screen.getByTestId('detection-ssn-overridden')).toHaveTextContent(
      'warehouse tag decides',
    );
    // An unknown detector renders as its label rather than breaking the row.
    expect(screen.getByText('phone')).toBeInTheDocument();
    // The caveat an empty-looking table must carry.
    expect(screen.getByText(/A column not listed is not thereby clean/)).toBeInTheDocument();
  });

  it('offers no dismiss or restore control to a non-admin', () => {
    renderPanel();
    expect(screen.queryByRole('button', { name: /^Dismiss/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Restore masking/ })).not.toBeInTheDocument();
  });

  it('lets an admin dismiss after confirming, and restore directly', async () => {
    const onChanged = vi.fn();
    mockSet.mockResolvedValue({
      ...masked,
      effect: 'dismissed',
      dismissed: true,
      dismissed_at: '2026-10-20T10:00:00Z',
    });
    renderPanel({ canDismiss: true, onChanged });

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss: contact' }));
    expect(mockSet).not.toHaveBeenCalled(); // behind a confirmation: it loosens masking
    await userEvent.click(await screen.findByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(mockSet).toHaveBeenCalledWith('a1', 'contact', true));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole('button', { name: 'Restore masking: payload' }));
    await waitFor(() => expect(mockSet).toHaveBeenCalledWith('a1', 'payload', false));
  }, 15000);
});
