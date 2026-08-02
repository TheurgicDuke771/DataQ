import { useContext } from 'react';

import type { MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { MeContext, MeUpdateContext } from './meContext';

/** The shared `/me` fetch state (identity + `is_workspace_admin`). */
export function useMe(): AsyncState<MeResponse> {
  return useContext(MeContext);
}

/** Push a fresh `/me` body (e.g. the result of a `PATCH /me`) into the shared
 *  context, so every reader — the Profile page, the completion prompt, a
 *  future admin-list — sees it without a second fetch (#1139). */
export function useUpdateMe(): (me: MeResponse) => void {
  return useContext(MeUpdateContext);
}

/** Convenience: true only once `/me` has resolved and the user is a workspace admin. */
export function useIsWorkspaceAdmin(): boolean {
  const me = useMe();
  return me.status === 'ok' && me.data.is_workspace_admin;
}
