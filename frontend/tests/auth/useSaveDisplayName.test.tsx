import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { updateMe } from '../../src/api/me';
import { MeUpdateContext } from '../../src/auth/meContext';
import { OtpSessionContext, type OtpSession } from '../../src/auth/otpSessionContext';
import { useSaveDisplayName } from '../../src/auth/useSaveDisplayName';

vi.mock('../../src/api/me', () => ({ updateMe: vi.fn() }));

const refreshedMe: MeResponse = {
  id: 'u-1',
  aad_object_id: null,
  email: 'ada@dataq.io',
  display_name: 'Ada Lovelace',
  last_seen_at: null,
  is_workspace_admin: false,
};

function wrapperFor(setMe: (me: MeResponse) => void, adopt: (me: MeResponse) => void) {
  const session: OtpSession = {
    state: { status: 'signed_out' },
    adopt,
    signOut: vi.fn(),
    retry: vi.fn(),
  };
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MeUpdateContext.Provider value={setMe}>
        <OtpSessionContext.Provider value={session}>{children}</OtpSessionContext.Provider>
      </MeUpdateContext.Provider>
    );
  };
}

describe('useSaveDisplayName', () => {
  it('PATCHes, then fans the refreshed /me out to MeContext and the otp session', async () => {
    vi.mocked(updateMe).mockResolvedValue(refreshedMe);
    const setMe = vi.fn();
    const adopt = vi.fn();
    const { result } = renderHook(() => useSaveDisplayName(), {
      wrapper: wrapperFor(setMe, adopt),
    });

    await act(async () => {
      await result.current('Ada Lovelace');
    });

    expect(updateMe).toHaveBeenCalledWith('Ada Lovelace');
    expect(setMe).toHaveBeenCalledWith(refreshedMe);
    expect(adopt).toHaveBeenCalledWith(refreshedMe);
  });

  it('propagates a failure to the caller and touches neither context', async () => {
    vi.mocked(updateMe).mockRejectedValue(new Error('network blew up'));
    const setMe = vi.fn();
    const adopt = vi.fn();
    const { result } = renderHook(() => useSaveDisplayName(), {
      wrapper: wrapperFor(setMe, adopt),
    });

    await expect(result.current('Ada Lovelace')).rejects.toThrow('network blew up');
    expect(setMe).not.toHaveBeenCalled();
    expect(adopt).not.toHaveBeenCalled();
  });
});
