import { useCallback } from 'react';

import { updateMe } from '../api/me';
import { useOtpSession } from './otpSessionContext';
import { useUpdateMe } from './useMe';

/**
 * `PATCH /me { display_name }`, then fan the fresh response out to every place an identity can be
 * rendered (#1139).
 */
export function useSaveDisplayName(): (displayName: string) => Promise<void> {
  const setMe = useUpdateMe();
  const { adopt } = useOtpSession();

  return useCallback(
    async (displayName: string) => {
      const me = await updateMe(displayName);
      setMe(me);
      adopt(me);
    },
    [setMe, adopt],
  );
}
