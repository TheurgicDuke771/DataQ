import { App as AntApp } from 'antd';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { useMe } from '../../src/auth/useMe';
import { useSaveDisplayName } from '../../src/auth/useSaveDisplayName';
import { ProfileCompletionPrompt } from '../../src/components/profile/ProfileCompletionPrompt';
import type { AsyncState } from '../../src/hooks/useAsyncData';

// Own file so `authMode` can be pinned to something other than 'otp' — a
// hoisted vi.mock is fixed for the whole file (see ProfileCompletionPrompt.test.tsx).
vi.mock('../../src/auth/config', () => ({ authMode: 'real' }));
vi.mock('../../src/auth/useMe', () => ({ useMe: vi.fn() }));
vi.mock('../../src/auth/useSaveDisplayName', () => ({ useSaveDisplayName: vi.fn() }));

const noNameEvenSoUnderReal: MeResponse = {
  id: 'u-1',
  aad_object_id: 'oid-1',
  email: 'ada@dataq.io',
  display_name: null,
  last_seen_at: null,
  is_workspace_admin: false,
};

describe('ProfileCompletionPrompt outside otp mode', () => {
  it('never renders — a null display_name in real/dev_bypass mode is not this flow', () => {
    vi.mocked(useMe).mockReturnValue({
      status: 'ok',
      data: noNameEvenSoUnderReal,
    } satisfies AsyncState<MeResponse>);
    vi.mocked(useSaveDisplayName).mockReturnValue(vi.fn());

    render(
      <AntApp>
        <ProfileCompletionPrompt />
      </AntApp>,
    );
    expect(screen.queryByText('Welcome to DataQ')).not.toBeInTheDocument();
  });
});
