import { useCallback } from 'react';

import { updateMe } from '../api/me';
import { useOtpSession } from './otpSessionContext';
import { useUpdateMe } from './useMe';

/**
 * `PATCH /me { display_name }`, then fan the fresh response out to every place
 * an identity can be rendered (#1139) — one shared hook so the profile-
 * completion prompt and the Profile page's own editor can't drift.
 *
 * Two updates, not one:
 *  - `useUpdateMe()` pushes into `MeContext` — what the Profile page and any
 *    future admin surface read.
 *  - `useOtpSession().adopt()` — in `otp` mode, `CurrentUserProvider` derives
 *    the header identity from the OTP session's OWN captured `/me`, not from
 *    `MeContext` (see `OtpCurrentUserProvider`), so without this the header
 *    name would keep showing the old value until the next full sign-in.
 *    Inert (a no-op) in every other mode — same pattern as `UserMenu`'s
 *    unconditional `useOtpSession()` call — so this hook needs no `authMode`
 *    branch of its own.
 *
 * Throws on failure; callers show the error themselves (they know whether
 * they're a modal or an inline form).
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
